"""Read-only orchestration controls. All decisions are scripted, not model results."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.investigation import InvestigationSession
from agent.shadow import DriverReply, ShadowRunner
from agent.shadow_context import unpack_view
from agent.shadow_provider import GroqShadowDriver
from core.diagnostics import ComponentReader
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replan_assess import assess_replanning
from eval.shadow_replay import materialize
from tests.test_replanning_profile import full_epochs
from tests.test_shadow_replan import Clock
from tests.test_shadow_replan_assess import controls_for


PROTOCOL = "bounded-coverage-v1"


def controller(first, **kwargs):
    return ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout="time-indexed-v1",
        investigation_protocol=PROTOCOL, clock=Clock(), **kwargs)


def reply(decision):
    return DriverReply(json.dumps(decision), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})


class Recording(FixedProbePolicy):
    def __init__(self):
        self.views = []

    def complete(self, messages, **kwargs):
        self.views.append(unpack_view(json.loads(messages[-1]["content"])))
        return super().complete(messages, **kwargs)


def test_mandatory_reads_are_visible_charged_and_not_counted_as_model_decisions():
    first, _ = full_epochs()
    policy, run = Recording(), controller(first)
    report = run.run_epoch(first, policy)["report"]
    fill = [turn for turn in report["turns"] if turn.get("actor") == "coverage-controller"]
    assert len(fill) == 1 and len(fill[0]["feedback"]) == 5
    assert "decision" not in fill[0]
    assert report["session"]["calls_used"] == 8 and len(report["attempts"]) == 2
    assert run.snapshot()["incident_budget"]["decisions_used"] == 2
    assert run.snapshot()["incident_budget"]["calls_used"] == 8
    assert report["investigation_protocol"] == PROTOCOL
    assert policy.views[1]["initial"]["root_cause_node"] is None
    assert policy.views[1]["remaining_decisions"] == 2
    assert policy.views[1]["remaining_tool_calls"] == 0
    assert report["result"]["proposal"]["service_id"] == "paymentservice"
    assert report["candidate_screened"] and not report["execution_authorized"]


def test_premature_abstention_is_retained_and_coverage_completed_before_reconsideration():
    class Premature(Recording):
        def complete(self, messages, **kwargs):
            view = unpack_view(json.loads(messages[-1]["content"]))
            if not view["turns"]:
                self.views.append(view)
                return reply({"action": "abstain", "reason": "insufficient_evidence"})
            return super().complete(messages, **kwargs)

    first, _ = full_epochs()
    run, policy = controller(first), Premature()
    report = run.run_epoch(first, policy)["report"]
    assert report["turns"][0]["outcome"] == "abstention_deferred"
    assert report["turns"][0]["decision"]["reason"] == "insufficient_evidence"
    assert len(report["turns"][1]["feedback"]) == 8
    assert report["session"]["calls_used"] == 8 and len(report["attempts"]) == 2
    assert report["candidate_screened"]


def wrong_anchor(view):
    identifier = next(feedback["data"]["samples"][-1]["evidence_id"] for turn in view["turns"]
        for feedback in turn.get("feedback", []) if feedback["request"]["service_id"] == "checkoutservice")
    return {"action": "propose", "hypothesis": {"service_id": "checkoutservice", "condition": "readiness_failure",
        "supporting_evidence": [identifier], "contradicting_evidence": [], "unresolved_alternatives": []}}


def test_wrong_target_can_be_corrected_only_by_a_new_policy_decision():
    class Correcting(Recording):
        def complete(self, messages, **kwargs):
            view = unpack_view(json.loads(messages[-1]["content"]))
            if view["evidence_progress"]["coverage_complete"] and not any(
                    turn.get("outcome") == "proposal_deferred" for turn in view["turns"]):
                self.views.append(view)
                return reply(wrong_anchor(view))
            return super().complete(messages, **kwargs)

    first, _ = full_epochs()
    run = controller(first)
    report = run.run_epoch(first, Correcting())["report"]
    rejected = next(turn for turn in report["turns"] if turn.get("outcome") == "proposal_deferred")
    assert rejected["decision"]["hypothesis"]["service_id"] == "checkoutservice"
    assert rejected["proposal_review"]["reason"] == "downstream_anomaly_unresolved"
    assert "replacement_candidate" not in rejected["proposal_review"]
    assert report["result"]["proposal"]["service_id"] == "paymentservice"
    assert len(report["attempts"]) == 3 and report["session"]["calls_used"] == 8


def test_repeated_bad_target_is_never_replaced_by_the_controller():
    class Wrong(Recording):
        def complete(self, messages, **kwargs):
            view = unpack_view(json.loads(messages[-1]["content"]))
            if view["evidence_progress"]["coverage_complete"]:
                return reply(wrong_anchor(view))
            return super().complete(messages, **kwargs)

    first, _ = full_epochs()
    run = controller(first)
    report = run.run_epoch(first, Wrong())["report"]
    assert len(report["attempts"]) == 4 and report["session"]["calls_used"] == 8
    assert report["termination_reason"] == "decision_budget_exhausted"
    assert not report["candidate_screened"] and run.snapshot()["current_candidate"] is None


def test_repeated_ungrounded_abstention_exhausts_the_same_allowance():
    class Refusing:
        kind = "scripted"

        def complete(self, *args, **kwargs):
            return reply({"action": "abstain", "reason": "insufficient_evidence"})

    first, _ = full_epochs()
    report = controller(first).run_epoch(first, Refusing())["report"]
    assert len(report["attempts"]) == 4 and report["session"]["calls_used"] == 8
    assert report["termination_reason"] == "unsupported_abstention"
    assert not report["candidate_screened"]


@pytest.mark.parametrize("after,expected", [
    ({"paymentservice": "UNKNOWN"}, "insufficient_evidence"),
    ({"paymentservice": "DOWN", "currencyservice": "DOWN"}, "unresolved_alternatives"),
    ({}, "unknown_condition"),
])
def test_negative_controls_still_abstain_and_never_get_a_candidate(after, expected):
    first, second = full_epochs(after=after)
    run = controller(first)
    run.run_epoch(first, Recording())
    report = run.run_epoch(second, Recording())["report"]
    assert report["result"]["outcome"] == "abstained" and report["result"]["reason"] == expected
    assert not report["candidate_screened"] and run.snapshot()["current_candidate"] is None
    assert run.snapshot()["incident_budget"]["calls_used"] <= 16
    assert run.snapshot()["incident_budget"]["decisions_used"] <= 8


def test_no_duplicate_retry_when_a_real_read_is_unavailable(monkeypatch):
    first, _ = full_epochs()
    monkeypatch.setattr(ComponentReader, "window", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    class Unavailable(Recording):
        def complete(self, messages, **kwargs):
            view = unpack_view(json.loads(messages[-1]["content"]))
            if view["turns"]:
                return reply({"action": "abstain", "reason": "insufficient_evidence"})
            return super().complete(messages, **kwargs)
    report = controller(first).run_epoch(first, Unavailable())["report"]
    assert report["session"]["calls_used"] == 3 and len(report["attempts"]) == 2
    assert not any(turn.get("actor") == "coverage-controller" for turn in report["turns"])
    assert report["result"]["reason"] == "insufficient_evidence"


def test_default_keeps_its_original_four_decision_read_schedule():
    first, _ = full_epochs()
    run = ReplanningInvestigation(first, profile="two-epoch-v1", clock=Clock())
    report = run.run_epoch(first, Recording())["report"]
    assert len(report["attempts"]) == 4 and report["session"]["calls_used"] == 8
    assert not any(turn.get("actor") == "coverage-controller" for turn in report["turns"])


@pytest.mark.parametrize("value", [None, True, "automatic", "bounded-coverage-v2"])
def test_unknown_protocols_are_rejected(value):
    first, _ = full_epochs()
    with pytest.raises(ValueError):
        ReplanningInvestigation(first, investigation_protocol=value)


def test_coverage_completion_cannot_be_enabled_without_native_dependency_screening():
    first, _ = full_epochs()
    with materialize(first) as path:
        session = InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id, as_of=first.as_of)
        with pytest.raises(ValueError):
            ShadowRunner(session, first.topology, Recording(), investigation_protocol=PROTOCOL)


def test_no_reads_past_deadline_and_partial_controller_feedback_is_retained(monkeypatch):
    first, _ = full_epochs()
    clock = Clock()
    original = ComponentReader.window

    def slow(*args, **kwargs):
        value = original(*args, **kwargs)
        clock.value += 100
        return value

    monkeypatch.setattr(ComponentReader, "window", slow)
    run = ReplanningInvestigation(first, profile="two-epoch-v1", clock=clock,
                                  investigation_protocol=PROTOCOL)
    report = run.run_epoch(first, Recording())["report"]
    assert report["termination_reason"] == "time_exhausted" and report["session"]["calls_used"] == 4
    fill = next(turn for turn in report["turns"] if turn.get("actor") == "coverage-controller")
    assert len(fill["feedback"]) == 1 and fill["feedback"][0]["outcome"] == "time_exhausted"
    assert len(report["attempts"]) == 1 and not report["candidate_screened"]


def test_uncoverable_scope_stops_without_silently_increasing_tool_budget():
    from eval.shadow_replay import FrozenInputs

    first, _ = full_epochs()
    data = first.model_dump(mode="json")
    symptom = next(row["payload"] for row in data["records"] if row["kind"] == "symptom")
    symptom["steps"][0].update(success=False, status_code=500)
    first = FrozenInputs.model_validate(data)
    run = controller(first)
    report = run.run_epoch(first, Recording())["report"]
    assert report["termination_reason"] == "coverage_budget_exhausted"
    assert report["session"]["calls_used"] == 3 and len(report["attempts"]) == 1
    assert run.snapshot()["incident_budget"]["limits"]["max_tool_calls"] == 16


@pytest.mark.usefixtures("mock_harmony_tokenizer")
def test_native_schema_cannot_advertise_reads_when_the_actual_read_budget_is_zero():
    first, _ = full_epochs()
    policy = Recording()
    controller(first).run_epoch(first, policy)
    driver = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)
    schema = driver._schema([
        {"role": "system", "content": "test"},
        {"role": "user", "content": json.dumps(policy.views[-1])}])
    assert "ToolRequest" not in schema["$defs"] and "ReadDecision" not in schema["$defs"]
    assert "ProposeDecision" in schema["$defs"] and "AbstainDecision" in schema["$defs"]


def test_assessment_binds_protocol_and_preserves_freshness_boundary():
    first, second = full_epochs()
    run = controller(first)
    run.run_epoch(first, Recording())
    run.run_epoch(second, Recording())
    snapshot = run.snapshot()
    controls = controls_for(snapshot)
    assert not assess_replanning(snapshot, controls)["diagnostic_control_passed"]
    controls["investigation_protocol"] = PROTOCOL
    assert assess_replanning(snapshot, controls)["diagnostic_control_passed"]
    assert snapshot["incident_budget"]["calls_used"] == 16
    assert snapshot["incident_budget"]["decisions_used"] == 4
    assert not snapshot["execution_authorized"] and not snapshot["recovery_verified"]
    wrong = deepcopy(snapshot)
    wrong["epochs"][0]["report"]["investigation_protocol"] = "model-directed-v1"
    assert not assess_replanning(wrong, controls)["diagnostic_control_passed"]


def test_mandatory_read_order_depends_on_scope_not_on_which_service_is_faulty():
    from tests.test_shadow_replan import epoch
    from eval.shadow_controls import fixture_topology

    orders = []
    for states in ({"paymentservice": "DOWN"}, {"redis-cart": "OOM_KILLED"}):
        first = epoch(statuses=states)
        first = first.model_copy(update={"topology": fixture_topology(first.as_of)})
        report = controller(first).run_epoch(first, Recording())["report"]
        orders.append([row["request"]["service_id"] for row in report["session"]["history"]])
    assert orders[0] == orders[1] and len(set(orders[0])) == 8


def test_expired_candidate_is_not_revalidated_by_faster_read_scheduling():
    first, _ = full_epochs()
    clock = Clock()

    class Slow(Recording):
        def complete(self, messages, **kwargs):
            clock.value += 16
            return super().complete(messages, **kwargs)

    run = ReplanningInvestigation(first, profile="two-epoch-v1", clock=clock,
                                  investigation_protocol=PROTOCOL)
    report = run.run_epoch(first, Slow())["report"]
    assert report["candidate_screened"] and run.snapshot()["current_candidate"] is None
    assert run.snapshot()["candidates"][0]["retirement_reason"] == "freshness_expired"


@pytest.mark.usefixtures("mock_harmony_tokenizer")
def test_native_read_batch_bound_matches_the_actual_remaining_allowance():
    class RedisThird(Recording):
        def complete(self, messages, **kwargs):
            view = unpack_view(json.loads(messages[-1]["content"]))
            if not view["turns"]:
                self.views.append(view)
                return reply({"action": "read", "requests": [
                    {"tool": "component_window", "service_id": name, "window_seconds": 60}
                    for name in ("checkoutservice", "cartservice", "redis-cart")]})
            return super().complete(messages, **kwargs)

    first, second = full_epochs(after={"paymentservice": "UNKNOWN"})
    run = controller(first)
    run.run_epoch(first, Recording())
    policy = RedisThird()
    run.run_epoch(second, policy)
    view = policy.views[1]
    assert view["remaining_tool_calls"] == 2
    driver = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)
    schema = driver._schema([{"role": "system", "content": "test"},
                             {"role": "user", "content": json.dumps(view)}])
    assert view["decision_protocol"]["max_requests_per_read"] == 2
    assert schema["$defs"]["ReadDecision"]["properties"]["requests"]["maxItems"] == 2


@pytest.mark.parametrize("allowance", [1, 2, 3])
def test_native_schema_rejects_batches_larger_than_the_advertised_allowance(allowance):
    from jsonschema import Draft202012Validator
    from agent.shadow import decision_schema
    from agent.shadow_protocol import provider_schema

    schema = provider_schema(decision_schema(), allowed_actions=["read"], max_read_requests=allowance)
    requests = [{"tool": "component_window", "service_id": name, "metric": None, "end_at": None,
                 "window_seconds": 60} for name in ("cartservice", "currencyservice", "paymentservice")]
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"decision": {"action": "read", "requests": requests[:allowance]}})
    assert not validator.is_valid({"decision": {"action": "read", "requests": [*requests[:allowance], requests[0]]}})


@pytest.mark.parametrize("value", [None, True, -1, 0, 4, 1.5])
def test_invalid_native_read_allowance_is_rejected_not_silently_raised(value):
    from agent.shadow import decision_schema
    from agent.shadow_protocol import provider_schema

    with pytest.raises(ValueError):
        provider_schema(decision_schema(), allowed_actions=["read"], max_read_requests=value)
