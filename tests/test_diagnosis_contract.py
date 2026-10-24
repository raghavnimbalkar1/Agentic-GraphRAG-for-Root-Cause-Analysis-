"""Diagnosis-contract regressions; recorded mistakes are not real-model passes."""

import json
from types import SimpleNamespace

import pytest

from agent.shadow import DriverReply, decision_schema
from agent.shadow_context import unpack_view
from agent.shadow_provider import GroqShadowDriver
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_preflight import RecordingPolicy
from eval.shadow_replan import ReplanningInvestigation
from tests.test_replanning_profile import full_epochs
from tests.test_shadow_replan import Clock


@pytest.mark.parametrize("layout", ["legacy-v1", "time-indexed-v1"])
def test_final_native_target_has_screened_role_without_hiding_any_service(layout):
    first, second = full_epochs()
    policy = RecordingPolicy()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout=layout, clock=Clock())
    for inputs in (first, second):
        controller.run_epoch(inputs, policy)
    driver = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)
    for messages in (policy.prompts[3], policy.prompts[7]):
        view = unpack_view(json.loads(messages[-1]["content"]))
        schema = driver._schema(messages)
        target = schema["$defs"]["HypothesisProposal"]["properties"]["service_id"]
        assert "not the workflow anchor" in target["description"]
        assert target["enum"] == view["initial"]["registered_services"]
        assert schema["$defs"]["HypothesisProposal"]["properties"]["condition"]["enum"] == [
            "readiness_failure", "resource_pressure"]
        assert view["decision_protocol"]["contract"] == "dependency-candidate-v2"
        system = messages[0]["content"]
        assert "A depends on B" in system and "A cannot explain B" in system
        assert "paymentservice" not in system and "checkoutservice" not in system


def test_provisional_schema_does_not_inherit_dependency_only_semantics():
    schema = decision_schema()
    fields = schema["$defs"]["HypothesisProposal"]["properties"]
    assert "description" not in fields["service_id"]
    assert "request_failure" in fields["condition"]["enum"]


def test_unknown_schema_screening_mode_is_rejected():
    with pytest.raises(ValueError):
        decision_schema(screening="automatic")


@pytest.mark.parametrize("layout", ["legacy-v1", "time-indexed-v1"])
def test_recorded_wrong_anchor_choice_stays_a_failure_after_contract_change(layout):
    class WrongAnchor(FixedProbePolicy):
        kind = "scripted"

        def complete(self, messages, **kwargs):
            decision = json.loads(super().complete(messages, **kwargs).content)
            if decision["action"] == "propose":
                view = unpack_view(json.loads(messages[-1]["content"]))
                sample = next(feedback["data"]["samples"][-1] for turn in view["turns"]
                    for feedback in turn.get("feedback", [])
                    if (feedback.get("request") or {}).get("service_id") == "checkoutservice")
                decision["hypothesis"].update(service_id="checkoutservice", condition="readiness_failure",
                    supporting_evidence=[sample["evidence_id"]])
            return DriverReply(json.dumps(decision), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

    first, _ = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout=layout, clock=Clock())
    event = controller.run_epoch(first, WrongAnchor())
    report = event["report"]
    assert report["turns"][-1]["proposal_review"]["reason"] == "downstream_anomaly_unresolved"
    assert report["turns"][-1]["decision"]["hypothesis"]["service_id"] == "checkoutservice"
    assert report["termination_reason"] == "decision_budget_exhausted"
    assert report["session"]["calls_used"] == 8 and len(report["attempts"]) == 4
    assert not report["candidate_screened"] and controller.snapshot()["current_candidate"] is None
    assert not report["execution_authorized"]
