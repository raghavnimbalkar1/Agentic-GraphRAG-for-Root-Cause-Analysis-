"""Fresh synthetic epochs, actual SQLite claims and process boundaries; no provider or runtime I/O."""

from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from agent import diagnostic_continuation as module
from agent.diagnostic_continuation import continue_diagnosis, start_diagnosis
from agent.investigation import InvestigationLimits
from agent.shadow import ShadowLimits
from agent.shadow import DriverReply
from agent.shadow_budget import SharedShadowBudget
from core.operation_journal import JournalDenied, OperationJournal
from core.workflow_checkpoints import CheckpointDenied, DiagnosticCheckpoints, SERIALIZER, diagnostic_thread_id
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import save_inputs
from tests.test_operation_journal import identity
from tests.test_shadow_replan import Clock, epoch


class CountingPolicy(FixedProbePolicy):
    def __init__(self):
        self.requests = 0

    def complete(self, *args, **kwargs):
        self.requests += 1
        return super().complete(*args, **kwargs)


@pytest.fixture
def case(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    fresh = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    options = {"workflow_database": tmp_path / "workflows.sqlite",
               "operation_journal": OperationJournal(tmp_path / "operations.sqlite"), "clock": Clock()}
    started = start_diagnosis(first, CountingPolicy(), approve_start=True, **options)
    assert started["outcome"] == "checkpointed", started
    return first, fresh, options, started


def proceed(case, driver=None):
    first, fresh, options, started = case
    return continue_diagnosis(first, fresh, driver or CountingPolicy(), parent_thread_id=started["thread_id"],
                              approve_continuation=True, **options)


def store(case):
    return DiagnosticCheckpoints(case[2]["workflow_database"])


def rewrite(case, mutation):
    path = case[2]["workflow_database"]
    with closing(sqlite3.connect(path)) as db, db:
        encoded = db.execute("SELECT payload FROM diagnostic_continuations").fetchone()[0]
        payload = SERIALIZER.loads_typed(("rca-json-v1", encoded))
        mutation(payload)
        _, body = SERIALIZER.dumps_typed(payload)
        from hashlib import sha256

        db.execute("UPDATE diagnostic_continuations SET payload=?,digest=?", (body, sha256(body).hexdigest()))


def test_fresh_epoch_preserves_all_spent_counts_and_retires_old_candidate(case):
    previous = case[3]["diagnosis"]["incident_budget"]
    response = proceed(case)
    assert response["outcome"] == "checkpointed", response
    current = response["diagnosis"]["incident_budget"]
    assert current["calls_used"] == current["decisions_used"] == 4
    for key in ("calls_used", "decisions_used", "response_bytes", "admission_units", "known_reported_tokens"):
        assert current[key] >= previous[key]
    assert current["remaining_seconds"] < previous["remaining_seconds"]
    assert current["evidence_elapsed_seconds"] == 5
    assert response["historical_only"] and not response["execution_authorized"] and not response["recovery_verified"]
    assert not response["automatic_continuation_allowed"]
    candidates = response["diagnosis"]["candidates"]
    assert [(row["proposal"]["service_id"], row["state"]) for row in candidates] == [
        ("paymentservice", "expired"), ("checkoutservice", "provisional")]
    assert candidates[0]["retirement_reason"] == "process_restart"
    context = response["diagnosis"]["epochs"][-1]["report"]["initial_context"]
    assert context["continuation"]["prior_candidates"][0]["state"] == "expired"
    assert response["diagnosis"]["procedure_execution"] is None
    assert store(case).load_controller(case[3]["thread_id"])["claimed_child"] == response["thread_id"]
    parent = next(row for row in store(case).inspect() if row["thread_id"] == case[3]["thread_id"])
    assert parent["continuation_record"]["claimed_child"] == response["thread_id"]


@pytest.mark.parametrize("flag", [False, 1, "true", None])
def test_explicit_start_and_continuation_flags_precede_io(tmp_path, flag):
    options = {"workflow_database": tmp_path / "workflows.sqlite", "operation_journal": OperationJournal(tmp_path / "operations.sqlite")}
    driver = CountingPolicy()
    assert start_diagnosis(epoch(), driver, approve_start=flag, **options)["outcome"] == "rejected"
    assert continue_diagnosis(epoch(), epoch(1), driver, parent_thread_id="WF-" + "a" * 64,
                              approve_continuation=flag, **options)["outcome"] == "rejected"
    assert not driver.requests and not list(tmp_path.iterdir())


def test_duplicate_parent_is_consumed_only_once(case):
    assert proceed(case)["outcome"] == "checkpointed"
    driver = CountingPolicy()
    result = proceed(case, driver)
    assert result["reason"] == "continuation_already_claimed" and not driver.requests


def test_exhausted_aggregate_budget_cannot_start_third_epoch(case):
    completed = proceed(case)
    first, fresh, options, _ = case
    third = epoch(2, statuses={"paymentservice": "DOWN"}, previous=fresh)
    driver = CountingPolicy()
    result = continue_diagnosis(first, third, driver, parent_thread_id=completed["thread_id"], approve_continuation=True, **options)
    assert result["outcome"] == "rejected" and not driver.requests
    assert store(case).load_controller(completed["thread_id"])["claimed_child"] is None


def test_three_explicit_epochs_share_one_original_allowance(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    third = epoch(2, statuses={"paymentservice": "DOWN"}, previous=second)
    options = {"workflow_database": tmp_path / "workflows.sqlite", "operation_journal": OperationJournal(tmp_path / "operations.sqlite")}
    a = start_diagnosis(first, CountingPolicy(), approve_start=True, shadow_limits=ShadowLimits(max_decisions=6), **options)
    b = continue_diagnosis(first, second, CountingPolicy(), parent_thread_id=a["thread_id"], approve_continuation=True, **options)
    assert b["outcome"] == "checkpointed", b
    c = continue_diagnosis(first, third, CountingPolicy(), parent_thread_id=b["thread_id"], approve_continuation=True, **options)
    assert c["outcome"] == "checkpointed", c
    assert c["diagnosis"]["incident_budget"]["decisions_used"] == 6
    assert c["diagnosis"]["incident_budget"]["calls_used"] == 6
    assert c["diagnosis"]["incident_budget"]["limits"]["max_decisions"] == 6
    assert [row["state"] for row in c["diagnosis"]["candidates"]] == ["expired", "expired", "provisional"]


def test_unknown_reported_usage_cannot_create_a_continuable_receipt(tmp_path):
    class Unknown:
        kind = "scripted"

        def complete(self, *args, **kwargs):
            return DriverReply('{"action":"abstain","reason":"insufficient_evidence"}', None)

    first = epoch()
    path = tmp_path / "workflow.sqlite"
    result = start_diagnosis(first, Unknown(), workflow_database=path,
        operation_journal=OperationJournal(tmp_path / "operations.sqlite"), approve_start=True)
    assert result["outcome"] == "rejected"
    row = DiagnosticCheckpoints(path).inspect()[0]
    assert not row["usage_complete"] and not row["automatic_resume_allowed"]
    with pytest.raises(CheckpointDenied):
        DiagnosticCheckpoints(path).load_controller(row["thread_id"])


@pytest.mark.parametrize("problem", ["same_epoch", "old_origin", "rewritten", "healthy", "source_profile"])
def test_invalid_fresh_input_is_refused_before_claim_or_driver(case, problem):
    first, fresh, options, started = case
    if problem == "same_epoch":
        fresh = first
    elif problem == "old_origin":
        first = epoch(statuses={"redis-cart": "DOWN"})
    elif problem == "rewritten":
        rows = list(fresh.records)
        index = next(i for i, row in enumerate(rows) if row.payload["evidence_id"] in
                     {old.payload["evidence_id"] for old in first.records})
        rows[index] = rows[index].model_copy(update={"stored_at": rows[index].stored_at + 0.001})
        fresh = fresh.model_copy(update={"records": tuple(rows)})
    elif problem == "healthy":
        fresh = epoch(1, outcome="healthy", previous=first)
    else:
        fresh = fresh.model_copy(update={"source_kind": "retained-observation"})
    driver = CountingPolicy()
    result = continue_diagnosis(first, fresh, driver, parent_thread_id=started["thread_id"], approve_continuation=True, **options)
    assert result["outcome"] == "rejected" and not driver.requests
    assert store(case).load_controller(started["thread_id"])["claimed_child"] is None


def test_downtime_is_charged_and_expired_receipt_is_not_claimed(case, monkeypatch):
    real_now = module.utc_now
    monkeypatch.setattr(module, "utc_now", lambda: real_now() + timedelta(seconds=181))
    driver = CountingPolicy()
    assert proceed(case, driver)["outcome"] == "rejected" and not driver.requests
    assert store(case).load_controller(case[3]["thread_id"])["claimed_child"] is None


def test_backward_wall_clock_is_refused(case, monkeypatch):
    real_now = module.utc_now
    monkeypatch.setattr(module, "utc_now", lambda: real_now() - timedelta(seconds=1))
    driver = CountingPolicy()
    assert proceed(case, driver)["reason"] == "continuation_clock_invalid" and not driver.requests


def test_changed_source_profile_is_refused_without_transferring(case, monkeypatch):
    monkeypatch.setattr(module, "implementation_manifest", lambda: {"changed": True})
    driver = CountingPolicy()
    assert proceed(case, driver)["reason"] == "continuation_origin_or_implementation_changed" and not driver.requests


@pytest.mark.parametrize("problem", ["count", "remaining", "report", "checkpoint", "journal", "epoch", "seen", "grant"])
def test_manually_reencoded_bad_receipt_cannot_restore_authority_or_refund(case, problem):
    def corrupt(payload):
        snapshot = payload["state"]["snapshot"]
        if problem == "count":
            snapshot["incident_budget"]["decisions_used"] = 0
        elif problem == "remaining":
            snapshot["incident_budget"]["remaining_seconds"] = 999
        elif problem == "report":
            snapshot["epochs"][-1]["report"]["known_reported_tokens"] = 999
        elif problem == "checkpoint":
            payload["checkpoint_id"] = "not-the-head"
        elif problem == "journal":
            payload["journal_identity"] = None
        elif problem == "epoch":
            snapshot["epochs"][-1]["epoch"] = 4
        elif problem == "seen":
            payload["state"]["seen"] = {}
        else:
            snapshot["procedure_execution"] = {"execution_authorized": True}
    rewrite(case, corrupt)
    driver = CountingPolicy()
    assert proceed(case, driver)["outcome"] == "rejected" and not driver.requests


@pytest.mark.parametrize("change", ["missing", "replacement"])
def test_resume_requires_same_existing_operation_journal(case, change):
    journal = case[2]["operation_journal"]
    journal.path.rename(journal.path.with_name("old.sqlite"))
    if change == "replacement":
        db, stream = journal._open(create=True)
        db.close()
        stream.close()
    driver = CountingPolicy()
    assert proceed(case, driver)["outcome"] == "rejected" and not driver.requests


def test_open_operation_barrier_blocks_continuation_for_entire_environment(case):
    journal = case[2]["operation_journal"]
    lease = journal.reserve(identity(), {})
    lease.close()
    driver = CountingPolicy()
    assert proceed(case, driver)["reason"] == "deployment_review_required" and not driver.requests
    assert store(case).load_controller(case[3]["thread_id"])["claimed_child"] is None


@pytest.mark.parametrize("same_origin", [True, False])
def test_even_closed_operation_history_blocks_this_incident_or_original_trigger(case, same_origin):
    journal = case[2]["operation_journal"]
    attempted = identity(trigger_evidence_id=case[0].symptom_evidence_id) if same_origin else identity(
        incident_id=case[3]["diagnosis"]["incident_id"])
    lease = journal.reserve(attempted, {})
    lease.record("not_dispatched", {})
    lease.close()
    journal.acknowledge_terminal(lease.identifier, expected_version=2, approve_review=True)
    driver = CountingPolicy()
    assert proceed(case, driver)["reason"] == "diagnostic_incident_has_operation_history" and not driver.requests


def test_operation_writer_lock_is_held_at_every_policy_call(case):
    journal = case[2]["operation_journal"]

    class Inspect(CountingPolicy):
        def complete(self, *args, **kwargs):
            with pytest.raises(JournalDenied, match="writer_active"):
                journal.reserve(identity(), {})
            return super().complete(*args, **kwargs)

    assert proceed(case, Inspect())["outcome"] == "checkpointed"


@pytest.mark.parametrize("after", [False, True])
def test_claim_write_ack_uncertainty_never_dispatches_or_refunds_automatically(case, monkeypatch, after):
    original = DiagnosticCheckpoints.claim_continuation

    def lost(self, *args, **kwargs):
        if after:
            original(self, *args, **kwargs)
        raise CheckpointDenied("workflow_continuation_write_unconfirmed")

    monkeypatch.setattr(DiagnosticCheckpoints, "claim_continuation", lost)
    driver = CountingPolicy()
    assert proceed(case, driver)["outcome"] == "rejected" and not driver.requests
    assert (store(case).load_controller(case[3]["thread_id"])["claimed_child"] is not None) == after


@pytest.mark.parametrize("after_commit", [False, True])
def test_actual_sqlite_child_claim_commit_uncertainty_stops_before_driver(case, monkeypatch, after_commit):
    original, fired = sqlite3.connect, []

    class LostAck(sqlite3.Connection):
        def __exit__(self, kind, value, traceback):
            try:
                child = self.execute("SELECT child_thread_id FROM diagnostic_continuations LIMIT 1").fetchone()
            except sqlite3.Error:
                child = None
            if kind is None and child and child[0] is not None and not fired:
                fired.append(True)
                if after_commit:
                    super().__exit__(kind, value, traceback)
                else:
                    self.rollback()
                raise OSError("Synthetic missing commit acknowledgement")
            return super().__exit__(kind, value, traceback)

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=LostAck)

    monkeypatch.setattr(sqlite3, "connect", connect)
    driver = CountingPolicy()
    result = proceed(case, driver)
    assert fired and result["reason"] == "workflow_continuation_write_unconfirmed" and not driver.requests
    assert len(store(case).inspect()) == 1
    assert (store(case).load_controller(case[3]["thread_id"])["claimed_child"] is not None) == after_commit


def test_model_transfer_refusal_precedes_parent_claim(case):
    driver = Mock(kind="model")
    response = proceed(case, driver)
    assert response["reason"] == "external_transfer_not_approved"
    driver.complete.assert_not_called()
    assert store(case).load_controller(case[3]["thread_id"])["claimed_child"] is None


def test_controller_attachment_failure_retains_native_history_but_cannot_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(DiagnosticCheckpoints, "save_controller", Mock(side_effect=CheckpointDenied("synthetic_attachment_failure")))
    first = epoch(statuses={"paymentservice": "DOWN"})
    options = {"workflow_database": tmp_path / "workflow.sqlite", "operation_journal": OperationJournal(tmp_path / "operations.sqlite")}
    driver = CountingPolicy()
    result = start_diagnosis(first, driver, approve_start=True, **options)
    assert result["outcome"] == "rejected" and driver.requests == 2
    assert DiagnosticCheckpoints(options["workflow_database"]).inspect()[0]["state"] == "completed_history"
    later = continue_diagnosis(first, epoch(1, previous=first), CountingPolicy(),
        parent_thread_id=diagnostic_thread_id("boutique-local", first.symptom_evidence_id), approve_continuation=True, **options)
    assert later["outcome"] == "rejected"


@pytest.mark.parametrize("key,value", [("calls_used", True), ("decisions_used", -1), ("response_bytes", 131073),
    ("admission_units", -1), ("known_reported_tokens", -1), ("remaining_seconds", 181),
    ("remaining_seconds", float("nan")), ("decision_in_flight", True), ("usage_complete", False),
    ("stop_reason", "usage_unknown"), ("durable", True)])
def test_budget_restore_refuses_invalid_or_unknown_accounting(key, value):
    evidence, shadow = InvestigationLimits(), ShadowLimits()
    snapshot = SharedShadowBudget(evidence, shadow).snapshot()
    snapshot[key] = value
    with pytest.raises(ValueError):
        SharedShadowBudget.from_completed_checkpoint(evidence, shadow, snapshot, downtime=0)


@pytest.mark.parametrize("downtime", [True, -1, float("inf"), float("nan"), 181])
def test_budget_restore_refuses_bad_or_exhausting_downtime(downtime):
    evidence, shadow = InvestigationLimits(), ShadowLimits()
    snapshot = SharedShadowBudget(evidence, shadow).snapshot()
    with pytest.raises(ValueError):
        SharedShadowBudget.from_completed_checkpoint(evidence, shadow, snapshot, downtime=downtime)


def test_aliasing_both_databases_is_rejected_before_creation(tmp_path):
    path = tmp_path / "same.sqlite"
    result = start_diagnosis(epoch(), CountingPolicy(), workflow_database=path, operation_journal=OperationJournal(path), approve_start=True)
    assert result["reason"] == "distinct_diagnostic_and_operation_stores_required" and not path.exists()


def test_separate_process_continues_with_existing_counters_and_new_evidence(case, tmp_path):
    origin, fresh, options, started = case
    a, b, output = (tmp_path / name for name in ("origin.json", "fresh.json", "continued.json"))
    save_inputs(a, origin)
    save_inputs(b, fresh)
    result = subprocess.run([sys.executable, "-m", "simulation.diagnostic_continuation", "continue", "--origin", str(a),
        "--fresh", str(b), "--parent-thread", started["thread_id"], "--workflow-database", str(options["workflow_database"]),
        "--operation-journal", str(options["operation_journal"].path), "--output", str(output), "--approve-continuation"],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(output.read_text())
    assert receipt["diagnosis"]["incident_budget"]["decisions_used"] == 4
    assert receipt["diagnosis"]["current_candidate"]["proposal"]["service_id"] == "checkoutservice"
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(result.stdout)["real_model_calls"] == 0


CRASH = '''
import os, sys
from pathlib import Path
from core.workflow_checkpoints import DiagnosticCheckpoints
from core.operation_journal import OperationJournal
from agent.diagnostic_continuation import continue_diagnosis
from eval.shadow_replay import load_inputs
from eval.baselines.investigation import FixedProbePolicy
root=Path(sys.argv[1])
if sys.argv[3] == "claim":
    original=DiagnosticCheckpoints.claim_continuation
    def die(self,*args,**kwargs):
        original(self,*args,**kwargs)
        os._exit(42)
    DiagnosticCheckpoints.claim_continuation=die
    driver=FixedProbePolicy()
else:
    class Die(FixedProbePolicy):
        def complete(self,*args,**kwargs): os._exit(43)
    driver=Die()
continue_diagnosis(load_inputs(root/"origin.json"),load_inputs(root/"fresh.json"),driver,
    parent_thread_id=sys.argv[2],workflow_database=root/"workflows.sqlite",
    operation_journal=OperationJournal(root/"operations.sqlite"),approve_continuation=True)
'''


@pytest.mark.parametrize("boundary,code", [("claim", 42), ("driver", 43)])
def test_real_exit_after_child_claim_or_during_decision_is_never_blindly_repeated(case, boundary, code):
    root = case[2]["workflow_database"].parent
    save_inputs(root / "origin.json", case[0])
    save_inputs(root / "fresh.json", case[1])
    result = subprocess.run([sys.executable, "-c", CRASH, str(root), case[3]["thread_id"], boundary], capture_output=True, timeout=20)
    assert result.returncode == code, result.stderr.decode()
    driver = CountingPolicy()
    assert proceed(case, driver)["reason"] == "continuation_already_claimed" and not driver.requests
    rows = store(case).inspect()
    assert len(rows) == (1 if boundary == "claim" else 2)
    if boundary == "driver":
        assert rows[0]["state"] == "interrupted_or_active_review_required" and not rows[0]["usage_complete"]


def test_cli_imports_no_settings_providers_or_runtime_clients(tmp_path):
    root = Path.cwd()
    code = f'''import sys
sys.path.insert(0,{str(root)!r})
import simulation.diagnostic_continuation
for name in ("core.config", "docker", "openai", "graph.graph_client", "agent.tools.bound_executor"):
    assert name not in sys.modules,name
'''
    env = {key: value for key, value in os.environ.items() if key not in {"NEO4J_PASSWORD", "GROQ_API_KEY", "OPENAI_API_KEY"}}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    assert not (tmp_path / "audit").exists()
