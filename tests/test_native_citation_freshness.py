"""Native citation eligibility over synthetic epochs; never fresh model inference."""

from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
from jsonschema import Draft202012Validator
import pytest

from agent.evidence_policy import _scope_views, assess_dependencies
from agent.shadow_context import unpack_view
from agent.shadow_provider import GroqShadowDriver
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from eval.shadow_preflight import RecordingPolicy
from eval.shadow_replan import ReplanningInvestigation
from tests.test_shadow_replan import Clock, epoch

EDGES = (("checkoutservice", "paymentservice"), ("checkoutservice", "currencyservice"))
pytestmark = pytest.mark.usefixtures("mock_harmony_tokenizer")


def context(layout="time-indexed-v1", protocol="bounded-coverage-v1"):
    first = epoch(statuses={"paymentservice": "DOWN", "checkoutservice": "DEGRADED"}, edges=EDGES)
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, edges=EDGES, previous=first)
    policy = RecordingPolicy()
    run = ReplanningInvestigation(first, clock=Clock(), profile="two-epoch-v1",
        evidence_layout=layout, investigation_protocol=protocol)
    run.run_epoch(first, policy)
    run.run_epoch(second, policy)
    messages = deepcopy(policy.prompts[-1])
    view = unpack_view(json.loads(messages[-1]["content"]))
    _, views, _, _ = _scope_views(view["initial"], view["turns"])
    old_id = next(row.payload["evidence_id"] for row in first.records
        if row.kind == "component" and row.payload["service_id"] == "paymentservice")
    return messages, view, views, old_id


def driver():
    return GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)


def citation_domain(schema, field="supporting_evidence"):
    return set(schema["$defs"]["HypothesisProposal"]["properties"][field]["items"]["enum"])


def decision(service, identifier):
    return {"decision": {"action": "propose", "hypothesis": {"service_id": service,
        "condition": "readiness_failure", "supporting_evidence": [identifier],
        "contradicting_evidence": [], "unresolved_alternatives": []}}}


def repack(messages, view):
    messages = deepcopy(messages)
    messages[-1]["content"] = json.dumps(view)
    return messages


@pytest.mark.parametrize("layout", ["legacy-v1", "time-indexed-v1"])
@pytest.mark.parametrize("protocol", ["model-directed-v1", "bounded-coverage-v1"])
def test_native_proposal_citations_are_all_latest_scoped_components(layout, protocol):
    messages, view, views, old_id = context(layout, protocol)
    schema = driver()._schema(messages)
    latest = {identifier for observed in views.values() for identifier in observed["ids"]}
    assert citation_domain(schema) == citation_domain(schema, "contradicting_evidence") == latest
    assert old_id not in latest and view["initial"]["symptom"]["evidence_id"] not in latest
    assert {observed["status"] for observed in views.values()} == {"HEALTHY", "DEGRADED"}


def test_native_schema_rejects_the_superseded_failure_even_inside_the_freshness_window():
    messages, view, _, old_id = context()
    response = decision("paymentservice", old_id)
    assert not Draft202012Validator(driver()._schema(messages)).is_valid(response)
    assert assess_dependencies(view["initial"], view["turns"], response["decision"]["hypothesis"])["verdict"] == "blocked"


@pytest.mark.parametrize("service", ["paymentservice", "currencyservice"])
def test_schema_does_not_select_the_correct_target_or_hide_healthy_controls(service):
    messages, view, views, _ = context()
    schema = driver()._schema(messages)
    response = decision(service, next(iter(views[service]["ids"])))
    assert Draft202012Validator(schema).is_valid(response)
    assert assess_dependencies(view["initial"], view["turns"], response["decision"]["hypothesis"])["reason"] == "condition_not_supported"
    properties = schema["$defs"]["HypothesisProposal"]["properties"]
    assert set(properties["service_id"]["enum"]) == set(REGISTRY.by_name)
    assert set(properties["condition"]["enum"]) == {"readiness_failure", "resource_pressure"}


def test_a_current_supported_proposal_still_needs_the_unchanged_local_screen():
    messages, view, views, _ = context()
    response = decision("checkoutservice", next(iter(views["checkoutservice"]["ids"])))
    assert Draft202012Validator(driver()._schema(messages)).is_valid(response)
    review = assess_dependencies(view["initial"], view["turns"], response["decision"]["hypothesis"])
    assert review["verdict"] == "candidate_supported" and not review["execution_authorized"]


def test_read_only_turn_has_no_unusable_empty_citation_enum():
    messages, view, _, _ = context()
    view["turns"] = []
    view["decision_protocol"]["allowed_actions"] = ["read", "abstain"]
    schema = driver()._schema(repack(messages, view))
    assert "HypothesisProposal" not in schema["$defs"]
    assert schema["$defs"]["ReadDecision"]["properties"]["requests"]["maxItems"] == 3
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("fault", ["missing", "unknown", "conflict", "unavailable"])
def test_inconsistent_proposal_action_cannot_build_a_native_schema(fault):
    messages, view, _, _ = context()
    view["decision_protocol"]["allowed_actions"] = ["propose", "abstain"]
    if fault == "missing":
        view["turns"] = []
    else:
        for turn in view["turns"]:
            for feedback in turn.get("feedback", []):
                if (feedback.get("request") or {}).get("service_id") != "currencyservice":
                    continue
                if fault == "conflict":
                    feedback["data"]["summary"]["latest_conflict"] = True
                elif fault == "unavailable":
                    feedback["outcome"] = "unavailable"
                else:
                    for sample in feedback["data"]["samples"]:
                        sample.update(status="UNKNOWN", quality="unknown")
    with pytest.raises(ValueError, match="complete known scope"):
        driver()._schema(repack(messages, view))


def test_provisional_schema_keeps_its_existing_non_dependency_reference_policy():
    messages, view, _, old_id = context()
    view["initial"].pop("screening_contract")
    assert old_id in citation_domain(driver()._schema(repack(messages, view)))


def test_real_sdk_receives_latest_ids_without_replacing_the_mocked_model_choice():
    from langchain_openai import ChatOpenAI

    messages, view, views, old_id = context()
    response = decision("paymentservice", old_id)
    captured = []

    def handle(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        captured.append(schema)
        latest = {identifier for observed in views.values() for identifier in observed["ids"]}
        assert citation_domain(schema) == latest and not Draft202012Validator(schema).is_valid(response)
        return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
            "model": "openai/gpt-oss-120b", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(response)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ChatOpenAI(model="openai/gpt-oss-120b", api_key="test", max_retries=0, cache=False,
            base_url="https://api.groq.com/openai/v1", http_client=client)
        reply = GroqShadowDriver(model, structured=True).complete(messages, max_output_tokens=768, timeout_seconds=1)
    assert len(captured) == 1 and json.loads(reply.content) == response
    assert assess_dependencies(view["initial"], view["turns"], response["decision"]["hypothesis"])["verdict"] == "blocked"
