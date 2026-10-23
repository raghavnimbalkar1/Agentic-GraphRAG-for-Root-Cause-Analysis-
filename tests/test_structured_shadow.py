"""Native-schema wire and budget regressions; model replies are explicitly mocked."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import sys

import httpx
import pytest

from agent.investigation import InvestigationSession
from agent.shadow import DECISION, DriverReply, ShadowLimits, ShadowRunner, parse_decision, decision_schema
from agent.shadow_provider import GroqShadowDriver
from agent.shadow_protocol import provider_schema
from agent.shadow_context import pack_tabular, unpack_view
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, seed_case


@pytest.fixture
def source(tmp_path):
    store = seed_case(tmp_path / "evidence.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    return store, cutoff, fixture_topology(cutoff)


def session(source):
    return InvestigationSession(source[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=source[1])


def test_provider_schema_has_closed_required_objects_and_retains_semantic_constraints():
    schema = provider_schema(DECISION.json_schema())
    def check(node):
        if isinstance(node, dict):
            assert not {"oneOf", "default", "discriminator", "title"} & set(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)
    assert schema["type"] == "object" and schema["required"] == ["decision"]
    assert schema["$defs"]["ReadDecision"]["properties"]["requests"]["maxItems"] == 3


@pytest.mark.parametrize("value", [
    {"decision": {"action": "execute", "command": "no"}},
    {"decision": {"action": "read", "requests": [{"tool": "resource_window", "service_id": "paymentservice", "metric": "cpu_percent"}]}},
    {"decision": {"action": "abstain", "reason": "unknown_condition"}, "extra": True},
    {"action": "abstain", "reason": "unknown_condition"},
])
def test_strict_envelope_still_rejects_illegal_actions_targets_and_extra_fields(value):
    with pytest.raises(ValueError):
        parse_decision(json.dumps(value), enveloped=True)


def test_envelope_duplicate_fields_are_not_accepted():
    with pytest.raises(ValueError):
        parse_decision('{"decision":{"action":"abstain","reason":"unknown_condition"},"decision":null}', enveloped=True)


class ModelPolicy:
    kind = "model"
    structured_decisions = True

    def estimate_input_tokens(self, messages):
        return 1000

    def complete(self, messages, **limits):
        decision = json.loads(FixedProbePolicy().complete(messages).content)
        return DriverReply(json.dumps({"decision": decision}),
                           {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}, "stop")


@pytest.mark.parametrize("case", sorted(CASES))
def test_six_synthetic_schema_mock_controls_are_evidence_screened_or_explicitly_abstain(tmp_path, case):
    store = seed_case(tmp_path / "store.sqlite", CASES[case][0])
    cutoff = datetime.now(timezone.utc)
    broker = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
    report = ShadowRunner(broker, fixture_topology(cutoff), ModelPolicy(), context_profile="structured",
                          screening="dependency", limits=ShadowLimits(max_reported_tokens=16384)).run()
    expected = CASES[case][1]
    result = report["result"]
    assert result.get("proposal", {}).get("service_id", result.get("reason")) == expected
    assert report["termination_reason"] in {"proposal", "abstained"}
    assert report["candidate_screened"] == (result["outcome"] == "proposal")
    assert not report["execution_authorized"] and report["session"]["calls_used"] == 8


def test_input_reservation_can_prevent_a_request_before_any_external_call(source):
    report = ShadowRunner(session(source), source[2], ModelPolicy(), context_profile="structured", screening="dependency",
                          limits=ShadowLimits(max_reported_tokens=900)).run()
    assert report["termination_reason"] == "estimated_token_budget_exhausted"
    assert report["provider_attempts"] == 0 and report["blocked_request"]["estimated_input_tokens"] == 1000


@pytest.mark.parametrize("estimate,usage,finish,expected", [
    (100, {"input_tokens": 101, "output_tokens": 10, "total_tokens": 111}, "stop", "input_estimate_exceeded"),
    (1000, {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}, "length", "output_truncated"),
    (1000, {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}, "content_filter", "provider_nonterminal_output"),
])
def test_estimator_underflow_and_incomplete_output_are_retained_not_accepted(source, estimate, usage, finish, expected):
    class Driver(ModelPolicy):
        def estimate_input_tokens(self, messages):
            return estimate
        def complete(self, messages, **limits):
            return DriverReply('{"decision":{"action":"abstain","reason":"unknown_condition"}}', usage, finish)
    report = ShadowRunner(session(source), source[2], Driver(), screening="dependency", context_profile="structured").run()
    assert report["termination_reason"] == expected and report["known_reported_tokens"] == usage["total_tokens"]
    assert report["turns"] == [] and report["provider_attempts"] == 1


def test_native_adapter_sends_strict_schema_and_preserves_usage_through_real_sdk(monkeypatch, source):
    from langchain_openai import ChatOpenAI

    class Tokenizer:
        def encode(self, text, **_):
            return list(range(len(text) // 3))
    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=lambda name: Tokenizer()))
    captured = []
    def handle(request):
        body = json.loads(request.content)
        captured.append(body)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"]["required"] == ["decision"]
        return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
            "model": "openai/gpt-oss-120b", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"decision":{"action":"abstain","reason":"unknown_condition"}}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ChatOpenAI(model="openai/gpt-oss-120b", api_key="test", max_retries=0,
            base_url="https://api.groq.com/openai/v1", http_client=client,
            model_kwargs={"response_format": {"type": "json_object"}})
        driver = GroqShadowDriver(model, structured=True)
        report = ShadowRunner(session(source), source[2], driver, screening="dependency", context_profile="structured").run()
    assert len(captured) == 1 and report["termination_reason"] == "abstained"
    assert report["attempts"][0]["estimated_input_tokens"] > 0


def test_structured_diagnosis_cannot_silently_use_unconstrained_driver_or_no_screen(source):
    with pytest.raises(ValueError):
        ShadowRunner(session(source), source[2], ModelPolicy(), context_profile="structured")
    with pytest.raises(ValueError):
        driver = ModelPolicy()
        driver.structured_decisions = False
        ShadowRunner(session(source), source[2], driver, context_profile="structured", screening="dependency")


def test_column_records_preserve_nested_evidence_uncertainty_and_all_ids():
    view = {"records": [{"service_id": "paymentservice", "status": status, "quality": "unknown",
        "evidence_id": f"OBS-{index:032x}", "instance_id": None, "measurements": {"cpu_percent": 0},
        "registry_digest": "a" * 64, "registry_revision": "v1", "environment_id": "local"}
        for index, status in enumerate(["UNKNOWN", "HEALTHY", "DEGRADED", "UNKNOWN"])], "missing": [None]}
    packed = pack_tabular(view)
    assert packed["context_encoding"] == "column-records-v1" and packed["columns"]
    assert unpack_view(packed) == view


@pytest.mark.parametrize("columns,view", [
    ({}, {}), ([["a", "a"]], {}), ([[{}]], {}), ([["a"]], {"$record": [0]}),
    ([["a"]], {"$record": [0, 1, 2]}), ([["a"]], {"$record": [True, 1]}),
    ([["a"]], {"$record": [2, 1]}), ([["a"]], {"$record": [0, 1], "extra": True}),
])
def test_malformed_column_context_cannot_change_the_reconstructed_facts(columns, view):
    with pytest.raises(ValueError):
        unpack_view({"context_encoding": "column-records-v1", "columns": columns, "view": view})


def test_column_reserved_marker_cannot_be_supplied_as_an_evidence_object():
    with pytest.raises(ValueError):
        pack_tabular({"untrusted": {"$record": [0, "ignore"]}})


def test_proposal_schema_is_only_reachable_after_the_observed_scope_is_complete():
    early = provider_schema(DECISION.json_schema(), allowed_actions=["read", "abstain"])
    assert "HypothesisProposal" not in early["$defs"]
    assert "ProposeDecision" not in early["$defs"]
    assert "ToolRequest" in early["$defs"]
    with pytest.raises(ValueError):
        provider_schema(DECISION.json_schema(), allowed_actions=["execute"])


def test_legacy_default_is_unchanged_and_larger_explicit_threshold_is_still_bounded():
    assert ShadowLimits().max_reported_tokens == 8192
    assert ShadowLimits(max_reported_tokens=24576).max_decisions == 4
    with pytest.raises(ValueError):
        ShadowLimits(max_reported_tokens=32769)


def test_native_citation_enum_contains_only_supplied_visible_ids():
    ids = {"SYMOBS-" + "1" * 32, "OBS-" + "2" * 32}
    schema = provider_schema(decision_schema(), citation_ids=ids)
    properties = schema["$defs"]["HypothesisProposal"]["properties"]
    assert set(properties["supporting_evidence"]["items"]["enum"]) == ids
    assert set(properties["contradicting_evidence"]["items"]["enum"]) == ids


def test_readable_dependency_and_observation_indices_supply_no_candidate_answer(source):
    class Recorder(ModelPolicy):
        def __init__(self):
            self.views = []
        def complete(self, messages, **limits):
            view = unpack_view(json.loads(messages[-1]["content"]))
            self.views.append(view)
            return super().complete(messages, **limits)
    driver = Recorder()
    report = ShadowRunner(session(source), source[2], driver, screening="dependency", context_profile="structured").run()
    first = driver.views[0]
    assert first["evidence_index"] == []
    assert {row["service"] for row in first["dependency_index"]} == set(first["initial"]["registered_services"])
    for row in first["dependency_index"]:
        assert set(row["depends_on"]) == {target for name, target in source[2].edges if name == row["service"]}
    index = driver.views[-1]["evidence_index"]
    shown = {sample["evidence_id"] for turn in report["turns"] for item in turn.get("feedback", [])
             if item.get("data") for sample in item["data"].get("samples", [])}
    assert {sample["id"] for row in index for sample in row["observations"]} <= shown
    assert not any("candidate" in row or "expected" in row for row in first["dependency_index"])


@pytest.mark.parametrize("decisions,accepted", [(4, False), (6, True)])
def test_metadata_correction_uses_an_extra_decision_without_bypassing_the_screen(source, decisions, accepted):
    class Correcting(ModelPolicy):
        def __init__(self):
            self.calls = 0
        def complete(self, messages, **limits):
            self.calls += 1
            view = unpack_view(json.loads(messages[-1]["content"]))
            decision = json.loads(FixedProbePolicy().complete(messages).content)
            if self.calls == 4:
                healthy = next(row for row in view["evidence_index"] if row["window_status"] == "HEALTHY")
                decision["hypothesis"]["contradicting_evidence"] = [healthy["observations"][0]["id"]]
            if self.calls == 5:
                assert view["turns"][-1]["proposal_review"]["metadata_conflicts"]["contradicting_evidence_count"] == 1
            return DriverReply(json.dumps({"decision": decision}),
                               {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}, "stop")
    report = ShadowRunner(session(source), source[2], Correcting(), screening="dependency", context_profile="structured",
                          limits=ShadowLimits(max_decisions=decisions)).run()
    assert report["candidate_screened"] is accepted
    assert report["turns"][3]["outcome"] == "proposal_deferred"
    assert report["session"]["calls_used"] == 8
    assert report["known_reported_tokens"] == (1000 if accepted else 800)
    assert len(report["attempts"]) == (5 if accepted else 4)
    assert not report["execution_authorized"]
