"""Offline live-handoff controls using local seeded stores and a fake model."""

from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from agent.shadow import DriverReply
from agent.shadow_protocol import ProviderFailure
from core.schemas import ObservationRecord
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import CASES, fixture_topology, seed_case
from eval.shadow_live import MAX_CALLS, RecordedLiveDriver, run_live_shadow
from eval.shadow_replay import load_inputs
from tests.test_shadow_capture import add_transaction, status
from tests.test_shadow_replan import Clock


def test_source_guard_includes_provider_configuration_and_capture_dependencies():
    from eval.shadow_live import source_manifest

    manifest = source_manifest()
    assert {"agent/nodes/reasoner.py", "core/config.py", "core/llm_usage.py", "core/evidence.py",
            "eval/shadow_capture.py", "eval/shadow_live.py", "graph/graph_client.py"} <= set(
                manifest["live_handoff_sha256"])
    assert all(len(digest) == 64 for digest in manifest["live_handoff_sha256"].values())


class FakeModel(FixedProbePolicy):
    kind = "model"
    structured_decisions = True

    def estimate_input_tokens(self, messages):
        return 1000

    def wait_for_admission(self, **kwargs):
        return True

    def complete(self, messages, **kwargs):
        decision = json.loads(super().complete(messages, **kwargs).content)
        return DriverReply(json.dumps({"decision": decision}),
                           {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})


@pytest.fixture
def trial(tmp_path):
    audit = tmp_path / "audit"
    store = seed_case(audit / "evidence.sqlite", CASES["payment"][0])
    edges = fixture_topology(datetime.now(timezone.utc)).edges
    from core.registry import DEFAULT_DEPLOYMENT

    export = {"services": list(DEFAULT_DEPLOYMENT.by_name), "edges": list(edges)}
    clock, output = Clock(), tmp_path / "trial"

    def refresh(seconds, *, change=None):
        clock.value += seconds
        origin = load_inputs(output / "initial/inputs.json")
        stamp = datetime.now(timezone.utc)
        observations = []
        for row in origin.records:
            if row.kind != "component":
                continue
            payload = {**row.payload, "evidence_id": "OBS-" + uuid4().hex,
                       "measured_at": stamp, "collected_at": stamp}
            if change == "replacement" and payload["service_id"] == "paymentservice":
                payload["instance_id"] = "f" * 64
            if change == "state" and payload["service_id"] == "paymentservice":
                payload.update(status="HEALTHY", detail="Built-in health probe: HEALTHY")
            observations.append(ObservationRecord.model_validate(payload))
        store.append_observations(observations)
        add_transaction(store, outcome="healthy" if change == "recovered" else "failed",
                        stamp=datetime.now(timezone.utc), identifier=uuid4().hex)
        # Logical elapsed time must not exceed the genuinely advanced capture cutoff.
        clock.value -= seconds

    def run(**kwargs):
        return run_live_shadow(audit, output, approve_live_transfer=True,
            status_reader=status, topology_loader=lambda: export, driver_factory=FakeModel,
            clock=clock, sleep=refresh, **kwargs)

    return SimpleNamespace(audit=audit, store=store, export=export, clock=clock,
                           output=output, refresh=refresh, run=run)


def test_approval_required_before_any_output_or_client(tmp_path):
    factory = Mock()
    for flag in (False, None, 1, "yes"):
        with pytest.raises(ValueError, match="Explicit approval"):
            run_live_shadow(tmp_path / "audit", tmp_path / "run", approve_live_transfer=flag,
                            driver_factory=factory)
    factory.assert_not_called()
    assert not (tmp_path / "run").exists()


def test_full_handoff_uses_real_capture_code_and_distinct_new_observations(trial):
    result = trial.run()
    assert result["outcome"] == "consistent", result
    assert result["real_model_calls"] == 2
    assert result["usage_complete"] and result["known_reported_tokens"] == 220
    assert result["fresh_consistency_verified"] and result["candidate_screened"]
    assert result["unused_allowance_closed"] == MAX_CALLS - 2 and result["allowance_closed"]
    assert not result["execution_authorized"] and not result["recovery_verified"]
    state = json.loads((trial.output / "investigation.json").read_text())
    assert state["current_candidate"] is None
    assert state["current_consistency_check"]["outcome"] == "consistent"
    assert state["current_consistency_check"]["read_budget"]["session"]["calls_used"] == 8
    initial = load_inputs(trial.output / "initial/inputs.json")
    fresh = load_inputs(trial.output / "refresh/inputs.json")
    assert fresh.as_of > initial.as_of and fresh.symptom_evidence_id != initial.symptom_evidence_id
    assert os.stat(trial.output).st_mode & 0o777 == 0o700
    assert all(os.stat(path).st_mode & 0o777 == 0o600 for path in trial.output.glob("*.json"))
    with pytest.raises(FileExistsError):
        trial.run()


@pytest.mark.parametrize("outcome", ["healthy", "unknown"])
def test_no_failed_current_transaction_never_constructs_a_provider(trial, outcome):
    add_transaction(trial.store, outcome=outcome)
    factory = Mock(side_effect=AssertionError("must not construct"))
    result = run_live_shadow(trial.audit, trial.output, approve_live_transfer=True,
        status_reader=status, topology_loader=lambda: trial.export, driver_factory=factory)
    assert result["outcome"] == "not_eligible" and result["real_model_calls"] == 0
    factory.assert_not_called()
    assert not (trial.output / "model-epoch.json").exists()


@pytest.mark.parametrize("change,reason", [
    ("replacement", "runtime_instance_changed"), ("state", "scope_state_changed"),
    ("recovered", "latest_transaction_not_failed"),
])
def test_changes_in_new_evidence_fail_without_new_reasoning_or_retarget(trial, change, reason):
    result = run_live_shadow(trial.audit, trial.output, approve_live_transfer=True,
        status_reader=status, topology_loader=lambda: trial.export, driver_factory=FakeModel,
        clock=trial.clock, sleep=lambda seconds: trial.refresh(seconds, change=change))
    assert result["outcome"] == "failed" and result["reason"] == reason
    assert result["candidate_screened"] and result["real_model_calls"] == 2
    assert not result["fresh_consistency_verified"]
    state = json.loads((trial.output / "investigation.json").read_text())
    assert state["current_candidate"] is None and state["current_consistency_check"] is None


def test_no_new_transaction_times_out_passively_and_retires_candidate(trial):
    def sleep(seconds):
        trial.clock.value += seconds

    result = run_live_shadow(trial.audit, trial.output, approve_live_transfer=True,
        status_reader=status, topology_loader=lambda: trial.export, driver_factory=FakeModel,
        clock=trial.clock, sleep=sleep)
    assert result["reason"] == "new_transaction_not_observed"
    assert not result["fresh_consistency_verified"] and result["real_model_calls"] == 2
    assert not (trial.output / "refresh").exists()
    state = json.loads((trial.output / "investigation.json").read_text())
    assert state["current_candidate"] is None and not state["candidate_rechecks"]


def test_active_delivery_prevents_initial_capture_and_provider_creation(trial):
    factory = Mock()
    result = run_live_shadow(trial.audit, trial.output, approve_live_transfer=True,
        status_reader=lambda: {**status(), "delivery_enabled": True}, driver_factory=factory)
    assert result["reason"] == "invalid_or_unavailable_live_handoff" and result["real_model_calls"] == 0
    factory.assert_not_called()


def test_safeguard_is_rechecked_after_rate_wait_and_latches(tmp_path):
    driver, safeguard = Mock(), Mock()
    driver.kind, driver.structured_decisions = "model", True
    recorded = RecordedLiveDriver(driver, tmp_path, safeguard)
    recorded.wait_for_admission(estimated_input_tokens=1, output_tokens=1, remaining_seconds=1)
    safeguard.side_effect = ValueError("private source detail")
    with pytest.raises(ProviderFailure) as failure:
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    assert failure.value.category == "live_safeguard_failed"
    assert "private" not in str(failure.value.diagnostics)
    safeguard.side_effect = None
    with pytest.raises(ProviderFailure):
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    driver.complete.assert_not_called()
    assert recorded.calls == 0


@pytest.mark.parametrize("failure", [
    ProviderFailure("provider_rate_limited", {"http_status": 429}),
    RuntimeError("do not retain this raw SDK exception"), KeyboardInterrupt(),
])
def test_dispatch_failure_retains_attempt_and_never_retries(tmp_path, failure):
    driver = Mock(kind="model", structured_decisions=True)
    driver.complete.side_effect = failure
    recorded = RecordedLiveDriver(driver, tmp_path, lambda: None)
    with pytest.raises(type(failure)):
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    with pytest.raises(ProviderFailure):
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    assert recorded.calls == 1 and recorded.replies == 0 and driver.complete.call_count == 1
    receipt = (tmp_path / "call-01-failed.json").read_text()
    assert "do not retain" not in receipt
    assert json.loads(receipt)["usage_complete"] is False


def test_unknown_usage_latches_stop_and_known_usage_reconciles(tmp_path):
    driver = FakeModel()
    driver.complete = Mock(return_value=DriverReply("{}", {"total_tokens": 10}))
    recorded = RecordedLiveDriver(driver, tmp_path, lambda: None)
    recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    with pytest.raises(ProviderFailure):
        recorded.wait_for_admission(estimated_input_tokens=1, output_tokens=1, remaining_seconds=1)
    assert recorded.calls == 1 and recorded.replies == 0 and recorded.stopped == "usage_unknown"


def test_four_call_ceiling_is_not_a_retry_allowance(tmp_path):
    driver = FakeModel()
    driver.complete = Mock(return_value=DriverReply("{}", {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}))
    recorded = RecordedLiveDriver(driver, tmp_path, lambda: None)
    for _ in range(MAX_CALLS):
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    with pytest.raises(ProviderFailure) as error:
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    assert error.value.category == "approved_dispatch_ceiling"
    assert recorded.calls == MAX_CALLS and recorded.known_tokens == 3 * MAX_CALLS


def test_interrupted_model_persists_report_and_closes_allowance(trial):
    driver = FakeModel()
    driver.complete = Mock(side_effect=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run_live_shadow(trial.audit, trial.output, approve_live_transfer=True,
            status_reader=status, topology_loader=lambda: trial.export, driver_factory=lambda: driver,
            clock=trial.clock)
    result = json.loads((trial.output / "result.json").read_text())
    assert result["outcome"] == "interrupted" and result["real_model_calls"] == 1
    assert not result["usage_complete"] and result["allowance_closed"]
    assert (trial.output / "investigation.json").exists()


def test_source_change_before_dispatch_blocks_sdk(trial, monkeypatch):
    import eval.shadow_live as live

    original = live.source_manifest
    driver = FakeModel()

    def factory():
        monkeypatch.setattr(live, "source_manifest", lambda: {**original(), "changed": True})
        return driver

    driver.complete = Mock()
    result = run_live_shadow(trial.audit, trial.output, approve_live_transfer=True,
        status_reader=status, topology_loader=lambda: trial.export, driver_factory=factory, clock=trial.clock)
    assert result["real_model_calls"] == 0 and not result["fresh_consistency_verified"]
    driver.complete.assert_not_called()


def test_failure_to_write_receipt_cannot_dispatch_or_charge_a_call(tmp_path, monkeypatch):
    import eval.shadow_live as live

    driver = Mock(kind="model", structured_decisions=True)
    recorded = RecordedLiveDriver(driver, tmp_path, lambda: None)
    monkeypatch.setattr(live, "_write_new", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        recorded.complete([], max_output_tokens=10, timeout_seconds=1)
    assert recorded.calls == 0
    driver.complete.assert_not_called()
