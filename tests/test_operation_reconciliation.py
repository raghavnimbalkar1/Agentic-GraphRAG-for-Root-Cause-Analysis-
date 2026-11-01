"""Synthetic retained evidence, real temporary SQLite, mocked runtime. No live probes or repairs."""

from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from docker.errors import NotFound
import pytest

from agent import procedure_execution as execution
from agent.tools import bound_executor
from agent.tools import operation_reconciliation as module
from core import evidence as evidence_module
from core import operation_journal as journal_module
from core.evidence import EvidenceStore
from core.operation_journal import AttemptIdentity, AttemptLease, JournalDenied, OperationJournal, ReconciliationLease
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation
from simulation.operation_review import main
from tests.test_bound_execution import factory as factory, issue
from tests.test_bound_recovery import application

RETAINED = module._retained
RUNTIME = module._runtime


@pytest.fixture
def case_factory(factory, monkeypatch, tmp_path):
    def make(relative="container/restart.sh", *, preparing=False, cleanup_failed=False):
        case = factory(relative)
        monkeypatch.setattr(journal_module, "_now", case.wall)
        monkeypatch.setattr(module, "utc_now", case.wall)
        monkeypatch.setattr(module, "monotonic", lambda: case.clock.value)
        case.grant = issue(case)
        if preparing:
            case.grant.begin()
            case.grant._lease.close()  # Simulate owner loss before any Docker work.
        else:
            if cleanup_failed:
                case.box.remove.side_effect = TimeoutError("Synthetic cleanup unknown")
            elif case.policy.operation == "throttle_cpu":
                case.container.update.side_effect = TimeoutError("Synthetic unknown outcome")
            else:
                case.container.restart.side_effect = TimeoutError("Synthetic unknown outcome")
            assert bound_executor.execute_bound_procedure(case.grant, case.graph)["state"] == (
                "not_dispatched" if cleanup_failed else "effect_uncertain")
        case.store = execution._operation_journal()
        case.clock.value += 180
        case.readonly = Mock()
        case.snapshot = case.store.inspect(receipt=True)[0]
        case.basis = case.snapshot["receipt"]["reconciliation_basis"]
        case.runtimes = {}
        for name, identifier in case.basis["instances"].items():
            target = Mock()
            target.name, target.id = name, identifier
            target.attrs = {"Config": {"Labels": {REGISTRY.ownership_label: REGISTRY.ownership_value}},
                "State": {"Running": True, "Paused": False, "Restarting": False,
                          "StartedAt": (case.origin.as_of - timedelta(seconds=60)).isoformat()},
                "NetworkSettings": {"Networks": {REGISTRY.network: {"NetworkID": "a" * 64, "IPAddress": "172.20.0.2"}}},
                "HostConfig": {"CpuQuota": -1}, "Image": "sha256:" + "b" * 64}
            case.runtimes[identifier] = target

        def get(identifier):
            if identifier in case.runtimes:
                return case.runtimes[identifier]
            raise NotFound("Absent")

        case.readonly.containers.get.side_effect = get
        monkeypatch.setattr(module, "_docker_client", Mock(return_value=case.readonly))
        monkeypatch.setattr(module, "_graph_client", Mock(return_value=case.graph))
        case.graph.get_service_snapshot.return_value = {name: {"status": "HEALTHY", "age_seconds": 1}
                                                        for name in REGISTRY.by_name}
        case.collector = {"schema_version": 2, "state": "observe_only", "heartbeat_fresh": True,
            "sensing_ready": True, "graph_synced": True, "delivery_enabled": False,
            "evidence_store": {"enabled": True, "state": "ready"}}
        monkeypatch.setattr("core.telemetry.collector_status", Mock(side_effect=lambda *a, **k: case.collector))
        from core.config import settings

        monkeypatch.setattr(settings, "audit_dir", tmp_path / "retained")
        monkeypatch.setattr(evidence_module, "time", SimpleNamespace(time=lambda: case.wall().timestamp()))
        case.evidence = EvidenceStore(settings.audit_dir / "evidence.sqlite")
        for name, identifier in case.basis["instances"].items():
            samples = [ObservationRecord(schema_version=2, service_id=name, instance_id=identifier,
                measured_at=case.wall() - timedelta(seconds=offset), collected_at=case.wall() - timedelta(seconds=offset),
                status="HEALTHY", quality="observed", detail="Synthetic software-test measurement",
                environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest)
                       for offset in (10, 5)]
            case.evidence.append_observations(samples)
        transactions = []
        for offset in (40, 5):
            started = case.wall() - timedelta(seconds=offset)
            transactions.append(SymptomObservation(environment_id=REGISTRY.environment_id,
                registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest, started_at=started,
                measured_at=started + timedelta(seconds=0.1), collected_at=started + timedelta(seconds=0.2),
                outcome="healthy", quality="observed", steps=application()["steps"]))
        with closing(case.evidence._connect()) as db, db:
            for item in transactions:
                case.evidence._put(db, item, "symptom")
        return case
    return make


def reconcile(case, *, close=False, **kwargs):
    row = case.store.inspect()[0]
    return module.reconcile_attempt(case.store, row["attempt_id"], expected_version=row["version"],
        approve_runtime_inspection=True, approve_current_state_closure=close, **kwargs)


def mutate_retained(monkeypatch, transform):
    def read(scope, cutoff):
        result = RETAINED(scope, cutoff)
        transform(result)
        return result
    monkeypatch.setattr(module, "_retained", read)


def assert_observation_only(case):
    case.readonly.containers.create.assert_not_called()
    case.readonly.containers.run.assert_not_called()
    for target in case.runtimes.values():
        target.restart.assert_not_called()
        target.update.assert_not_called()
        target.exec_run.assert_not_called()
        target.remove.assert_not_called()
    case.graph.update_service_observations.assert_not_called()
    case.graph.reset_all_to_healthy.assert_not_called()


def test_passive_preview_retains_unknown_history_and_does_not_release_barrier(case_factory):
    case = case_factory()
    result = reconcile(case)
    assert result["outcome"] == "observed_healthy", result
    assert not result["current_state_closed"] and not result["recovery_verified"] and not result["prior_effect_proven"]
    assert result["new_application_probes"] == result["real_model_calls"] == 0
    row = case.store.inspect(receipt=True)[0]
    assert row["stage"] == "reconciled_observed_healthy" and not row["closed"]
    assert row["receipt"]["state"] == "effect_uncertain" and not row["receipt"]["operation_confirmed"]
    assert row["receipt"]["reconciliations"][-1]["outcome"] == "observed_healthy"
    with pytest.raises(JournalDenied, match="terminal_review_not_eligible"):
        case.store.acknowledge_terminal(row["attempt_id"], expected_version=row["version"], approve_review=True)
    assert_observation_only(case)


def test_explicit_current_state_closure_rechecks_and_preserves_old_incident_uniqueness(case_factory):
    case = case_factory()
    result = reconcile(case, close=True)
    assert result["outcome"] == "observed_healthy" and result["current_state_closed"]
    row = case.store.inspect(receipt=True)[0]
    assert row["stage"] == "operator_closed" and row["closed"]
    assert row["receipt"]["operator_review"]["kind"] == "observed-current-state-not-effect-proof"
    assert row["receipt"]["state"] == "effect_uncertain"
    identity = journal_module.AttemptIdentity.model_validate(row["identity"])
    with pytest.raises(JournalDenied, match="incident_or_trigger_already_attempted"):
        case.store.reserve(identity, {})
    assert bound_executor.execute_bound_procedure(result, case.graph)["request_outcome"] == "rejected"
    assert_observation_only(case)


@pytest.mark.parametrize("approval", [False, 1, "true", None])
def test_approval_is_literal_and_precedes_any_runtime_inspection(case_factory, approval):
    case = case_factory()
    result = module.reconcile_attempt(case.store, case.snapshot["attempt_id"], expected_version=case.snapshot["version"],
                                      approve_runtime_inspection=approval)
    assert result["outcome"] == "rejected"
    module._docker_client.assert_not_called()
    assert case.store.inspect()[0]["version"] == case.snapshot["version"]


def test_stale_version_and_active_writer_cannot_reconcile(case_factory):
    case = case_factory()
    args = {"expected_version": case.snapshot["version"] - 1, "approve_runtime_inspection": True}
    assert module.reconcile_attempt(case.store, case.snapshot["attempt_id"], **args)["outcome"] == "rejected"
    db, stream = case.store._open()
    try:
        assert reconcile(case)["reason"] == "writer_active"
    finally:
        db.close()
        stream.close()
    module._docker_client.assert_not_called()


def test_quiet_interval_is_required_without_waiting_or_running_probes(case_factory):
    case = case_factory()
    case.clock.value -= 80
    result = reconcile(case)
    assert result["reason"] == "quiet_interval_not_elapsed" and not result["current_state_closed"]
    module._docker_client.assert_not_called()


def test_temporary_containment_cannot_be_closed_as_current_recovery(case_factory):
    case = case_factory("adservice/throttle.sh")
    result = reconcile(case, close=True)
    assert result["reason"] == "temporary_mitigation_requires_separate_review"
    assert not case.store.inspect()[0]["closed"]
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("problem", ["missing", "single", "duplicate_time", "unknown", "replacement", "future", "stale",
    "truncated", "conflict", "registry", "reused_id", "wrong_source"])
def test_unreliable_component_evidence_keeps_barrier(case_factory, monkeypatch, problem):
    case = case_factory()

    def alter(data):
        name = next(iter(data["components"]))
        window = data["components"][name]
        if problem == "missing":
            del data["components"][name]
        elif problem == "single":
            window["samples"] = window["samples"][:1]
        elif problem == "duplicate_time":
            window["samples"][0]["measured_at"] = window["samples"][1]["measured_at"]
            window["samples"][0]["collected_at"] = window["samples"][1]["collected_at"]
        elif problem == "unknown":
            window["samples"][-1].update(status="UNKNOWN", quality="unknown")
        elif problem == "replacement":
            window["samples"][-1]["instance_id"] = "f" * 64
        elif problem in {"future", "stale"}:
            for row in window["samples"]:
                row["measured_at"] = (case.wall() + timedelta(seconds=1 if problem == "future" else -31)).isoformat()
        elif problem == "truncated":
            window["truncated"] = True
        elif problem == "conflict":
            window["summary"]["latest_conflict"] = True
        elif problem == "registry":
            window["registry_digest"] = "f" * 64
        elif problem == "wrong_source":
            window["source"] = "not-approved"
        else:
            window["samples"][-1]["evidence_id"] = case.basis["component_ids"][name][0]

    mutate_retained(monkeypatch, alter)
    result = reconcile(case, close=True)
    assert result["outcome"] == "inconclusive" and not result["current_state_closed"]
    assert not case.store.inspect()[0]["closed"]
    assert_observation_only(case)


@pytest.mark.parametrize("problem", ["missing", "single", "unknown", "failed", "cleanup", "duplicate_id", "duplicate_time",
    "old_request", "truncated", "missing_marker", "reused_trigger", "unknown_recent_peer"])
def test_passive_checkout_evidence_must_support_current_state(case_factory, monkeypatch, problem):
    case = case_factory()

    def alter(data):
        history = data["transactions"]
        rows = history["observations"]
        if problem == "missing":
            rows.clear()
        elif problem == "single":
            del rows[1:]
        elif problem == "unknown":
            rows[-1].update(outcome="unknown", quality="unknown")
        elif problem == "failed":
            rows[-1].update(outcome="failed")
            rows[-1]["steps"][3].update(success=False, status_code=500, marker_found=False)
        elif problem == "cleanup":
            rows[-1]["steps"][-1].update(success=False, status_code=500)
        elif problem == "duplicate_id":
            rows[-1]["evidence_id"] = rows[0]["evidence_id"]
        elif problem == "duplicate_time":
            rows[-1].update({key: rows[0][key] for key in ("started_at", "measured_at", "collected_at")})
        elif problem == "old_request":
            rows[0]["started_at"] = (case.origin.as_of + timedelta(seconds=119)).isoformat()
        elif problem == "truncated":
            history["truncated"] = True
        elif problem == "missing_marker":
            rows[-1]["steps"][3]["marker_found"] = None
        elif problem == "reused_trigger":
            rows[-1]["evidence_id"] = case.snapshot["identity"]["trigger_evidence_id"]
        else:
            peer = deepcopy(rows[-1])
            peer.update(evidence_id="SYMOBS-" + uuid4().hex, outcome="unknown", quality="unknown")
            rows.append(peer)

    mutate_retained(monkeypatch, alter)
    result = reconcile(case, close=True)
    assert result["outcome"] == "inconclusive" and not result["current_state_closed"]
    assert not case.store.inspect()[0]["closed"]
    assert_observation_only(case)


@pytest.mark.parametrize("problem", ["owner", "name", "id", "stopped", "paused", "restarting", "network", "new_start"])
def test_runtime_must_still_be_owned_identified_and_stable(case_factory, problem):
    case = case_factory()
    target = next(iter(case.runtimes.values()))
    if problem == "owner":
        target.attrs["Config"]["Labels"].clear()
    elif problem == "name":
        target.name = "other-service"
    elif problem == "id":
        target.id = "f" * 64
    elif problem == "network":
        target.attrs["NetworkSettings"]["Networks"].clear()
    elif problem == "new_start":
        target.attrs["State"]["StartedAt"] = (case.wall() - timedelta(seconds=1)).isoformat()
    else:
        target.attrs["State"][{"stopped": "Running", "paused": "Paused", "restarting": "Restarting"}[problem]] = problem != "stopped"
    result = reconcile(case, close=True)
    assert result["outcome"] == "inconclusive" and not result["current_state_closed"]
    assert_observation_only(case)


@pytest.mark.parametrize("owned", [True, False])
def test_request_container_presence_is_reported_without_cleanup(case_factory, owned):
    case = case_factory()
    intent = case.snapshot["receipt"]["sandbox_intent"]
    box = Mock()
    box.name = intent["name"]
    box.attrs = {"Image": intent["image_id"], "Config": {"Labels": {
        "rca.role": "bound-sop-intent", "rca.attempt": intent["nonce"] if owned else "not-owned"}}}
    get = case.readonly.containers.get.side_effect
    case.readonly.containers.get.side_effect = lambda name: box if name == box.name else get(name)
    result = reconcile(case, close=True)
    assert result["reason"] == "request_sandbox_present_or_identity_conflict"
    assert result["sandbox_checks"][-1]["basis"] == ("owned-request-container-present" if owned else "request-container-identity-conflict")
    box.remove.assert_not_called()
    box.stop.assert_not_called()
    box.exec_run.assert_not_called()


def test_preparing_without_container_create_admission_can_be_observed(case_factory):
    case = case_factory(preparing=True)
    result = reconcile(case, close=True)
    assert result["outcome"] == "observed_healthy" and result["current_state_closed"]
    assert all(row["basis"] == "v2-no-request-create-admission" for row in result["sandbox_checks"])
    assert_observation_only(case)


@pytest.mark.parametrize("problem", ["delivery", "collector", "graph", "catalog", "topology"])
def test_context_drift_or_unavailability_is_inconclusive(case_factory, problem):
    case = case_factory()
    if problem == "delivery":
        case.collector["delivery_enabled"] = True
    elif problem == "collector":
        case.collector["heartbeat_fresh"] = False
    elif problem == "graph":
        case.graph.get_service_snapshot.return_value = {}
    elif problem == "catalog":
        case.graph._run.return_value = []
    else:
        case.graph.get_dependency_topology.return_value = {"services": list(REGISTRY.by_name), "edges": [["frontend", "adservice"]]}
    assert reconcile(case, close=True)["outcome"] == "inconclusive"
    assert not case.store.inspect()[0]["closed"]


def test_runtime_change_between_passes_blocks_closure(case_factory, monkeypatch):
    case = case_factory()
    calls = 0

    def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            next(iter(case.runtimes.values())).attrs["HostConfig"]["CpuQuota"] = 50000
        return RUNTIME(*args, **kwargs)

    monkeypatch.setattr(module, "_runtime", read)
    assert reconcile(case, close=True)["reason"] == "runtime_changed_during_reconciliation"


def test_client_close_delay_cannot_extend_evidence_lifetime(case_factory):
    case = case_factory()
    case.readonly.close.side_effect = lambda: setattr(case.clock, "value", case.clock.value + 6)
    result = reconcile(case, close=True)
    assert result["outcome"] == "inconclusive" and result["reason"] == "reconciliation_context_expired"
    assert not case.store.inspect()[0]["closed"]


def test_unknown_receipt_write_keeps_closure_status_explicitly_unknown(case_factory, monkeypatch):
    case = case_factory()
    finish = ReconciliationLease.finish

    def lost(self, *args, **kwargs):
        finish(self, *args, **kwargs)
        raise JournalDenied("reconciliation_finish_unconfirmed")

    monkeypatch.setattr(ReconciliationLease, "finish", lost)
    result = reconcile(case, close=True)
    assert result["outcome"] == "inconclusive" and result["current_state_closed"] is None
    assert case.store.inspect()[0]["closed"]  # The committed bookkeeping is not silently repeated.


def test_interrupted_review_is_retained_and_has_a_fixed_explicit_budget(case_factory, monkeypatch):
    case = case_factory()
    monkeypatch.setattr(module, "_retained", Mock(side_effect=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        reconcile(case)
    assert case.store.inspect(receipt=True)[0]["receipt"]["reconciliations"][-1]["reason"] == "reconciliation_interrupted"
    monkeypatch.setattr(module, "_retained", Mock(side_effect=OSError("PRIVATE")))
    assert reconcile(case)["outcome"] == "inconclusive"
    assert reconcile(case)["outcome"] == "inconclusive"
    result = reconcile(case)
    assert result["outcome"] == "rejected" and result["reason"] == "reconciliation_budget_exhausted"
    assert len(case.store.inspect(receipt=True)[0]["receipt"]["reconciliations"]) == 3


CRASH_REVIEW = """
from datetime import datetime
import json, os, sys
from pathlib import Path
from core import operation_journal as module
module._now = lambda: datetime.fromisoformat(sys.argv[4])
store = module.OperationJournal(Path(sys.argv[1]))
lease = store.reserve(module.AttemptIdentity.model_validate(json.loads(sys.argv[2])), json.loads(sys.argv[3]))
if sys.argv[5] == 'during-review':
    lease.close()
    store.begin_reconciliation(lease.identifier, expected_version=1, approve_inspection=True)
else:
    receipt = json.loads(sys.argv[3])
    receipt.update(state='effect_attempted', action_dispatched=True, operation_confirmed=False)
    lease.record('effect_attempted', receipt)
os._exit(71)
"""


@pytest.mark.parametrize("phase", ["before-ack", "during-review"])
def test_restart_can_review_current_state_without_recreating_execution_owner(case_factory, tmp_path, phase):
    case = case_factory()
    path = tmp_path / "crashed.sqlite"
    receipt = deepcopy(case.snapshot["receipt"])
    receipt.update(state="preparing", action_dispatched=False, operation_confirmed=False)
    child = subprocess.run([sys.executable, "-c", CRASH_REVIEW, str(path), json.dumps(case.snapshot["identity"]),
        json.dumps(receipt), case.origin.as_of.isoformat(), phase], capture_output=True, text=True, timeout=20)
    assert child.returncode == 71, child.stderr
    case.store = OperationJournal(path)
    result = reconcile(case, close=True)
    assert result["outcome"] == "observed_healthy" and result["current_state_closed"]
    assert not result["prior_effect_proven"] and not result["execution_authorized"]
    row = case.store.inspect(receipt=True)[0]
    if phase == "during-review":
        assert row["receipt"]["reconciliations"][0]["reason"] == "previous_reconciliation_interrupted"
    assert_observation_only(case)


def test_cli_without_runtime_approval_performs_no_inspection(case_factory, capsys):
    case = case_factory()
    assert main(["--journal", str(case.store.path), "reconcile", case.snapshot["attempt_id"],
                 "--version", str(case.snapshot["version"])]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "rejected"
    module._docker_client.assert_not_called()


def test_request_create_metadata_is_durable_before_sdk_create(factory):
    case = factory()
    store = execution._operation_journal()

    def create(**kwargs):
        row = store.inspect(receipt=True)[0]
        intent = row["receipt"]["sandbox_intent"]
        assert row["stage"] == "preparing" and intent["name"] == kwargs["name"]
        assert intent["image_id"] == kwargs["image"] and intent["nonce"] == kwargs["labels"]["rca.attempt"]
        assert row["receipt"]["reconciliation_basis"]["source_kind"] == "retained-observation"
        return case.box

    case.client.containers.create.side_effect = create
    assert bound_executor.execute_bound_procedure(issue(case), case.graph)["operation_confirmed"]


@pytest.mark.parametrize("committed", [False, True])
def test_request_admission_write_loss_cannot_create_container(factory, monkeypatch, committed):
    case = factory()
    record = AttemptLease.record

    def lost(self, stage, receipt):
        if stage == "preparing" and receipt.get("sandbox_intent") and receipt["sandbox_cleaned"] is None:
            if committed:
                record(self, stage, receipt)
            raise JournalDenied("journal_write_unconfirmed")
        return record(self, stage, receipt)

    monkeypatch.setattr(AttemptLease, "record", lost)
    result = bound_executor.execute_bound_procedure(issue(case), case.graph)
    assert not result["request_action_dispatched"]
    case.client.containers.create.assert_not_called()
    case.container.restart.assert_not_called()


def test_unknown_cleanup_requires_observed_absence_before_current_state_closure(case_factory):
    case = case_factory(cleanup_failed=True)
    with pytest.raises(JournalDenied, match="request_sandbox_reconciliation_required"):
        case.store.acknowledge_terminal(case.snapshot["attempt_id"], expected_version=case.snapshot["version"], approve_review=True)
    result = reconcile(case, close=True)
    assert result["current_state_closed"] and not result["prior_effect_proven"]


def test_explicit_orphan_removal_keeps_barrier_and_requires_new_current_state_witnesses(case_factory, monkeypatch):
    from agent.tools import sandbox_cleanup
    from tests.test_request_sandbox_cleanup import request_box

    case = case_factory(cleanup_failed=True)
    monkeypatch.setattr(sandbox_cleanup, "utc_now", case.wall)
    monkeypatch.setattr(sandbox_cleanup, "monotonic", lambda: case.clock.value)
    monkeypatch.setattr(sandbox_cleanup, "_docker_client", Mock(return_value=case.readonly))
    intent = case.snapshot["receipt"]["sandbox_intent"]
    box, present = request_box(intent), {"value": True}
    get = case.readonly.containers.get.side_effect

    def lookup(key):
        if key in {intent["name"], box.id} and present["value"]:
            return box
        return get(key)

    case.readonly.containers.get.side_effect = lookup
    case.readonly.api.remove_container.side_effect = lambda *a, **k: present.update(value=False)
    result = sandbox_cleanup.cleanup_request_sandbox(case.store, case.snapshot["attempt_id"],
        expected_version=case.snapshot["version"], approve_cleanup=True)
    assert result["outcome"] == "absent" and not result["current_state_closed"]
    assert reconcile(case, close=True)["reason"] == "quiet_interval_not_elapsed"
    # Time alone is insufficient: retained component and checkout samples are now stale.
    case.clock.value += 121
    assert reconcile(case, close=True)["outcome"] == "inconclusive"
    # New rows go through the real EvidenceStore, not fabricated reader metadata.
    case.clock.value += 10
    later = case.wall()
    for name, identifier in case.basis["instances"].items():
        case.evidence.append_observations([ObservationRecord(schema_version=2, service_id=name, instance_id=identifier,
            measured_at=later - timedelta(seconds=offset), collected_at=later - timedelta(seconds=offset),
            status="HEALTHY", quality="observed", detail="Synthetic later observation",
            environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest)
            for offset in (5, 1)])
    with closing(case.evidence._connect()) as db, db:
        for offset in (5, 1):
            started = later - timedelta(seconds=offset)
            item = SymptomObservation(environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
                registry_digest=REGISTRY.digest, started_at=started, measured_at=started + timedelta(seconds=.1),
                collected_at=started + timedelta(seconds=.2), outcome="healthy", quality="observed", steps=application()["steps"])
            case.evidence._put(db, item, "symptom")
    assert reconcile(case, close=True)["current_state_closed"]
    receipt = case.store.inspect(receipt=True)[0]["receipt"]
    assert receipt["sandbox_cleaned"] is False and receipt["state"] == "not_dispatched"
    assert receipt["sandbox_cleanup"]["report"]["outcome"] == "absent"
    case.readonly.api.remove_container.assert_called_once_with(box.id, force=True, v=False)
    assert_observation_only(case)
    row = case.store.inspect(receipt=True)[0]
    assert row["receipt"]["sandbox_cleaned"] is False  # Do not rewrite historical cleanup uncertainty.
    assert row["receipt"]["state"] == "not_dispatched"
    assert_observation_only(case)


@pytest.mark.parametrize("problem", ["legacy_policy", "synthetic", "missing_scope", "wrong_instances"])
def test_unreconstructable_old_or_invalid_basis_fails_before_runtime(case_factory, tmp_path, problem):
    case = case_factory()
    receipt = deepcopy(case.snapshot["receipt"])
    if problem == "legacy_policy":
        receipt["execution_policy"] = "operator-controlled-v1"
    elif problem == "synthetic":
        receipt["reconciliation_basis"]["source_kind"] = "synthetic"
    elif problem == "missing_scope":
        receipt["reconciliation_basis"]["scope"].pop()
    else:
        receipt["reconciliation_basis"]["instances"][case.target] = "f" * 64
    case.store = OperationJournal(tmp_path / "unsupported.sqlite")
    owner = case.store.reserve(AttemptIdentity.model_validate(case.snapshot["identity"]), receipt)
    owner.close()
    assert reconcile(case, close=True)["outcome"] == "inconclusive"
    module._docker_client.assert_not_called()


def test_preview_is_not_reused_as_closure_when_evidence_changes(case_factory, monkeypatch):
    case = case_factory()
    assert reconcile(case)["outcome"] == "observed_healthy"

    def unknown(data):
        next(iter(data["components"].values()))["samples"][-1].update(status="UNKNOWN", quality="unknown")

    mutate_retained(monkeypatch, unknown)
    result = reconcile(case, close=True)
    assert result["outcome"] == "inconclusive" and not result["current_state_closed"]
    history = case.store.inspect(receipt=True)[0]["receipt"]["reconciliations"]
    assert len(history) == 2 and history[0]["outcome"] == "observed_healthy" and history[1]["outcome"] == "inconclusive"
    assert module._docker_client.call_count == 2


def test_inspection_deadline_stops_additional_runtime_reads(case_factory):
    case = case_factory()
    original = case.readonly.containers.get.side_effect

    def slow(identifier):
        case.clock.value += 8
        return original(identifier)

    case.readonly.containers.get.side_effect = slow
    result = reconcile(case, close=True)
    assert result["reason"] == "reconciliation_deadline_exhausted" and not result["current_state_closed"]
    assert case.readonly.containers.get.call_count <= 2


def test_current_state_review_never_calls_canary_health_probes_or_model(case_factory, monkeypatch):
    case = case_factory()
    forbidden = Mock(side_effect=AssertionError("No new probes or provider requests"))
    monkeypatch.setattr("simulation.application_canary.check_application", forbidden)
    monkeypatch.setattr("core.health.observe_many", forbidden)
    monkeypatch.setattr("agent.shadow_provider.configured_groq_driver", forbidden)
    assert reconcile(case)["outcome"] == "observed_healthy"
    forbidden.assert_not_called()


def test_cli_denial_needs_no_project_settings_or_runtime_initialization(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(root), "HOME": str(tmp_path)}
    child = subprocess.run([sys.executable, "-m", "simulation.operation_review", "--journal", str(tmp_path / "none.sqlite"),
        "reconcile", "ATT-test", "--version", "1"], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert child.returncode == 1 and json.loads(child.stdout)["reason"] == "explicit_runtime_inspection_required", child.stderr
    assert not (tmp_path / "none.sqlite").exists() and not (tmp_path / "audit").exists()


def test_request_reservation_latency_cannot_use_an_old_sandbox_timeout(factory, monkeypatch):
    case = factory()
    grant = issue(case)
    record = AttemptLease.record

    def slow(self, stage, receipt):
        result = record(self, stage, receipt)
        if stage == "preparing" and receipt.get("sandbox_intent") and receipt["sandbox_cleaned"] is None:
            case.clock.value += (grant.offer.expires_at - case.wall()).total_seconds() - 0.5
        return result

    monkeypatch.setattr(AttemptLease, "record", slow)
    result = bound_executor.execute_bound_procedure(grant, case.graph)
    assert not result["request_action_dispatched"]
    case.client.containers.create.assert_not_called()
    case.container.restart.assert_not_called()
