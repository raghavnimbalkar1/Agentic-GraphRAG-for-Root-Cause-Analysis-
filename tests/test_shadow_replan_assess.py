"""Post-run controls preserve failed policy proposals and distinguish guard fallback."""

from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest

from agent.shadow import DriverReply
from agent.shadow_context import unpack_view
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replan_assess import ReplanControls, assess_replanning, implementation_digest
from eval.shadow_replay import _write_new
from tests.test_replanning_profile import full_epochs
from tests.test_shadow_replan import Clock


def controls_for(snapshot):
    return deepcopy({"profile": snapshot["profile"], "evidence_layout": snapshot.get("evidence_layout", "legacy-v1"),
        "implementation_digest": implementation_digest(snapshot["implementation"]),
        "evidence_limits": snapshot["evidence_limits"], "shadow_limits": snapshot["shadow_limits"], "epochs": [
            {"epoch": row["epoch"], "input_digest": row["input_digest"],
             "expected": {"outcome": "proposal", "service_id": target, "condition": "readiness_failure"},
             "clue_service": target} for row, target in zip(snapshot["epochs"], ("paymentservice", "checkoutservice"), strict=True)]})


@pytest.fixture
def passed():
    first, second = full_epochs()
    clock = Clock()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout="time-indexed-v1", clock=clock)
    controller.run_epoch(first, FixedProbePolicy())
    controller.run_epoch(second, FixedProbePolicy())
    return controller, clock


def test_complete_control_passes_but_is_not_live_freshness_or_recovery(passed):
    controller, clock = passed
    snapshot = controller.snapshot()
    controls = controls_for(snapshot)
    result = assess_replanning(snapshot, controls)
    assert result["diagnostic_control_passed"] and result["current_candidate_recorded"]
    clock.value += 31
    result = assess_replanning(controller.snapshot(), controls)
    assert result["diagnostic_control_passed"] and not result["current_candidate_recorded"]
    assert not result["current_freshness_independently_verified"] and not result["recovery_verified"]
    assert result["assessment_model_calls"] == 0 and not result["execution_authorized"]


@pytest.mark.parametrize("change", [
    lambda value: value["epochs"].pop(),
    lambda value: value["epochs"].append(deepcopy(value["epochs"][-1])),
    lambda value: value["epochs"][1].update(epoch=1),
    lambda value: value["epochs"][1].update(input_digest="f" * 64),
    lambda value: value.update(profile="legacy"),
    lambda value: value.update(evidence_layout="legacy-v1"),
    lambda value: value.update(implementation={}),
    lambda value: value["evidence_limits"].update(max_tool_calls=7),
    lambda value: value["shadow_limits"].update(max_decisions=6),
    lambda value: value.update(execution_authorized=True),
])
def test_missing_extra_misaligned_or_unmatched_epochs_cannot_be_dropped_from_denominator(passed, change):
    snapshot = passed[0].snapshot()
    controls = controls_for(snapshot)
    change(snapshot)
    result = assess_replanning(snapshot, controls)
    assert not result["diagnostic_control_passed"]
    assert len(result["epochs"]) == max(len(snapshot["epochs"]), len(controls["epochs"]))
    if [row["epoch"] for row in snapshot["epochs"]] != [row["epoch"] for row in controls["epochs"]]:
        assert not result["all_epochs_recorded"]


@pytest.mark.parametrize("field,value", [
    ("candidate_screened", False), ("screening_policy", "provisional-v1"),
    ("execution_authorized", True), ("usage_complete", False),
    ("termination_reason", "decision_budget_exhausted"),
    ("evidence_layout", "legacy-v1"), ("context_profile", "compact"), ("mode", "not-shadow"),
])
def test_final_result_alone_cannot_pass_without_screen_usage_and_explicit_policy_terminal(passed, field, value):
    snapshot = passed[0].snapshot()
    controls = controls_for(snapshot)
    snapshot["epochs"][1]["report"][field] = value
    result = assess_replanning(snapshot, controls)
    assert not result["diagnostic_control_passed"] and not result["epochs"][1]["diagnostic_control_passed"]


def test_unknown_guard_fallback_is_not_the_correct_explicit_abstention():
    first, second = full_epochs(after={"paymentservice": "UNKNOWN"})
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=Clock())
    controller.run_epoch(first, FixedProbePolicy())
    controller.run_epoch(second, FixedProbePolicy())
    snapshot = controller.snapshot()
    controls = controls_for(snapshot)
    controls["epochs"][1].update(expected={"outcome": "abstained", "reason": "insufficient_evidence"},
        clue_service="paymentservice", clue_status="UNKNOWN")
    assert assess_replanning(snapshot, controls)["diagnostic_control_passed"]
    snapshot["epochs"][1]["report"]["termination_reason"] = "reported_token_limit"
    result = assess_replanning(snapshot, controls)
    assert not result["diagnostic_control_passed"] and result["epochs"][1]["guard_fallback"]


def test_reconstructed_failed_decision_patterns_still_block_and_remain_visible_in_assessment():
    class BadTerminal(FixedProbePolicy):
        epoch = 1

        def complete(self, messages, **kwargs):
            reply = super().complete(messages, **kwargs)
            decision = json.loads(reply.content)
            if decision["action"] == "propose":
                view = unpack_view(json.loads(messages[-1]["content"]))
                row = next(row for row in view["evidence_index"] if row["service"] == "checkoutservice")
                decision["hypothesis"].update(service_id="checkoutservice", condition="readiness_failure",
                    supporting_evidence=[row["latest"][0]["evidence_id"]],
                    unresolved_alternatives=[] if self.epoch == 1 else ["paymentservice"])
            return DriverReply(json.dumps(decision), reply.usage)

    first, second = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout="time-indexed-v1", clock=Clock())
    driver = BadTerminal()
    controller.run_epoch(first, driver)
    driver.epoch = 2
    controller.run_epoch(second, driver)
    snapshot = controller.snapshot()
    assessed = assess_replanning(snapshot, controls_for(snapshot))
    assert not assessed["diagnostic_control_passed"] and not assessed["current_candidate_recorded"]
    assert [row["last_policy_proposal"]["screen_reason"] for row in assessed["epochs"]] == [
        "downstream_anomaly_unresolved", "declared_unresolved_evidence"]
    assert all(row["last_policy_proposal"]["service_id"] == "checkoutservice" for row in assessed["epochs"])
    assert all(row["guard_fallback"] for row in assessed["epochs"])
    assert snapshot["incident_budget"]["calls_used"] == 16 and snapshot["incident_budget"]["decisions_used"] == 8


@pytest.mark.parametrize("report", [["not-a-report"], {"turns": [None]}, {"result": []}, {"turns": []}])
def test_malformed_report_is_not_a_passing_result(passed, report):
    snapshot = passed[0].snapshot()
    controls = controls_for(snapshot)
    snapshot["epochs"][1]["report"] = report
    result = assess_replanning(snapshot, controls)
    assert not result["diagnostic_control_passed"]


@pytest.mark.parametrize("change", [
    lambda value: value["epochs"].reverse(),
    lambda value: value["epochs"][0].update(input_digest="invalid"),
    lambda value: value["epochs"][0].update(clue_service="unregistered"),
    lambda value: value["epochs"][0]["expected"].update(service_id="checkoutservice"),
    lambda value: value.update(evidence_layout="automatic"),
    lambda value: value.update(extra="private"),
    lambda value: value["epochs"].pop(),
])
def test_invalid_controls_are_rejected_before_assessment(passed, change):
    controls = controls_for(passed[0].snapshot())
    change(controls)
    with pytest.raises(ValueError):
        ReplanControls.model_validate(controls)


def test_cli_is_private_exclusive_and_needs_no_provider_credentials(passed, tmp_path):
    snapshot = passed[0].snapshot()
    report, controls, output = [tmp_path / name for name in ("report.json", "controls.json", "assessment.json")]
    _write_new(report, snapshot)
    _write_new(controls, controls_for(snapshot))
    environment = {key: value for key, value in os.environ.items() if "API_KEY" not in key and "PASSWORD" not in key}
    command = [sys.executable, "-m", "eval.shadow_replan_assess", "--report", str(report),
               "--controls", str(controls), "--output", str(output)]
    run = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text())["assessment_model_calls"] == 0
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    repeated = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
    assert repeated.returncode != 0 and output.read_bytes() == before
