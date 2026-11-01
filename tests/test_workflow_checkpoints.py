"""Native SQLite checkpoints, synthetic diagnosis and actual process exits; no live services."""

from contextlib import closing
from datetime import timedelta
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from agent.shadow import ShadowRunner
from core import workflow_checkpoints as module
from core.workflow_checkpoints import CheckpointDenied, DiagnosticCheckpoints, PlainJsonSerializer
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import save_inputs
from tests.test_shadow_loop import ScriptedDriver, read, session, source as source, suggest
from tests.test_shadow_replan import epoch


def run(source, path, driver=None):
    return ShadowRunner(session(source), source[2], driver or ScriptedDriver([read("paymentservice"), suggest])).run(checkpoint_path=path)


def test_native_checkpoints_retain_session_context_usage_without_changing_report(source, tmp_path):
    path = tmp_path / "workflow.sqlite"
    expected = ShadowRunner(session(source), source[2], ScriptedDriver([read("paymentservice"), suggest])).run()
    report = run(source, path)
    for key in ("termination_reason", "known_reported_tokens", "result", "turns", "admission_units"):
        assert report[key] == expected[key]
    assert {key: value for key, value in report["session"].items() if key != "history"} == {
        key: value for key, value in expected["session"].items() if key != "history"}
    assert [{key: value for key, value in row.items() if key != "recorded_at"} for row in report["session"]["history"]] == [
        {key: value for key, value in row.items() if key != "recorded_at"} for row in expected["session"]["history"]]
    row = DiagnosticCheckpoints(path).inspect(include_state=True)[0]
    assert row["state"] == "completed_history" and row["checkpointed_decisions"] == 2
    assert row["checkpointed_reported_tokens"] == 240 and row["usage_complete"]
    assert row["candidate_is_historical"] and not row["automatic_resume_allowed"]
    assert not row["execution_authorized"] and not row["recovery_verified"]
    assert row["saved_state"]["checkpoint_session"] == report["session"]
    assert row["saved_state"]["checkpoint_context"] == report["initial_context"]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] >= 6
        assert db.execute("SELECT DISTINCT type FROM checkpoints").fetchall() == [("rca-json-v1",)]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_name(path.name + ".lock").stat().st_mode & 0o777 == 0o600


def test_running_reservation_and_input_checkpoint_commit_before_driver(source, tmp_path):
    path = tmp_path / "workflow.sqlite"

    def observe(messages):
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT state FROM diagnostic_runs").fetchone()[0] == "running"
            assert db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] >= 2
        return {"action": "abstain", "reason": "insufficient_evidence"}

    report = run(source, path, ScriptedDriver([observe]))
    assert report["termination_reason"] == "abstained"


def test_provider_reply_checkpoint_commits_before_evidence_dispatch(source, tmp_path, monkeypatch):
    path = tmp_path / "workflow.sqlite"
    original = module.DiagnosticCheckpoints.inspect
    active = session(source)
    call = active.call

    def observe(request):
        row = original(DiagnosticCheckpoints(path), include_state=True)[0]
        assert row["checkpointed_decisions"] == 1 and row["checkpointed_reported_tokens"] == 120
        assert row["saved_state"]["decision"]["action"] == "read"
        return call(request)

    monkeypatch.setattr(active, "call", observe)
    ShadowRunner(active, source[2], ScriptedDriver([read("paymentservice"), suggest])).run(checkpoint_path=path)


@pytest.mark.parametrize("mutation", ["none", "cutoff", "implementation", "driver"])
def test_completed_trigger_cannot_be_reused_with_new_controller_or_identity(source, tmp_path, mutation):
    path = tmp_path / "workflow.sqlite"
    run(source, path)
    row = DiagnosticCheckpoints(path).inspect()[0]
    identity = module.DiagnosticIdentity.model_validate(row["identity"])
    if mutation == "cutoff":
        identity = identity.model_copy(update={"as_of": identity.as_of + timedelta(seconds=10)})
    elif mutation == "implementation":
        identity = identity.model_copy(update={"implementation_digest": "f" * 64})
    elif mutation == "driver":
        identity = identity.model_copy(update={"driver_kind": "model"})
    builder = Mock()
    with pytest.raises(CheckpointDenied, match="workflow_already_admitted_review_required"):
        DiagnosticCheckpoints(path).invoke(builder, {}, identity)
    builder.compile.assert_not_called()
    assert DiagnosticCheckpoints(path).inspect()[0]["state"] == "completed_history"


def test_rejected_reservation_never_falls_back_to_ephemeral_graph(source, tmp_path, monkeypatch):
    driver = ScriptedDriver([read("paymentservice")])
    monkeypatch.setattr(DiagnosticCheckpoints, "_guard", Mock(side_effect=OSError("private storage failure")))
    with pytest.raises(CheckpointDenied, match="workflow_persistence_unconfirmed"):
        run(source, tmp_path / "workflow.sqlite", driver)
    assert driver.calls == []


def test_checkpoint_write_failure_blocks_next_node_and_reuse(source, tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = tmp_path / "workflow.sqlite"
    driver = ScriptedDriver([read("paymentservice")])
    monkeypatch.setattr(SqliteSaver, "put", Mock(side_effect=OSError("private storage failure")))
    with pytest.raises(CheckpointDenied, match="workflow_persistence_unconfirmed"):
        run(source, path, driver)
    assert driver.calls == []
    assert DiagnosticCheckpoints(path).inspect()[0]["state"] == "interrupted_or_active_review_required"
    with pytest.raises(CheckpointDenied, match="workflow_already_admitted_review_required"):
        run(source, path, driver)
    assert driver.calls == []


@pytest.mark.parametrize("moment", ["before", "after"])
def test_guard_write_failure_remains_review_only_before_or_after_commit(source, tmp_path, monkeypatch, moment):
    path = tmp_path / "workflow.sqlite"
    original = module.DiagnosticCheckpoints._guard
    hits = 0

    def fail(self, db, identity, thread):
        nonlocal hits
        original(self, db, identity, thread)
        hits += 1
        # Force an interruption after at least one real graph checkpoint exists.
        if hits == 5:
            if moment == "after":
                db.commit()
            raise OSError("private interrupted write")

    monkeypatch.setattr(DiagnosticCheckpoints, "_guard", fail)
    with pytest.raises(CheckpointDenied):
        run(source, path)
    row = DiagnosticCheckpoints(path).inspect()[0]
    assert not row["usage_complete"] and row["pending_work_may_have_run"]
    assert not row["automatic_resume_allowed"]


@pytest.mark.parametrize("boundary", ["admission", "terminal"])
@pytest.mark.parametrize("ack", ["before_commit", "after_commit"])
def test_actual_commit_ack_loss_cannot_replay_unknown_progress(source, tmp_path, monkeypatch, boundary, ack):
    original = sqlite3.connect
    path, fired = tmp_path / "workflow.sqlite", []

    class LostAck(sqlite3.Connection):
        def __exit__(self, kind, value, traceback):
            try:
                state = self.execute("SELECT state FROM diagnostic_runs LIMIT 1").fetchone()
            except sqlite3.Error:
                state = None
            target = "running" if boundary == "admission" else "completed"
            if kind is None and state == (target,) and not fired:
                fired.append(True)
                if ack == "before_commit":
                    self.rollback()
                else:
                    super().__exit__(kind, value, traceback)
                raise OSError("Synthetic commit acknowledgement unavailable")
            return super().__exit__(kind, value, traceback)

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=LostAck)

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    driver = ScriptedDriver([read("paymentservice"), suggest])
    with pytest.raises(CheckpointDenied, match="workflow_persistence_unconfirmed"):
        run(source, path, driver)
    assert fired
    rows = DiagnosticCheckpoints(path).inspect()
    if boundary == "admission":
        assert not driver.calls
        assert len(rows) == (ack == "after_commit")
    else:
        assert len(driver.calls) == 2 and rows[0]["checkpointed_reported_tokens"] == 240
    if rows:
        assert not rows[0]["usage_complete"] and rows[0]["pending_work_may_have_run"]
        retry = ScriptedDriver([read("paymentservice")])
        with pytest.raises(CheckpointDenied, match="workflow_already_admitted_review_required"):
            run(source, path, retry)
        assert not retry.calls


def test_active_os_writer_refuses_new_run_without_model(source, tmp_path):
    path = tmp_path / "workflow.sqlite"
    lock = path.with_name(path.name + ".lock")
    driver = ScriptedDriver([read("paymentservice")])
    with lock.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(CheckpointDenied, match="workflow_writer_active"):
            run(source, path, driver)
    assert driver.calls == [] and not path.exists()


def test_node_interruption_does_not_store_exception_text_or_replay(source, tmp_path):
    class Interrupt:
        kind = "scripted"

        def complete(self, *args, **kwargs):
            raise KeyboardInterrupt("private exception sentinel")

    path = tmp_path / "workflow.sqlite"
    with pytest.raises(KeyboardInterrupt):
        run(source, path, Interrupt())
    row = DiagnosticCheckpoints(path).inspect()[0]
    assert row["pending_work_may_have_run"] and not row["usage_complete"]
    with closing(sqlite3.connect(path)) as db:
        blobs = db.execute("SELECT value FROM writes").fetchall()
        assert blobs and all(b"private exception sentinel" not in blob[0] for blob in blobs)


def test_missing_optional_extra_does_not_call_driver_or_create_store(source, tmp_path, monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "langgraph.checkpoint.sqlite":
            raise ImportError("Synthetic absent optional package")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    driver = ScriptedDriver([read("paymentservice")])
    path = tmp_path / "workflow.sqlite"
    with pytest.raises(CheckpointDenied, match="workflow_persistence_extra_required"):
        run(source, path, driver)
    assert driver.calls == [] and not path.exists()


def test_admission_capacity_is_bounded_without_pruning_history(source, tmp_path):
    path = tmp_path / "workflow.sqlite"
    run(source, path)
    with closing(sqlite3.connect(path)) as db, db:
        for index in range(255):
            db.execute("INSERT INTO diagnostic_runs SELECT ?,identity,state,started_at,finished_at FROM diagnostic_runs LIMIT 1",
                       (f"synthetic-other-{index}",))
    with closing(sqlite3.connect(path)) as db:
        identity = module.DiagnosticIdentity.model_validate_json(db.execute("SELECT identity FROM diagnostic_runs LIMIT 1").fetchone()[0])
    identity = identity.model_copy(update={"symptom_evidence_id": "SYMOBS-" + "9" * 32})
    builder = Mock()
    with pytest.raises(CheckpointDenied, match="workflow_capacity_review_required"):
        DiagnosticCheckpoints(path).invoke(builder, {}, identity)
    builder.compile.assert_not_called()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM diagnostic_runs").fetchone()[0] == 256


def test_store_replacement_is_detected_before_another_node(source, tmp_path, monkeypatch):
    path = tmp_path / "workflow.sqlite"
    original = DiagnosticCheckpoints._guard
    count = 0

    def replaced(self, db, identity, thread):
        nonlocal count
        count += 1
        if count == 4:
            moved = self.path.with_name("old.sqlite")
            self.path.rename(moved)
            self.path.touch()
        original(self, db, identity, thread)

    monkeypatch.setattr(DiagnosticCheckpoints, "_guard", replaced)
    driver = ScriptedDriver([read("paymentservice")])
    with pytest.raises(CheckpointDenied, match="workflow_file_replaced"):
        run(source, path, driver)
    assert len(driver.calls) <= 1


@pytest.mark.parametrize("kind", ["symlink", "foreign", "schema", "full"])
def test_invalid_store_never_opens_driver(source, tmp_path, kind, monkeypatch):
    path = tmp_path / "workflow.sqlite"
    if kind == "symlink":
        path.symlink_to(tmp_path / "other.sqlite")
    elif kind in {"foreign", "schema"}:
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE alien (id TEXT)")
            db.execute(f"PRAGMA application_id={module.APPLICATION_ID if kind == 'schema' else 123}")
            db.execute("PRAGMA user_version=999")
    else:
        monkeypatch.setattr(module, "MAX_DATABASE", 1)
    driver = ScriptedDriver([read("paymentservice")])
    with pytest.raises(CheckpointDenied):
        run(source, path, driver)
    assert driver.calls == []


@pytest.mark.parametrize("value", [object(), Path("private"), {1: "not a JSON key"}, float("nan"), float("inf")])
def test_serializer_rejects_objects_nonfinite_and_nonstring_keys(value):
    with pytest.raises(CheckpointDenied):
        PlainJsonSerializer().dumps_typed(value)


@pytest.mark.parametrize("kind,payload", [("pickle", b"not loaded"), ("msgpack", b"not loaded"),
    ("rca-json-v1", b'{"x":1,"x":2}'), ("rca-json-v1", b'{"x":NaN}'),
    ("rca-json-v1", b"[")])
def test_serializer_does_not_revive_untrusted_encodings(kind, payload):
    with pytest.raises(CheckpointDenied):
        PlainJsonSerializer().loads_typed((kind, payload))


def test_constructor_record_remains_plain_data_without_import_or_revival():
    value = {"lc": 2, "type": "constructor", "id": ["os", "system"], "kwargs": {"command": "never-run"}}
    serializer = PlainJsonSerializer()
    assert serializer.loads_typed(serializer.dumps_typed(value)) == value


@pytest.mark.parametrize("depth", [True, False])
def test_serializer_bounds_depth_and_payload(monkeypatch, depth):
    value = []
    if depth:
        for _ in range(50):
            value = [value]
    else:
        monkeypatch.setattr(module, "MAX_PAYLOAD", 1)
    with pytest.raises(CheckpointDenied):
        PlainJsonSerializer().dumps_typed(value)


@pytest.mark.parametrize("problem", ["identity", "context", "encoding", "terminal", "corrupt"])
def test_readonly_inspection_refuses_inconsistent_or_foreign_data(source, tmp_path, problem):
    path = tmp_path / "workflow.sqlite"
    run(source, path)
    with closing(sqlite3.connect(path)) as db, db:
        if problem == "identity":
            db.execute("UPDATE diagnostic_runs SET thread_id='WF-wrong'")
        elif problem == "context":
            encoded = json.loads(db.execute("SELECT checkpoint FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1").fetchone()[0])
            encoded["channel_values"]["checkpoint_context"]["unexpected"] = True
            db.execute("UPDATE checkpoints SET checkpoint=? WHERE checkpoint_id=(SELECT max(checkpoint_id) FROM checkpoints)",
                       (json.dumps(encoded).encode(),))
        elif problem == "encoding":
            db.execute("UPDATE checkpoints SET type='pickle'")
        elif problem == "terminal":
            db.execute("DELETE FROM checkpoints")
        else:
            db.execute("PRAGMA user_version=99")
    with pytest.raises(CheckpointDenied):
        DiagnosticCheckpoints(path).inspect(include_state=True)


def test_inspection_of_missing_database_never_creates_files(tmp_path):
    path = tmp_path / "missing" / "workflow.sqlite"
    with pytest.raises(CheckpointDenied, match="workflow_missing_or_symlink"):
        DiagnosticCheckpoints(path).inspect()
    assert not path.parent.exists()


@pytest.mark.parametrize("limit", [0, 101, True, "1"])
def test_invalid_inspection_limit_is_rejected_before_io(tmp_path, limit):
    with pytest.raises(CheckpointDenied, match="invalid_workflow_inspection_options"):
        DiagnosticCheckpoints(tmp_path / "missing").inspect(limit=limit)


CRASH = '''
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone
from agent.shadow import ShadowRunner, DriverReply
from core.workflow_checkpoints import DiagnosticCheckpoints
from eval.shadow_controls import seed_case, fixture_topology
from tests.test_shadow_loop import session, read
root = Path(sys.argv[1])
store = seed_case(root / "evidence.sqlite", {"paymentservice":"CONNECTION_REFUSED"})
cutoff = datetime.now(timezone.utc)
source = store, cutoff, fixture_topology(cutoff)
class Crash:
    kind="scripted"
    def __init__(self): self.calls=0
    def complete(self, messages, **limits):
        self.calls += 1
        if self.calls == int(sys.argv[2]):
            (root / "synthetic-effect-marker").write_text("provider response may have existed")
            os._exit(41)
        return DriverReply(json.dumps(read("paymentservice")), {"input_tokens":100,"output_tokens":20,"total_tokens":120})
ShadowRunner(session(source), source[2], Crash()).run(checkpoint_path=root / "workflows.sqlite")
'''


@pytest.mark.parametrize("call", [1, 2])
def test_real_process_exit_retains_history_and_does_not_reset_allowance(source, tmp_path, call):
    root = tmp_path / "child"
    root.mkdir()
    result = subprocess.run([sys.executable, "-c", CRASH, str(root), str(call)], cwd=Path.cwd(), capture_output=True, timeout=20)
    assert result.returncode == 41, result.stderr.decode()
    path = root / "workflows.sqlite"
    row = DiagnosticCheckpoints(path).inspect(include_state=True)[0]
    assert row["state"] == "interrupted_or_active_review_required" and not row["usage_complete"]
    assert row["checkpointed_decisions"] == call - 1
    assert row["checkpointed_reported_tokens"] == (call - 1) * 120
    assert not row["automatic_resume_allowed"] and row["pending_work_may_have_run"]
    assert (root / "synthetic-effect-marker").exists()
    driver = ScriptedDriver([read("paymentservice")])
    with pytest.raises(CheckpointDenied, match="workflow_already_admitted_review_required"):
        run(source, path, driver)
    assert driver.calls == []


def test_replanning_checkpoints_each_new_epoch_with_shared_live_budget(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    controller = ReplanningInvestigation(first)
    path = tmp_path / "workflow.sqlite"
    a = controller.run_epoch(first, FixedProbePolicy(), checkpoint_path=path)
    b = controller.run_epoch(second, FixedProbePolicy(), checkpoint_path=path)
    assert a["outcome"] == b["outcome"] == "investigated"
    rows = DiagnosticCheckpoints(path).inspect(include_state=True)
    assert len(rows) == 2 and all(row["state"] == "completed_history" for row in rows)
    assert rows[0]["saved_state"]["checkpoint_budget"]["decisions_used"] == 4
    assert rows[1]["saved_state"]["checkpoint_budget"]["decisions_used"] == 2
    assert not any(row["execution_authorized"] or row["automatic_resume_allowed"] for row in rows)


def test_fresh_controller_cannot_replay_persisted_origin_epoch(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    path = tmp_path / "workflow.sqlite"
    assert ReplanningInvestigation(first).run_epoch(first, FixedProbePolicy(), checkpoint_path=path)["outcome"] == "investigated"
    driver = Mock(kind="scripted")
    controller = ReplanningInvestigation(first)
    event = controller.run_epoch(first, driver, checkpoint_path=path)
    assert event["outcome"] == "rejected" and event["reason"] == "workflow_already_admitted_review_required"
    assert controller.snapshot()["incident_budget"]["stop_reason"] == "workflow_checkpoint_review_required"
    following = controller.run_epoch(epoch(1, previous=first), driver, checkpoint_path=path)
    assert following["outcome"] == "rejected" and following["reason"] == "workflow_checkpoint_review_required"
    driver.complete.assert_not_called()


def test_cli_checkpoint_failure_stops_later_epochs_and_returns_failure(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    a, b, store = (tmp_path / name for name in ("a.json", "b.json", "workflows.sqlite"))
    save_inputs(a, first)
    save_inputs(b, second)
    ReplanningInvestigation(first).run_epoch(first, FixedProbePolicy(), checkpoint_path=store)
    output = tmp_path / "attempt.json"
    result = subprocess.run([sys.executable, "-m", "eval.shadow_replan", "--inputs", str(a), "--inputs", str(b),
        "--checkpoint-database", str(store), "--output", str(output)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 1, result.stderr
    receipt = json.loads(output.read_text())
    assert receipt["cli_failure"] == {"epoch": 1, "reason": "epoch_rejected"}
    assert len(receipt["epochs"]) == 1 and len(DiagnosticCheckpoints(store).inspect()) == 1
    assert receipt["incident_budget"]["decisions_used"] == 0


def test_cli_new_checkpoint_run_and_readonly_review_complete_without_provider(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    source, store, output = (tmp_path / name for name in ("a.json", "workflows.sqlite", "report.json"))
    save_inputs(source, first)
    result = subprocess.run([sys.executable, "-m", "eval.shadow_replan", "--inputs", str(source),
        "--checkpoint-database", str(store), "--output", str(output)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())["epochs"][0]["report"]
    assert report["workflow_checkpoint"]["mode"] == "durable-history-only"
    reviewed = subprocess.run([sys.executable, "-m", "simulation.workflow_review", "--database", str(store)],
                              capture_output=True, text=True, timeout=15)
    assert reviewed.returncode == 0, reviewed.stderr
    row = json.loads(reviewed.stdout)["workflows"][0]
    assert row["state"] == "completed_history" and "saved_state" not in row
    assert not row["execution_authorized"] and not row["automatic_resume_allowed"]


def test_review_cli_isolated_from_settings_providers_and_runtime(tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = f'''import sys
sys.path.insert(0, {str(root)!r})
from simulation.workflow_review import main
assert main(["--database", "absent/workflows.sqlite"]) == 1
for name in ("core.config", "agent.shadow", "docker", "openai", "graph.graph_client", "langgraph.checkpoint.sqlite"):
    assert name not in sys.modules, name
'''
    environment = {key: value for key, value in os.environ.items() if key not in {"NEO4J_PASSWORD", "OPENAI_API_KEY", "GROQ_API_KEY"}}
    result = subprocess.run([sys.executable, "-c", command], cwd=tmp_path, env=environment, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    assert not (tmp_path / "absent").exists() and not (tmp_path / "audit").exists()
