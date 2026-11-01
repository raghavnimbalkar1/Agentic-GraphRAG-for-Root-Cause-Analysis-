"""Real local SQLite/process-exit controls; no Docker, application or model requests."""

from copy import copy, deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from core.operation_journal import AttemptIdentity, JournalDenied, OperationJournal
from simulation.operation_review import main


def identity(**changes):
    return AttemptIdentity(**{ "environment_id": "boutique-local", "registry_digest": "a" * 64,
        "incident_id": "INC-one", "trigger_evidence_id": "SYMOBS-" + "b" * 32, "plan_revision": "c" * 64,
        "offer_digest": "d" * 64, "catalog_digest": "e" * 64, "contract_digest": "f" * 64,
        "target": "paymentservice", "instance_id": "1" * 64, "skill_name": "Payment_Restart_SOP",
        "condition": "CONNECTION_REFUSED", **changes})


def journal(tmp_path):
    return OperationJournal(tmp_path / "operations.sqlite")


def test_durable_intent_and_os_writer_lock_precede_effect(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {"state": "preparing"})
    row = store.inspect(receipt=True)[0]
    assert row["stage"] == "preparing" and row["version"] == 1
    assert row["identity"] == identity().model_dump()
    assert row["receipt"] == {"state": "preparing"}
    assert "owner_hash" not in json.dumps(row) and lease._token not in json.dumps(row)
    with pytest.raises(JournalDenied, match="writer_active"):
        journal(tmp_path).reserve(identity(incident_id="INC-two"), {})
    lease.close()
    with pytest.raises(JournalDenied, match="deployment_review_required"):
        journal(tmp_path).reserve(identity(incident_id="INC-two"), {})
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert os.stat(store.path.with_name(store.path.name + ".lock")).st_mode & 0o777 == 0o600


def test_no_dispatched_terminal_needs_versioned_operator_review(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    lease.record("not_dispatched", {"state": "not_dispatched"})
    lease.close()
    current = store.inspect()[0]
    with pytest.raises(JournalDenied, match="explicit_versioned_operator_review_required"):
        store.acknowledge_terminal(lease.identifier, expected_version=current["version"])
    with pytest.raises(JournalDenied, match="terminal_review_not_eligible"):
        store.acknowledge_terminal(lease.identifier, expected_version=1, approve_review=True)
    value = store.acknowledge_terminal(lease.identifier, expected_version=current["version"], approve_review=True)
    assert value["stage"] == "operator_closed" and value["closed"]
    assert not value["execution_authorized"] and not value["automatic_retry_allowed"]
    following = store.reserve(identity(incident_id="INC-two", trigger_evidence_id="SYMOBS-" + "2" * 32), {})
    following.close()


@pytest.mark.parametrize("changes", [{}, {"plan_revision": "2" * 64}, {"contract_digest": "3" * 64},
    {"target": "checkoutservice"}, {"incident_id": "INC-new"}, {"trigger_evidence_id": "SYMOBS-" + "3" * 32}])
def test_closed_incident_or_origin_transaction_cannot_be_replayed(tmp_path, changes):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    lease.record("not_dispatched", {})
    lease.close()
    store.acknowledge_terminal(lease.identifier, expected_version=2, approve_review=True)
    with pytest.raises(JournalDenied, match="incident_or_trigger_already_attempted"):
        store.reserve(identity(**changes), {})


@pytest.mark.parametrize("phase", ["effect_attempted", "effect_uncertain", "operation_confirmed", "verifying",
                                   "verification_inconclusive", "not_recovered", "recurrence_observed", "verified_mitigation"])
def test_uncertain_unverified_and_mitigated_attempts_cannot_release_barrier(tmp_path, phase):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    lease.record("effect_attempted", {})
    if phase == "effect_uncertain":
        lease.record(phase, {})
    elif phase != "effect_attempted":
        lease.record("operation_confirmed", {})
        if phase != "operation_confirmed":
            lease.record("verifying", {})
            if phase != "verifying":
                lease.record(phase, {})
    lease.close()
    row = store.inspect()[0]
    with pytest.raises(JournalDenied, match="terminal_review_not_eligible"):
        store.acknowledge_terminal(lease.identifier, expected_version=row["version"], approve_review=True)
    with pytest.raises(JournalDenied, match="deployment_review_required"):
        store.reserve(identity(incident_id="INC-new", registry_digest="4" * 64, trigger_evidence_id="SYMOBS-" + "3" * 32), {})


def test_verified_window_does_not_automatically_release_but_can_be_reviewed(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    for stage in ("effect_attempted", "operation_confirmed", "verifying", "verified_recovery_window"):
        lease.record(stage, {})
    lease.close()
    assert store.inspect()[0]["stage"] == "verified_recovery_window"
    with pytest.raises(JournalDenied):
        store.reserve(identity(incident_id="INC-new"), {})
    assert store.acknowledge_terminal(lease.identifier, expected_version=5, approve_review=True)["closed"]


def test_restart_cannot_reconstruct_owner_from_a_json_receipt(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    lease.record("effect_attempted", {})
    lease.record("operation_confirmed", {})
    lease.close()
    receipt = store.inspect(receipt=True)[0]
    with pytest.raises(JournalDenied, match="owned_confirmed_attempt_required"):
        journal(tmp_path).resume_verification(lease.identifier, json.dumps(receipt))
    owner = journal(tmp_path).resume_verification(lease.identifier, lease._token)
    owner.record("verifying", {})
    owner.close()
    with pytest.raises(JournalDenied):
        store.resume_verification(lease.identifier, lease._token)


@pytest.mark.parametrize("operation", [copy, deepcopy, pickle.dumps, json.dumps])
def test_owner_is_not_a_serializable_capability(tmp_path, operation):
    lease = journal(tmp_path).reserve(identity(), {})
    try:
        with pytest.raises(TypeError):
            operation(lease)
    finally:
        lease.close()


@pytest.mark.parametrize("receipt", [[], "text", None, {"value": float("nan")}, {"value": object()},
                                    {"value": "x" * (2 * 1024 * 1024)}])
def test_invalid_or_oversized_receipt_is_rejected_before_file_creation(tmp_path, receipt):
    store = journal(tmp_path)
    with pytest.raises(JournalDenied, match="invalid_or_oversized_receipt"):
        store.reserve(identity(), receipt)
    assert not store.path.exists()


def test_failed_record_rolls_back_without_losing_the_previous_state(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    with pytest.raises(JournalDenied):
        lease.record("effect_attempted", {"value": float("nan")})
    assert store.inspect()[0]["stage"] == "preparing"
    lease.record("not_dispatched", {})
    lease.close()


@pytest.mark.parametrize("stage", ["verified_recovery_window", "operator_closed", "unsupported"])
def test_no_skipping_admission_or_verification_states(tmp_path, stage):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    with pytest.raises(JournalDenied, match="transition_or_event_budget_rejected"):
        lease.record(stage, {})
    assert store.inspect()[0]["version"] == 1
    lease.close()


def test_event_budget_does_not_expand_or_delete_history(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    for i in range(31):
        lease.record("preparing", {"index": i})
    with pytest.raises(JournalDenied, match="transition_or_event_budget_rejected"):
        lease.record("preparing", {})
    lease.close()
    assert store.inspect()[0]["version"] == 32
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 32


@pytest.mark.parametrize("corruption", ["receipt", "identity", "history", "barrier"])
def test_corrupt_journal_is_not_eligible_for_review_or_dispatch(tmp_path, corruption):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    lease.record("not_dispatched", {})
    lease.close()
    with sqlite3.connect(store.path) as db:
        if corruption == "receipt":
            db.execute("UPDATE attempts SET receipt=?", ('{"changed":true}',))
        elif corruption == "identity":
            db.execute("UPDATE attempts SET identity=?", ('{}',))
        elif corruption == "history":
            db.execute("DELETE FROM events WHERE version=2")
        else:
            db.execute("DELETE FROM barriers")
    with pytest.raises(JournalDenied):
        store.inspect()
    with pytest.raises(JournalDenied):
        store.acknowledge_terminal(lease.identifier, expected_version=2, approve_review=True)


def test_database_loss_is_not_silently_recreated_during_owned_work(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    store.path.rename(tmp_path / "moved.sqlite")
    with pytest.raises(JournalDenied):
        lease.record("effect_attempted", {})
    lease.close()
    assert not store.path.exists()


@pytest.mark.parametrize("kind", ["wrong_schema", "unrelated_tables", "garbage", "symlink"])
def test_unrecognized_database_is_not_reset(tmp_path, kind):
    store = journal(tmp_path)
    if kind == "symlink":
        other = tmp_path / "other.sqlite"
        other.write_text("untouched")
        store.path.symlink_to(other)
    elif kind == "garbage":
        store.path.write_text("not a SQLite database")
    else:
        with sqlite3.connect(store.path) as db:
            db.execute("CREATE TABLE existing (value TEXT)")
            if kind == "wrong_schema":
                db.execute("PRAGMA user_version=2")
    before = store.path.read_bytes()
    with pytest.raises(JournalDenied):
        store.reserve(identity(), {})
    assert store.path.read_bytes() == before


def test_inspection_does_not_create_a_missing_journal(tmp_path):
    store = OperationJournal(tmp_path / "missing" / "operations.sqlite")
    with pytest.raises(JournalDenied):
        store.inspect()
    assert not store.path.parent.exists()


def test_symlinked_writer_lock_is_not_followed(tmp_path):
    store = journal(tmp_path)
    other = tmp_path / "untouched.txt"
    other.write_text("untouched")
    store.path.with_name(store.path.name + ".lock").symlink_to(other)
    with pytest.raises(JournalDenied):
        store.reserve(identity(), {})
    assert other.read_text() == "untouched" and not store.path.exists()


def test_replaced_journal_inode_blocks_an_existing_owner(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    replacement = tmp_path / "replacement.sqlite"
    replacement.write_text("different file")
    replacement.replace(store.path)
    with pytest.raises(JournalDenied, match="journal_file_replaced"):
        lease.record("effect_attempted", {})
    lease.close()


@pytest.mark.parametrize("limit", [True, 0, 101, "20"])
def test_inspection_limit_is_strict(tmp_path, limit):
    with pytest.raises(JournalDenied, match="invalid_inspection_limit"):
        journal(tmp_path).inspect(limit=limit)


def test_constructed_invalid_identity_is_revalidated(tmp_path):
    invalid = identity().model_copy(update={"instance_id": "PRIVATE-invalid"})
    with pytest.raises(JournalDenied, match="invalid_attempt_identity"):
        journal(tmp_path).reserve(invalid, {})
    assert not journal(tmp_path).path.exists()


def test_a_second_process_cannot_enter_an_active_writer(tmp_path):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    program = """
import json, sys
from pathlib import Path
from core.operation_journal import OperationJournal, AttemptIdentity, JournalDenied
try:
    OperationJournal(Path(sys.argv[1])).reserve(AttemptIdentity.model_validate(json.loads(sys.argv[2])), {})
except JournalDenied as error:
    print(str(error))
else:
    raise SystemExit(2)
"""
    try:
        child = subprocess.run([sys.executable, "-c", program, str(store.path), identity().model_dump_json()],
                               capture_output=True, text=True, timeout=20)
        assert child.returncode == 0 and child.stdout.strip() == "writer_active"
        assert store.inspect()[0]["version"] == 1
    finally:
        lease.close()


def test_inspection_uses_a_consistent_snapshot_while_a_writer_commits(tmp_path):
    store = journal(tmp_path)
    ready = threading.Event()

    def writer():
        lease = store.reserve(identity(), {})
        try:
            ready.set()
            for index in range(25):
                lease.record("preparing", {"index": index})
                time.sleep(0.001)
        finally:
            lease.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(5)
        versions = [store.inspect()[0]["version"] for _ in range(50)]
        future.result()
    assert versions == sorted(versions)
    assert store.inspect()[0]["version"] == 26


@pytest.mark.parametrize("approval", [1, "true", None, False])
def test_terminal_review_requires_literal_approval(tmp_path, approval):
    with pytest.raises(JournalDenied, match="explicit_versioned_operator_review_required"):
        journal(tmp_path).acknowledge_terminal("ATT-test", expected_version=1, approve_review=approval)


def test_inspection_cli_is_read_only_and_does_not_create_missing_files(tmp_path, capsys):
    file = tmp_path / "missing.sqlite"
    assert main(["--journal", str(file), "show"]) == 1
    assert not file.exists()
    value = json.loads(capsys.readouterr().out)
    assert value["execution_authorized"] is False and "Traceback" not in str(value)


def test_review_cli_does_not_bypass_approval_or_version(tmp_path, capsys):
    store = journal(tmp_path)
    lease = store.reserve(identity(), {})
    lease.record("not_dispatched", {})
    lease.close()
    args = ["--journal", str(store.path), "acknowledge-terminal", lease.identifier, "--version", "2"]
    assert main(args) == 1
    assert main(args + ["--approve-review"]) == 0
    assert "owner_hash" not in capsys.readouterr().out
    assert store.inspect()[0]["closed"]


CRASH_PROGRAM = """
import json, os, sys
from pathlib import Path
from core.operation_journal import OperationJournal, AttemptIdentity
store = OperationJournal(Path(sys.argv[1]))
lease = store.reserve(AttemptIdentity.model_validate(json.loads(sys.argv[2])), {})
phase = sys.argv[3]
if phase != 'preparing':
    lease.record('effect_attempted', {})
if phase not in {'preparing', 'effect_attempted'}:
    Path(sys.argv[1] + '.simulated-effect').write_text('only a local test marker')
if phase in {'operation_confirmed', 'verifying', 'verified_recovery_window'}:
    lease.record('operation_confirmed', {})
if phase in {'verifying', 'verified_recovery_window'}:
    lease.record('verifying', {})
if phase == 'verified_recovery_window':
    lease.record('verified_recovery_window', {})
os._exit(71)
"""


@pytest.mark.parametrize("phase,stored", [("preparing", "preparing"), ("effect_attempted", "effect_attempted"),
    ("effect_before_ack", "effect_attempted"), ("operation_confirmed", "operation_confirmed"),
    ("verifying", "verifying"), ("verified_recovery_window", "verified_recovery_window")])
def test_abrupt_exit_preserves_barrier_without_resuming_or_repeating_effect(tmp_path, phase, stored):
    store = journal(tmp_path)
    child = subprocess.run([sys.executable, "-c", CRASH_PROGRAM, str(store.path), identity().model_dump_json(), phase],
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 71, child.stderr
    row = journal(tmp_path).inspect()[0]
    assert row["stage"] == stored and not row["automatic_retry_allowed"]
    assert Path(str(store.path) + ".simulated-effect").exists() == (phase not in {"preparing", "effect_attempted"})
    with pytest.raises(JournalDenied, match="deployment_review_required"):
        journal(tmp_path).reserve(identity(incident_id="INC-new", trigger_evidence_id="SYMOBS-" + "2" * 32), {})
