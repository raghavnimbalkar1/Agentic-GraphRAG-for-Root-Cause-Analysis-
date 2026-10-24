"""A guard fallback is not a correct policy decision or a measured RCA success."""

from copy import deepcopy

import pytest

from agent.shadow import ShadowRunner
from eval.shadow_controls import assess_control, run_controls


@pytest.fixture(scope="module")
def reports():
    return {row["case"]: row["report"] for row in run_controls()["controls"]}


@pytest.mark.parametrize("case,target,condition", [
    ("payment", "paymentservice", "readiness_failure"),
    ("redis", "redis-cart", "resource_pressure"),
])
def test_matching_explicit_provisional_control_with_component_clue(reports, case, target, condition):
    score = assess_control(reports[case], expected={"outcome": "proposal", "service_id": target,
        "condition": condition}, clue_service=target)
    assert score["control_passed"] and score["explicit_policy_terminal"]
    assert not score["causal_accuracy_measured"]


def unknown_score(report):
    return assess_control(report, expected={"outcome": "abstained", "reason": "insufficient_evidence"},
                          clue_service="paymentservice", clue_status="UNKNOWN")


def test_explicit_policy_abstention_with_observed_unknown_can_pass_control(reports):
    score = unknown_score(reports["unknown-telemetry"])
    assert score["control_passed"] and score["explicit_policy_terminal"] and score["required_clue_seen"]


@pytest.mark.parametrize("termination", ["reported_token_limit", "decision_budget_exhausted", "time_exhausted",
    "prompt_limit", "admission_budget_exhausted", "tool_budget_exhausted", "provider_unavailable", "usage_unknown",
    "denied", "unshown_citation"])
def test_fallback_abstention_never_becomes_a_correct_policy_control(reports, termination):
    report = deepcopy(reports["unknown-telemetry"])
    report["termination_reason"] = termination
    score = unknown_score(report)
    assert score["terminal_matches_control"] and score["required_clue_seen"]
    assert not score["control_passed"] and not score["explicit_policy_terminal"]


def test_abstention_requires_an_explicit_terminal_decision_record(reports):
    report = deepcopy(reports["unknown-telemetry"])
    report["turns"].pop()
    assert not unknown_score(report)["control_passed"]


def test_empty_window_is_not_an_observed_unknown_sample(reports):
    report = deepcopy(reports["unknown-telemetry"])
    for turn in report["turns"]:
        for feedback in turn.get("feedback", []):
            if (feedback.get("data") or {}).get("service_id") == "paymentservice":
                feedback["data"]["samples"] = []
                feedback["data"]["summary"]["unknown_samples"] = 0
    assert not unknown_score(report)["required_clue_seen"]


def test_wrong_target_can_be_a_valid_provisional_hypothesis_but_fails_control(reports):
    report = deepcopy(reports["redis"])
    report["result"]["proposal"].update(service_id="checkoutservice", condition="readiness_failure")
    report["turns"][-1]["decision"]["hypothesis"] = deepcopy(report["result"]["proposal"])
    report["turns"][-1]["result"] = deepcopy(report["result"])
    score = assess_control(report, expected={"outcome": "proposal", "service_id": "redis-cart",
        "condition": "resource_pressure"}, clue_service="redis-cart")
    assert score["explicit_policy_terminal"] and not score["terminal_matches_control"]
    assert not score["control_passed"]


def test_explicit_abstention_without_component_read_does_not_pass_evidence_control(reports):
    report = deepcopy(reports["unknown-telemetry"])
    report["turns"] = [report["turns"][-1]]
    assert not unknown_score(report)["control_passed"]


def test_standalone_controls_do_not_count_a_fallback_as_policy_success(monkeypatch):
    original = ShadowRunner.run
    def with_guard_fallback(runner):
        report = original(runner)
        if report["result"].get("reason") == "insufficient_evidence":
            report["termination_reason"] = "reported_token_limit"
            report["turns"].pop()
        return report
    monkeypatch.setattr(ShadowRunner, "run", with_guard_fallback)
    result = run_controls()
    unknown = next(row for row in result["controls"] if row["case"] == "unknown-telemetry")
    assert unknown["actual"] == unknown["expected"]
    assert not unknown["passed"] and not result["passed"]


@pytest.mark.parametrize("case,reason,clue", [
    ("multiple-candidates", "unresolved_alternatives", "paymentservice"),
    ("unrelated-cpu-only", "unknown_condition", "checkoutservice"),
])
def test_screened_negative_needs_reason_specific_evidence_not_a_lucky_reason(case, reason, clue):
    report = next(row["report"] for row in run_controls(screening="dependency")["controls"] if row["case"] == case)
    expected = {"outcome": "abstained", "reason": reason}
    assert assess_control(report, expected=expected, clue_service=clue)["control_passed"]
    # Keep the matching terminal and one clue, but remove the rest of the reads.
    # A cached complete assessment must not stand in for the actual transcript.
    for turn in report["turns"]:
        if "feedback" in turn:
            turn["feedback"] = [row for row in turn["feedback"] if row["data"]["service_id"] == clue]
    score = assess_control(report, expected=expected, clue_service=clue)
    assert score["terminal_matches_control"] and score["required_clue_seen"]
    assert score["abstention_supported"] is False
    assert not score["control_passed"]


def test_two_observed_independent_failures_support_abstention_without_reading_everything():
    report = next(row["report"] for row in run_controls(screening="dependency")["controls"]
                  if row["case"] == "multiple-candidates")
    for turn in report["turns"]:
        if "feedback" in turn:
            turn["feedback"] = [row for row in turn["feedback"]
                                if row["data"]["service_id"] in {"paymentservice", "redis-cart"}]
    score = assess_control(report, expected={"outcome": "abstained", "reason": "unresolved_alternatives"},
                           clue_service="paymentservice")
    assert score["abstention_supported"] and score["control_passed"]


def test_same_chain_anomalies_do_not_support_independent_alternatives():
    report = next(row["report"] for row in run_controls(screening="dependency")["controls"]
                  if row["case"] == "payment")
    report["result"] = {"outcome": "abstained", "reason": "unresolved_alternatives"}
    report["termination_reason"] = "abstained"
    report["turns"][-1] = {"decision": {"action": "abstain", "reason": "unresolved_alternatives"},
                            "result": report["result"]}
    score = assess_control(report, expected=report["result"], clue_service="paymentservice")
    assert score["terminal_matches_control"] and score["required_clue_seen"]
    assert score["abstention_supported"] is False and not score["control_passed"]


@pytest.mark.parametrize("change", [
    lambda data: data["summary"].update(fresh_at_cutoff=False),
    lambda data: data["summary"].update(latest_conflict=True),
    lambda data: data["samples"][-1].update(instance_id=None),
    lambda data: data["samples"][-1].update(quality="unknown"),
])
def test_uncertain_branch_does_not_establish_independent_failures(change):
    report = next(row["report"] for row in run_controls(screening="dependency")["controls"]
                  if row["case"] == "multiple-candidates")
    for turn in report["turns"]:
        for feedback in turn.get("feedback", []):
            if feedback["data"]["service_id"] == "redis-cart":
                change(feedback["data"])
    score = assess_control(report, expected={"outcome": "abstained", "reason": "unresolved_alternatives"},
                           clue_service="paymentservice")
    assert score["required_clue_seen"] and not score["abstention_supported"] and not score["control_passed"]


def test_unread_scope_is_not_evidence_of_unknown_telemetry():
    from agent.evidence_policy import assess_abstention

    report = next(row["report"] for row in run_controls(screening="dependency")["controls"]
                  if row["case"] == "unknown-telemetry")
    context = report["initial_context"]
    assert assess_abstention(context, report["turns"], "insufficient_evidence")["supported"]
    assert not assess_abstention(context, [], "insufficient_evidence")["supported"]


@pytest.mark.parametrize("outcome,supported", [("unavailable", True), ("denied", False), ("duplicate", False)])
def test_unavailable_read_is_distinct_from_an_unread_or_invalid_request(outcome, supported):
    from agent.evidence_policy import assess_abstention

    report = next(row["report"] for row in run_controls(screening="dependency")["controls"]
                  if row["case"] == "unknown-telemetry")
    turns = [{"feedback": [{"outcome": outcome, "tool": "component_window",
                            "request": {"service_id": "paymentservice"}, "data": None}]}]
    assert assess_abstention(report["initial_context"], turns, "insufficient_evidence")["supported"] is supported


def test_scoring_source_is_included_in_new_frozen_implementation_manifests():
    from eval.shadow_replay import implementation_manifest

    assert "eval/shadow_controls.py" in implementation_manifest()["source_sha256"]


@pytest.mark.parametrize("shared_status,supported", [("OOM_KILLED", False), ("UNKNOWN", False), ("HEALTHY", True)])
def test_separate_callers_need_a_known_healthy_shared_dependency(shared_status, supported):
    from agent.evidence_policy import assess_abstention

    report = next(row["report"] for row in run_controls(screening="dependency")["controls"]
                  if row["case"] == "redis")
    context = report["initial_context"]
    context["topology"]["edges"] = [["checkoutservice", "cartservice"], ["checkoutservice", "currencyservice"],
        ["cartservice", "redis-cart"], ["currencyservice", "redis-cart"]]
    for turn in report["turns"]:
        for feedback in turn.get("feedback", []):
            data = feedback["data"]
            if data["service_id"] == "currencyservice":
                data["samples"][-1]["status"] = "DOWN"
                data["summary"]["status_at_window_end"] = "DOWN"
            if data["service_id"] == "redis-cart":
                data["samples"][-1].update(status=shared_status,
                    quality="unknown" if shared_status == "UNKNOWN" else "observed")
                data["summary"]["status_at_window_end"] = shared_status
    review = assess_abstention(context, report["turns"], "unresolved_alternatives")
    assert review["supported"] is supported
