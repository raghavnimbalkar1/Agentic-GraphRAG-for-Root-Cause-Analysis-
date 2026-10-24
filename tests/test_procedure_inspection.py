"""Trusted operator adapter tests; no real Docker, graph, HTTP or provider calls."""

from copy import deepcopy
from datetime import timedelta
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from core.health import Observation
from core.procedure_contracts import PreflightPermissions
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from simulation import procedure_preflight as module
from tests.test_procedure_contracts import INSTANCE, NOW, REVISION, catalog


def inspect(monkeypatch, *, statuses=("CONNECTION_REFUSED", "CONNECTION_REFUSED"), second_catalog=None,
            permission=True, owner=None, instance=INSTANCE):
    cap = catalog(applies_to=["paymentservice"])
    capture = Mock(side_effect=[cap, second_catalog or cap])
    monkeypatch.setattr(module, "capture_procedure_catalog", capture)
    readings = [Observation(status, "Probe evidence", measured_at=NOW.isoformat(), instance_id=INSTANCE)
                for status in statuses]
    if len(readings) == 2:
        readings[-1].instance_id = instance
    probe = Mock(side_effect=readings)
    docker = Mock()
    target = docker.containers.get.return_value
    target.id, target.name = INSTANCE, "paymentservice"
    target.attrs = {"Config": {"Labels": {REGISTRY.ownership_label: owner or REGISTRY.ownership_value}},
                    "HostConfig": {"CpuQuota": 0}}
    result = module.inspect_procedure(Mock(), docker, skill_name=cap.contracts[0].name,
        service_id="paymentservice", incident_id="INC-CONTROL", revision=REVISION,
        permissions=PreflightPermissions(disruption=permission), observe_fn=probe, now_fn=lambda: NOW)
    docker.containers.run.assert_not_called()
    target.restart.assert_not_called()
    target.update.assert_not_called()
    target.exec_run.assert_not_called()
    assert not result["action_dispatched"] and not result["execution_authorized"]
    return result, capture, probe, docker


def test_inspector_recaptures_catalog_and_probes_before_read_only_eligibility(monkeypatch):
    result, capture, probe, docker = inspect(monkeypatch)
    assert result["eligible"] and capture.call_count == probe.call_count == 2
    assert len(result["observations"]) == 2 and result["runtime"]["instance_id"] == INSTANCE
    assert result["plan"]["catalog_digest"] == result["current_catalog_digest"]
    assert result["plan"]["evidence_id"] != result["fresh_evidence_id"]
    docker.containers.get.assert_called_once_with(INSTANCE)


@pytest.mark.parametrize("status", ["HEALTHY", "UNKNOWN", "HIGH_CPU", "DEGRADED"])
def test_inspector_will_not_turn_an_unmatched_condition_into_a_restart(monkeypatch, status):
    result, capture, probe, _ = inspect(monkeypatch, statuses=(status,))
    assert not result["eligible"] and result["reason"] == "no_matching_observed_condition"
    assert capture.call_count == probe.call_count == 1
    assert len(result["observations"]) == 1


def test_recovered_service_during_preflight_is_denied(monkeypatch):
    result, _, _, _ = inspect(monkeypatch, statuses=("CONNECTION_REFUSED", "HEALTHY"))
    assert not result["eligible"]


def test_catalog_edit_during_preflight_is_denied(monkeypatch):
    changed = catalog(applies_to=["paymentservice"], description="Changed while planning")
    result, _, _, _ = inspect(monkeypatch, second_catalog=changed)
    assert result["reason"] == "catalog_changed"


def test_replacement_during_preflight_is_denied(monkeypatch):
    result, _, _, _ = inspect(monkeypatch, instance="d" * 64)
    assert result["reason"] == "identity_or_ownership_changed"


def test_owner_change_is_denied(monkeypatch):
    result, _, _, _ = inspect(monkeypatch, owner="unrelated-application")
    assert result["reason"] == "identity_or_ownership_changed"


def test_default_no_permission_never_becomes_eligible(monkeypatch):
    result, _, _, _ = inspect(monkeypatch, permission=False)
    assert result["reason"] == "operator_permission_required"


def test_wrong_target_is_denied_before_probe(monkeypatch):
    monkeypatch.setattr(module, "capture_procedure_catalog", lambda _: catalog())
    probe = Mock(side_effect=AssertionError("Must not probe the wrong target"))
    result = module.inspect_procedure(Mock(), Mock(), skill_name="Payment_Restart", service_id="paymentservice",
        incident_id="INC-TEST", revision=REVISION, observe_fn=probe)
    assert result["reason"] == "procedure_target_mismatch"


def test_probe_failure_has_fixed_error_without_leaking_raw_exception(monkeypatch):
    cap = catalog(applies_to=["paymentservice"])
    monkeypatch.setattr(module, "capture_procedure_catalog", lambda _: cap)
    result = module.inspect_procedure(Mock(), Mock(), skill_name="Payment_Restart", service_id="paymentservice",
        incident_id="INC-TEST", revision=REVISION, observe_fn=Mock(side_effect=RuntimeError("PRIVATE-RAW")))
    assert result["reason"] == "invalid_or_unavailable_preflight"
    assert "PRIVATE-RAW" not in json.dumps(result)


def test_second_probe_failure_retains_the_first_observation_and_bound_intent(monkeypatch):
    cap = catalog(applies_to=["paymentservice"])
    monkeypatch.setattr(module, "capture_procedure_catalog", lambda _: cap)
    first = Observation("CONNECTION_REFUSED", "Probe", measured_at=NOW.isoformat(), instance_id=INSTANCE)
    result = module.inspect_procedure(Mock(), Mock(), skill_name="Payment_Restart", service_id="paymentservice",
        incident_id="INC-CONTROL", revision=REVISION, observe_fn=Mock(side_effect=[first, RuntimeError("PRIVATE")]),
        now_fn=lambda: NOW)
    assert result["reason"] == "invalid_or_unavailable_preflight"
    assert result["plan"]["evidence_id"] == result["observations"][0]["evidence_id"]
    assert not result["eligible"] and "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("flag,value", [("--service", "unregistered"), ("--revision", "short"),
    ("--incident-id", "../bad"), ("--skill", "../bad")])
def test_cli_rejects_invalid_scope_before_marker_or_runtime(tmp_path, flag, value):
    output = tmp_path / "result.json"
    args = {"--skill": "Payment_Restart_SOP", "--service": "paymentservice",
            "--incident-id": "INC-CONTROL", "--revision": REVISION, "--output": str(output)}
    args[flag] = value
    process = subprocess.run([sys.executable, "-m", "simulation.procedure_preflight",
        *(value for pair in args.items() for value in pair)], capture_output=True, text=True)
    assert process.returncode != 0 and not output.exists()
    assert not output.with_name("result.json.started.json").exists()


def test_cli_cannot_overwrite_previous_report(tmp_path):
    output = tmp_path / "result.json"
    output.write_text("KEEP")
    process = subprocess.run([sys.executable, "-m", "simulation.procedure_preflight", "--skill", "Payment_Restart_SOP",
        "--service", "paymentservice", "--incident-id", "INC-CONTROL", "--revision", REVISION,
        "--output", str(output)], capture_output=True, text=True)
    assert process.returncode != 0 and output.read_text() == "KEEP"


def test_inspection_input_does_not_mutate_caller_permission_object(monkeypatch):
    permission = PreflightPermissions(disruption=True)
    before = deepcopy(permission.model_dump())
    cap = catalog(applies_to=["paymentservice"])
    monkeypatch.setattr(module, "capture_procedure_catalog", lambda _: cap)
    old = Observation("CONNECTION_REFUSED", "Old probe", measured_at=(NOW - timedelta(seconds=31)).isoformat(), instance_id=INSTANCE)
    result = module.inspect_procedure(Mock(), Mock(), skill_name="Payment_Restart", service_id="paymentservice",
        incident_id="INC-CONTROL", revision=REVISION, permissions=permission, observe_fn=lambda *args: old, now_fn=lambda: NOW)
    assert not result["eligible"] and permission.model_dump() == before
