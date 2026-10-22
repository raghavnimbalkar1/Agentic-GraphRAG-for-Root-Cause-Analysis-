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
