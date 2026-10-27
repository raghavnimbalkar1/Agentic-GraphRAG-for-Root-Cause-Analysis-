"""Campaign integrity and scoring controls; all model responses are mocked."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent.shadow import DriverReply
from agent.shadow_protocol import ProviderFailure
from eval.shadow_campaign import CampaignPlan, assess_campaign, check_campaign, prepare_campaign
from eval.shadow_controls import fixture_topology
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import _read_json, _write_new, load_inputs, save_inputs
from tests.test_shadow_replan import Clock, epoch
from tests.test_structured_shadow import ModelPolicy


@pytest.fixture
def planned(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN", "checkoutservice": "DEGRADED"})
    edges = fixture_topology(first.as_of).edges
    first = epoch(statuses={"paymentservice": "DOWN", "checkoutservice": "DEGRADED"}, edges=edges)
    second = epoch(1, previous=first, statuses={"checkoutservice": "DEGRADED"}, edges=edges)
    third = epoch(statuses={"shippingservice": "DOWN", "checkoutservice": "DEGRADED"}, edges=edges)
    cases = []
    for name, values, targets, cohort in [
        ("switch", [first, second], ["paymentservice", "checkoutservice"], "development"),
        ("shipping", [third], ["shippingservice"], "prospective"),
    ]:
        paths, controls = [], []
        for index, (value, target) in enumerate(zip(values, targets, strict=True), 1):
            path = tmp_path / "inputs" / name / f"{index}.json"
            save_inputs(path, value)
            paths.append(str(path))
            controls.append({"epoch": index, "input_digest": value.digest,
                "expected": {"outcome": "proposal", "service_id": target, "condition": "readiness_failure"},
                "clue_service": target, "clue_status": "DEGRADED" if target == "checkoutservice" else "DOWN"})
        cases.append({"case_id": name, "cohort": cohort, "inputs": paths, "controls": controls})
    return tmp_path, {"repetitions": 2, "cases": cases}


@pytest.fixture
def prepared(planned):
    base, plan = planned
    root = base / "campaign"
    prepare_campaign(plan, root)
    return root, plan


def reports_for(root, directory, *, mode=None):
    body, plan = check_campaign(root)
    cases = {case.case_id: case for case in plan.cases}
    reports = {}
    for serial, trial in enumerate(body["schedule"], 1):
        class MockModel(ModelPolicy):
            def complete(self, messages, **limits):
                if mode == "rate" and serial == 1:
                    raise ProviderFailure("provider_rate_limited", {"http_status": 429})
                if mode == "unknown" and serial == 1:
                    return DriverReply("{}", None)
                reply = super().complete(messages, **limits)
                return DriverReply(reply.content, {"input_tokens": 100 + serial,
                    "output_tokens": 100, "total_tokens": 200 + serial}, "stop")

        values = [load_inputs(root / path) for path in cases[trial["case_id"]].inputs]
        controller = ReplanningInvestigation(values[0], profile="two-epoch-v1", clock=Clock(),
            evidence_layout="time-indexed-v1", investigation_protocol="bounded-coverage-v1")
        for value in values:
            controller.run_epoch(value, MockModel(), approve_external=True)
        path = directory / f"{trial['trial_id']}.json"
        _write_new(path, controller.snapshot())
        reports[trial["trial_id"]] = path
    return reports


def test_prepare_freezes_repeat_order_controls_and_source_without_provider(prepared, monkeypatch):
    root, _ = prepared
    monkeypatch.setattr("agent.shadow_provider.configured_groq_driver", lambda **_: pytest.fail("Unexpected provider"))
    body, plan = check_campaign(root)
    assert plan.repetitions == 2
    assert [row["trial_id"] for row in body["schedule"]] == ["switch-r01", "shipping-r01", "switch-r02", "shipping-r02"]
    assert body["proposed_call_ceiling"] == 24 and body["new_model_calls"] == 0
    assert all(row["passed"] for row in body["fixed_preflights"])
    assert not body["model_trial_authorized"] and not body["held_out_independently_verified"]
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(root / "campaign.json").st_mode & 0o777 == 0o600
    assert "expected" not in (root / "switch/epoch-001.json").read_text()


def test_fixed_preflight_does_not_count_as_model_acceptance(prepared):
    root, _ = prepared
    result = assess_campaign(root, {})
    assert result["fixed_preflights_passed"] and not result["all_planned_trials_passed"]
    assert len(result["trials"]) == 4 and all(row["status"] == "not_run" for row in result["trials"])
    assert not result["usage_complete"] and result["assessment_model_calls"] == 0


def test_all_mock_controls_can_pass_without_claiming_sdk_origin_or_live_recovery(prepared, tmp_path):
    root, _ = prepared
    result = assess_campaign(root, reports_for(root, tmp_path / "reports"))
    assert result["all_planned_trials_passed"] and result["usage_complete"]
    assert result["cohorts"] == {"development": {"planned": 2, "passed": 2}, "prospective": {"planned": 2, "passed": 2}}
    assert result["known_reported_tokens"] > 0 and result["assessment_model_calls"] == 0
    assert not result["provider_origin_independently_verified"]
    assert not result["held_out_independently_verified"] and not result["live_freshness_verified"]
    assert not result["recovery_verified"] and not result["execution_authorized"] and not result["main_merge_allowed"]


def test_one_missing_repeat_blocks_acceptance_without_dropping_its_slot(prepared, tmp_path):
    root, _ = prepared
    reports = reports_for(root, tmp_path / "reports")
    reports.pop("shipping-r02")
    result = assess_campaign(root, reports)
    assert not result["all_planned_trials_passed"] and not result["all_reports_supplied"]
    assert len(result["trials"]) == 4 and result["trials"][-1]["status"] == "not_run"


def test_copied_transcript_is_not_an_independent_repetition(prepared, tmp_path):
    root, _ = prepared
    reports = reports_for(root, tmp_path / "reports")
    reports["switch-r02"] = reports["switch-r01"]
    result = assess_campaign(root, reports)
    assert result["duplicate_reports_present"] and not result["all_planned_trials_passed"]
    assert result["known_reported_tokens"] is None and not result["usage_complete"]
    assert all(not row["passed"] for row in result["trials"] if row["case_id"] == "switch")


@pytest.mark.parametrize("mode", ["rate", "unknown"])
def test_reports_after_a_provider_stop_are_not_accepted(prepared, tmp_path, mode):
    root, _ = prepared
    result = assess_campaign(root, reports_for(root, tmp_path / "reports", mode=mode))
    assert not result["reported_provider_stop_respected"] and not result["all_planned_trials_passed"]
    assert not result["usage_complete"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(implementation={}),
    lambda value: value.update(execution_authorized=True),
    lambda value: value.update(recovery_verified=True),
    lambda value: value.update(profile="legacy"),
    lambda value: value.update(origin_input_digest="0" * 64),
    lambda value: value["epochs"][0]["report"].update(known_reported_tokens=0),
    lambda value: value["epochs"][0]["report"].update(provider_attempts=999),
    lambda value: value["epochs"][0]["report"].update(driver_kind="deterministic"),
    lambda value: value["epochs"][0]["report"].update(candidate_screened=False),
    lambda value: value["epochs"][0]["report"]["initial_context"].update(availability_cutoff="2000-01-01T00:00:00+00:00"),
    lambda value: value["epochs"][0]["report"]["initial_context"]["topology"].update(edges=[]),
    lambda value: value["epochs"][0]["report"]["attempts"][0].update(max_output_tokens=9999),
    lambda value: value["epochs"][0]["report"]["attempts"][0].update(number=99),
    lambda value: value["epochs"][0].update(input_digest="0" * 64),
    lambda value: value["epochs"].pop(),
    lambda value: value["epochs"].append(value["epochs"][0]),
])
def test_mismatched_malformed_or_unaccounted_reports_cannot_pass(prepared, tmp_path, change):
    root, _ = prepared
    reports = reports_for(root, tmp_path / "reports")
    path = reports["switch-r01"]
    data = _read_json(path)
    change(data)
    path.write_text(json.dumps(data))
    result = assess_campaign(root, reports)
    assert not result["all_planned_trials_passed"] and not result["trials"][0]["passed"]


@pytest.mark.parametrize("target", ["switch/controls.json", "switch/fixed.json", "switch/fixed-prompts.json", "source-snapshot.json"])
def test_frozen_case_or_archive_tampering_is_rejected(prepared, target):
    root, _ = prepared
    (root / target).write_text("{}")
    with pytest.raises((ValueError, KeyError, TypeError)):
        check_campaign(root)


def test_changed_source_is_rejected_before_scoring(prepared, monkeypatch):
    root, _ = prepared
    monkeypatch.setattr("eval.shadow_campaign.implementation_manifest", lambda: {})
    with pytest.raises(ValueError, match="source"):
        assess_campaign(root, {})


def test_input_mutation_is_rejected(prepared):
    root, _ = prepared
    path = root / "switch/epoch-001.json"
    data = _read_json(path)
    data["inputs"]["records"][0]["payload"]["status"] = "DOWN"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        check_campaign(root)


@pytest.mark.parametrize("repetitions", [0, 4, True, "2"])
def test_invalid_repeat_counts_are_rejected(planned, repetitions):
    _, plan = planned
    plan["repetitions"] = repetitions
    with pytest.raises(ValueError):
        CampaignPlan.model_validate(plan)


@pytest.mark.parametrize("change", [
    lambda plan: plan["cases"][0].update(case_id="../escape"),
    lambda plan: plan["cases"].append(deepcopy(plan["cases"][0])),
    lambda plan: plan["cases"][0].update(cohort="held-out"),
    lambda plan: plan.update(model_trial_authorized=True),
    lambda plan: plan["cases"][0]["controls"].pop(),
    lambda plan: plan["cases"][0]["controls"][0].update(epoch=2),
])
def test_scope_escalation_and_invalid_plans_are_rejected(planned, change):
    _, plan = planned
    change(plan)
    with pytest.raises(ValueError):
        CampaignPlan.model_validate(plan)


def test_duplicate_inputs_cannot_be_counted_as_new_cases(planned):
    _, plan = planned
    same = deepcopy(plan["cases"][0])
    same["case_id"] = "different-name"
    plan["cases"].append(same)
    with pytest.raises(ValueError, match="repetitions"):
        CampaignPlan.model_validate(plan)


def test_input_digest_mismatch_fails_before_creating_campaign(planned):
    base, plan = planned
    plan["cases"][0]["controls"][0]["input_digest"] = "0" * 64
    with pytest.raises(ValueError):
        prepare_campaign(plan, base / "rejected")
    assert not (base / "rejected").exists()


def test_existing_campaign_cannot_be_overwritten(prepared):
    root, plan = prepared
    before = (root / "campaign.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare_campaign(plan, root)
    assert (root / "campaign.json").read_bytes() == before


def test_extra_trial_cannot_be_inserted_at_assessment(prepared):
    root, _ = prepared
    with pytest.raises(ValueError):
        assess_campaign(root, {"surprise-r01": "missing.json"})


def test_offline_cli_preserves_missing_trials_and_cannot_dispatch(prepared, tmp_path):
    root, _ = prepared
    mapping, output = tmp_path / "reports.json", tmp_path / "assessment.json"
    _write_new(mapping, {})
    args = [sys.executable, "-m", "eval.shadow_campaign", "assess", "--campaign", str(root),
            "--reports", str(mapping), "--output", str(output)]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 2 and _read_json(output)["assessment_model_calls"] == 0
    before = output.read_bytes()
    assert subprocess.run(args, capture_output=True, text=True, check=False).returncode == 1
    assert output.read_bytes() == before
    assert subprocess.run(args + ["--approve-model"], capture_output=True, text=True, check=False).returncode == 2


@pytest.mark.parametrize("version", [True, "1", 2])
def test_explicit_schema_version_required(planned, version):
    _, plan = planned
    plan["schema_version"] = version
    with pytest.raises(ValueError):
        CampaignPlan.model_validate(plan)


def test_failed_fixed_control_is_retained_and_blocks_campaign(planned):
    base, plan = planned
    plan["cases"][1]["controls"][0]["expected"]["condition"] = "resource_pressure"
    root = base / "failed-control"
    body = prepare_campaign(plan, root)
    assert not body["fixed_preflights"][1]["passed"]
    assert len(body["schedule"]) == 4
    result = assess_campaign(root, {})
    assert not result["fixed_preflights_passed"] and not result["all_planned_trials_passed"]


def test_retained_observations_cannot_enter_synthetic_campaign(planned):
    base, plan = planned
    path = Path(plan["cases"][1]["inputs"][0])
    inputs = load_inputs(path).model_copy(update={"source_kind": "retained-observation"})
    retained = base / "retained.json"
    save_inputs(retained, inputs)
    plan["cases"][1]["inputs"] = [str(retained)]
    plan["cases"][1]["controls"][0]["input_digest"] = inputs.digest
    with pytest.raises(ValueError):
        prepare_campaign(plan, base / "rejected")
    assert not (base / "rejected").exists()


def test_screened_flag_alone_cannot_replace_full_scope_evidence(prepared, tmp_path):
    root, _ = prepared
    reports = reports_for(root, tmp_path / "reports")
    path = reports["shipping-r01"]
    data = _read_json(path)
    report = data["epochs"][0]["report"]
    for turn in report["turns"]:
        if "feedback" in turn:
            turn["feedback"] = [item for item in turn["feedback"]
                if (item.get("request") or {}).get("service_id") == "shippingservice"]
    assert report["candidate_screened"]
    path.write_text(json.dumps(data))
    result = assess_campaign(root, reports)
    row = result["trials"][1]
    assert not result["all_planned_trials_passed"] and not row["passed"]
    assert row["epochs"][0]["score"]["control_passed"]
    assert not row["epochs"][0]["evidence_screen_recomputed"]
