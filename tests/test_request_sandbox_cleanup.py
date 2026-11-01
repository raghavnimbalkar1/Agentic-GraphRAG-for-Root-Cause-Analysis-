"""Mocked Docker and real temporary journals. No provider, live deletion or application probes."""

from contextlib import closing
from copy import copy, deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from docker import DockerClient
from docker.errors import NotFound
import pytest

from agent.tools import sandbox_cleanup as module
from core import operation_journal as journal_module
from core.operation_journal import JournalDenied, OperationJournal, SandboxCleanupLease
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from simulation.operation_review import main
from tests.test_bound_execution import factory as factory, issue
from tests.test_operation_journal import identity


def request_box(reservation):
    box = Mock()
    box.id, box.name = "c" * 64, reservation["name"]
    box.attrs = {"Id": box.id, "Name": "/" + box.name, "Image": reservation["image_id"],
        "Config": {"Labels": {"rca.role": reservation["role"], "rca.attempt": reservation["nonce"]},
                   "User": "1000:1000", "Cmd": ["bash", "/script/sop.sh"], "Entrypoint": [],
                   "Env": ["PATH=/usr/bin", "RCA_CONTROL_MODE=intent-only"]},
        "HostConfig": {"NetworkMode": "none", "Privileged": False, "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                       "CapAdd": [], "SecurityOpt": ["no-new-privileges"], "PidMode": "", "IpcMode": "private",
                       "Memory": 128 * 1024 * 1024, "MemorySwap": 128 * 1024 * 1024,
                       "NanoCpus": 250_000_000, "PidsLimit": 32, "Tmpfs": {"/tmp": "size=16m"}},
        "NetworkSettings": {"Networks": {"none": {}}},
        "Mounts": [{"Type": "bind", "RW": False, "Source": f"/tmp/rca-bound-sop-test/{name}",
                    "Destination": f"/script/{name}"} for name in ("sop.sh", "control_client.py")]}
    return box


@pytest.fixture
def case(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=100.)
    start = datetime.now(timezone.utc)
    wall = lambda: start + timedelta(seconds=clock.value - 100)
    monkeypatch.setattr(module, "monotonic", lambda: clock.value)
    monkeypatch.setattr(module, "utc_now", wall)
    monkeypatch.setattr(journal_module, "_now", wall)
    reservation = {"name": "sop-intent-" + "a" * 12, "nonce": "a" * 32,
                   "image_id": "sha256:" + "b" * 64, "role": "bound-sop-intent"}
    receipt = {"execution_policy": "operator-controlled-v2", "sandbox_intent": reservation,
               "sandbox_cleaned": False, "state": "not_dispatched", "operation_confirmed": False}
    store = OperationJournal(tmp_path / "operations.sqlite")
    attempt = identity(environment_id=REGISTRY.environment_id, registry_digest=REGISTRY.digest)
    owner = store.reserve(attempt, receipt)
    owner.record("not_dispatched", receipt)
    owner.close()
    box = request_box(reservation)
    client, present = Mock(), {"value": True}

    def get(key):
        assert key in {reservation["name"], box.id}, "No unrelated container inspection"
        if not present["value"]:
            raise NotFound("Synthetic absent")
        return box

    def remove(key, **kwargs):
        row = store.inspect(receipt=True)[0]
        assert row["stage"] == "cleanup_removal_admitted"
        assert row["receipt"]["sandbox_cleanup"]["removal_admission"]["container_id"] == key == box.id
        assert not row["closed"] and row["receipt"]["sandbox_cleaned"] is False
        present["value"] = False

    client.containers.get.side_effect = get
    client.api.remove_container.side_effect = remove
    monkeypatch.setattr(module, "_docker_client", Mock(return_value=client))
    return SimpleNamespace(store=store, attempt=attempt, reservation=reservation, receipt=receipt,
        box=box, client=client, present=present, clock=clock, wall=wall, owner=owner)


def cleanup(case, **kwargs):
    row = case.store.inspect()[0]
    return module.cleanup_request_sandbox(case.store, row["attempt_id"], expected_version=row["version"],
                                         approve_cleanup=True, **kwargs)


def replace_receipt(case, change):
    """Make an older interrupted receipt through its real owner before a review exists."""
    db, stream = case.store._open()
    try:
        row = journal_module._row(db, case.owner.identifier)
        receipt = json.loads(row["receipt"])
        change(receipt)
        with db:
            journal_module._record(db, row, "not_dispatched", receipt)
    finally:
        db.close()
        stream.close()


def assert_no_service_work(case):
    case.client.containers.create.assert_not_called()
    case.client.containers.run.assert_not_called()
    case.client.containers.prune.assert_not_called()
    case.box.remove.assert_not_called()  # Removal uses a pinned immutable ID, not the name/object.
    case.box.restart.assert_not_called()
    case.box.update.assert_not_called()
    case.box.exec_run.assert_not_called()


def test_exact_request_removal_is_durable_before_one_sdk_call_and_keeps_barrier(case):
    result = cleanup(case)
    assert result["outcome"] == "absent" and result["journal_finish_confirmed"]
    assert result["remove_dispatched"] and result["removal_acknowledged"] and result["absence_observed"]
    case.client.api.remove_container.assert_called_once_with(case.box.id, force=True, v=False)
    case.client.close.assert_called_once()
    row = case.store.inspect(receipt=True)[0]
    assert row["stage"] == "cleanup_finished" and not row["closed"]
    assert row["receipt"]["sandbox_cleaned"] is False
    assert row["receipt"]["state"] == "not_dispatched" and not row["receipt"]["operation_confirmed"]
    with pytest.raises(JournalDenied, match="terminal_review_not_eligible"):
        case.store.acknowledge_terminal(row["attempt_id"], expected_version=row["version"], approve_review=True)
    with pytest.raises(JournalDenied, match="deployment_review_required"):
        case.store.reserve(identity(incident_id="INC-new", environment_id=REGISTRY.environment_id), {})
    assert_no_service_work(case)


def test_already_absent_is_only_an_observation_and_never_dispatches_removal(case):
    case.present["value"] = False
    result = cleanup(case)
    assert result["outcome"] == "absent" and result["reason"] == "reserved_request_already_absent"
    assert not result["remove_dispatched"] and not result["removal_acknowledged"]
    assert case.store.inspect(receipt=True)[0]["receipt"]["sandbox_cleanup"]["removal_admission"] is None
    case.client.api.remove_container.assert_not_called()
    assert_no_service_work(case)


@pytest.mark.parametrize("approval", [False, 1, "true", None])
def test_literal_approval_required_before_runtime_or_journal_changes(case, approval):
    row = case.store.inspect()[0]
    result = module.cleanup_request_sandbox(case.store, row["attempt_id"], expected_version=row["version"], approve_cleanup=approval)
    assert result["outcome"] == "rejected"
    assert case.store.inspect()[0] == row
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("version", [True, 0, 1, "2"])
def test_strict_current_version_required(case, version):
    result = module.cleanup_request_sandbox(case.store, case.owner.identifier, expected_version=version, approve_cleanup=True)
    assert result["outcome"] == "rejected"
    module._docker_client.assert_not_called()


def test_writer_lock_excludes_cleanup_and_is_held_through_removal(case):
    db, stream = case.store._open()
    try:
        assert cleanup(case)["reason"] == "writer_active"
    finally:
        db.close()
        stream.close()
    remove = case.client.api.remove_container.side_effect

    def guarded(*args, **kwargs):
        with pytest.raises(JournalDenied, match="writer_active"):
            case.store._open()
        return remove(*args, **kwargs)

    case.client.api.remove_container.side_effect = guarded
    assert cleanup(case)["outcome"] == "absent"


@pytest.mark.parametrize("problem", ["policy", "missing", "nonce", "name", "role", "image", "extra"])
def test_missing_legacy_or_conflicting_reservation_cannot_open_docker(case, problem):
    def change(receipt):
        if problem == "policy":
            receipt["execution_policy"] = "operator-controlled-v1"
        elif problem == "missing":
            receipt.pop("sandbox_intent")
        else:
            receipt["sandbox_intent"][problem] = "invalid"

    replace_receipt(case, change)
    assert cleanup(case)["reason"] == "exact_request_reservation_required"
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("section,key,value", [
    ("root", "Id", "d" * 64), ("root", "Name", "/paymentservice"), ("root", "Image", "sha256:" + "d" * 64),
    ("Labels", "rca.attempt", "d" * 32), ("Labels", "rca.role", "service"),
    ("Labels", REGISTRY.ownership_label, REGISTRY.ownership_value), ("Labels", "com.docker.compose.service", "paymentservice"),
    ("Config", "User", "root"), ("Config", "Cmd", ["sh"]), ("Config", "Entrypoint", ["sh"]),
    ("Config", "Env", []), ("HostConfig", "NetworkMode", "boutique-sim"), ("HostConfig", "Privileged", True),
    ("HostConfig", "ReadonlyRootfs", False), ("HostConfig", "CapDrop", []), ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
    ("HostConfig", "PidMode", "host"), ("HostConfig", "IpcMode", "host"),
    ("HostConfig", "SecurityOpt", []), ("HostConfig", "VolumesFrom", ["paymentservice"]),
    ("HostConfig", "Devices", [{}]), ("HostConfig", "Memory", 0), ("HostConfig", "MemorySwap", -1),
    ("HostConfig", "NanoCpus", 0), ("HostConfig", "PidsLimit", -1), ("HostConfig", "Tmpfs", {}),
    ("NetworkSettings", "Networks", {"boutique-sim": {}}),
])
def test_identity_isolation_or_managed_ownership_conflict_blocks_removal(case, section, key, value):
    data = case.box.attrs if section == "root" else case.box.attrs[section] if section != "Labels" else case.box.attrs["Config"]["Labels"]
    data[key] = value
    result = cleanup(case)
    assert result["outcome"] == "inconclusive"
    assert result["reason"] == "request_sandbox_identity_or_isolation_conflict"
    case.client.api.remove_container.assert_not_called()
    assert_no_service_work(case)


@pytest.mark.parametrize("problem", ["writable", "socket", "missing", "duplicate", "different_directory", "traversal"])
def test_mount_conflicts_block_removal(case, problem):
    mounts = case.box.attrs["Mounts"]
    if problem == "writable":
        mounts[0]["RW"] = True
    elif problem == "socket":
        mounts.append({"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": False})
    elif problem == "missing":
        mounts.pop()
    elif problem == "duplicate":
        mounts.append(deepcopy(mounts[0]))
    elif problem == "different_directory":
        mounts[0]["Source"] = "/tmp/rca-bound-sop-other/sop.sh"
    else:
        mounts[0]["Source"] = "/tmp/../tmp/rca-bound-sop-test/sop.sh"
    assert cleanup(case)["reason"] == "request_sandbox_mount_conflict"
    case.client.api.remove_container.assert_not_called()


def test_scoped_service_instance_cannot_be_deleted_even_with_request_labels(case):
    replace_receipt(case, lambda receipt: receipt.update(reconciliation_basis={"instances": {"frontend": case.box.id}}))
    assert cleanup(case)["reason"] == "request_sandbox_identity_or_isolation_conflict"
    case.client.api.remove_container.assert_not_called()


def test_original_target_instance_cannot_be_deleted_even_with_request_labels(case):
    case.box.id = case.attempt.instance_id
    case.box.attrs["Id"] = case.box.id
    assert cleanup(case)["reason"] == "request_sandbox_identity_or_isolation_conflict"
    case.client.api.remove_container.assert_not_called()


@pytest.mark.parametrize("field,value", [("environment_id", "another-environment"), ("registry_digest", "d" * 64)])
def test_another_deployment_cannot_open_docker(case, field, value):
    store = OperationJournal(case.store.path.with_name("other.sqlite"))
    owner = store.reserve(case.attempt.model_copy(update={field: value}), case.receipt)
    owner.close()
    result = module.cleanup_request_sandbox(store, owner.identifier, expected_version=1, approve_cleanup=True)
    assert result["reason"] == "sandbox_cleanup_deployment_mismatch"
    module._docker_client.assert_not_called()


def test_identity_is_rechecked_by_immutable_id_after_removal_admission(case):
    calls = 0

    def reload():
        nonlocal calls
        calls += 1
        if calls == 2:
            case.box.attrs["Config"]["Labels"]["rca.attempt"] = "d" * 32

    case.box.reload.side_effect = reload
    assert cleanup(case)["outcome"] == "inconclusive"
    assert case.store.inspect(receipt=True)[0]["receipt"]["sandbox_cleanup"]["removal_admission"] is not None
    case.client.api.remove_container.assert_not_called()


@pytest.mark.parametrize("acknowledgement", ["timeout", "not_found", "success_but_present", "name_reused"])
def test_unknown_or_conflicting_removal_never_retries_or_releases_barrier(case, acknowledgement):
    if acknowledgement == "timeout":
        case.client.api.remove_container.side_effect = TimeoutError("PRIVATE synthetic SDK text")
    elif acknowledgement == "not_found":
        case.client.api.remove_container.side_effect = NotFound("PRIVATE absent acknowledgement")
    elif acknowledgement == "success_but_present":
        case.client.api.remove_container.side_effect = None
    else:
        get = case.client.containers.get.side_effect

        def reused(key):
            if not case.present["value"] and key == case.reservation["name"]:
                return Mock()  # No deletion of the newly reused name.
            return get(key)

        case.client.containers.get.side_effect = reused
    result = cleanup(case)
    assert result["outcome"] == "inconclusive" and not result["current_state_closed"]
    assert "PRIVATE" not in json.dumps(result)
    assert cleanup(case)["outcome"] == "rejected"
    case.client.api.remove_container.assert_called_once_with(case.box.id, force=True, v=False)


@pytest.mark.parametrize("phase", ["admission", "inspection", "removal_admission", "remove_return", "close"])
def test_deadline_is_not_extended_by_durable_io_or_sdk_waits(case, monkeypatch, phase):
    def delayed(function):
        def run(*args, **kwargs):
            result = function(*args, **kwargs)
            case.clock.value += 21
            return result
        return run

    if phase == "admission":
        monkeypatch.setattr(case.store, "begin_sandbox_cleanup", delayed(case.store.begin_sandbox_cleanup))
    elif phase == "inspection":
        case.box.reload.side_effect = lambda: setattr(case.clock, "value", case.clock.value + 21)
    elif phase == "removal_admission":
        monkeypatch.setattr(SandboxCleanupLease, "admit_removal", delayed(SandboxCleanupLease.admit_removal))
    elif phase == "remove_return":
        case.client.api.remove_container.side_effect = delayed(case.client.api.remove_container.side_effect)
    else:
        case.client.close.side_effect = lambda: setattr(case.clock, "value", case.clock.value + 21)
    assert cleanup(case)["outcome"] == "inconclusive"
    assert case.client.api.remove_container.call_count == (1 if phase in {"remove_return", "close"} else 0)
    assert not case.store.inspect()[0]["closed"]


def test_resource_close_uncertainty_cannot_accept_absence_or_repeat_close(case):
    case.client.close.side_effect = OSError("PRIVATE close")
    result = cleanup(case)
    assert result["outcome"] == "inconclusive" and result["absence_observed"]
    case.client.close.assert_called_once()


@pytest.mark.parametrize("committed", [False, True])
def test_cleanup_start_acknowledgement_loss_never_opens_runtime(case, monkeypatch, committed):
    begin = case.store.begin_sandbox_cleanup

    def lost(*args, **kwargs):
        if committed:
            begin(*args, **kwargs).close()
        raise JournalDenied("sandbox_cleanup_start_unconfirmed")

    monkeypatch.setattr(case.store, "begin_sandbox_cleanup", lost)
    assert cleanup(case)["outcome"] == "rejected"
    module._docker_client.assert_not_called()
    assert case.store.inspect()[0]["stage"] == ("cleanup_inspecting" if committed else "not_dispatched")


@pytest.mark.parametrize("committed", [False, True])
def test_removal_admission_acknowledgement_loss_never_dispatches(case, monkeypatch, committed):
    admit = SandboxCleanupLease.admit_removal

    def lost(self, *args, **kwargs):
        if committed:
            admit(self, *args, **kwargs)
        raise JournalDenied("sandbox_removal_admission_unconfirmed")

    monkeypatch.setattr(SandboxCleanupLease, "admit_removal", lost)
    result = cleanup(case)
    assert result["outcome"] == "inconclusive"
    case.client.api.remove_container.assert_not_called()
    receipt = case.store.inspect(receipt=True)[0]["receipt"]["sandbox_cleanup"]
    assert (receipt["removal_admission"] is not None) == committed
    assert cleanup(case)["outcome"] == "rejected"


@pytest.mark.parametrize("committed", [False, True])
def test_finish_acknowledgement_loss_is_not_accepted_or_repeated(case, monkeypatch, committed):
    finish = SandboxCleanupLease.finish

    def lost(self, *args, **kwargs):
        if committed:
            finish(self, *args, **kwargs)
        raise JournalDenied("sandbox_cleanup_finish_unconfirmed")

    monkeypatch.setattr(SandboxCleanupLease, "finish", lost)
    result = cleanup(case)
    assert result["outcome"] == "inconclusive" and not result["journal_finish_confirmed"]
    assert not case.store.inspect()[0]["closed"]
    assert cleanup(case)["outcome"] == "rejected"
    case.client.api.remove_container.assert_called_once()


CRASH = """
import os, sys
from pathlib import Path
from core.operation_journal import OperationJournal
store = OperationJournal(Path(sys.argv[1]))
row = store.inspect()[0]
lease = store.begin_sandbox_cleanup(row['attempt_id'], expected_version=row['version'], approve_cleanup=True)
if sys.argv[2] != 'inspection':
    lease.admit_removal('c' * 64)
if sys.argv[2] == 'after-effect':
    Path(sys.argv[1] + '.synthetic-removal').touch()
os._exit(71)
"""


@pytest.mark.parametrize("phase", ["inspection", "admitted", "after-effect"])
def test_actual_process_exit_retains_admission_and_does_not_replay_removal(case, phase):
    child = subprocess.run([sys.executable, "-c", CRASH, str(case.store.path), phase],
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 71, child.stderr
    assert Path(str(case.store.path) + ".synthetic-removal").exists() == (phase == "after-effect")
    row = case.store.inspect(receipt=True)[0]
    assert row["stage"] == ("cleanup_inspecting" if phase == "inspection" else "cleanup_removal_admitted")
    assert not row["closed"] and row["receipt"]["sandbox_cleanup"]["report"] is None
    assert cleanup(case)["outcome"] == "rejected"
    module._docker_client.assert_not_called()
    review = case.store.begin_reconciliation(row["attempt_id"], expected_version=row["version"], approve_inspection=True)
    review.close()  # Independent observation is possible; it does not continue cleanup.


@pytest.mark.parametrize("transform", [copy, deepcopy, pickle.dumps])
def test_cleanup_owner_cannot_be_copied_or_serialized(case, transform):
    lease = case.store.begin_sandbox_cleanup(case.owner.identifier, expected_version=2, approve_cleanup=True)
    try:
        with pytest.raises(TypeError):
            transform(lease)
    finally:
        lease.close()


def test_cli_denies_without_approval_and_emits_explicit_cleanup_receipt(case, capsys):
    args = ["--journal", str(case.store.path), "cleanup-request-sandbox", case.owner.identifier, "--version", "2"]
    assert main(args) == 1
    module._docker_client.assert_not_called()
    capsys.readouterr()
    assert main(args + ["--approve-cleanup"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["outcome"] == "absent" and not value["execution_authorized"]


def test_cleanup_does_not_create_a_missing_journal(tmp_path):
    store = OperationJournal(tmp_path / "missing.sqlite")
    assert module.cleanup_request_sandbox(store, "ATT-missing", expected_version=1, approve_cleanup=True)["outcome"] == "rejected"
    assert not store.path.exists()


def test_event_budget_keeps_room_for_cleanup_and_separate_reconciliation(case):
    db, stream = case.store._open()
    try:
        for _ in range(26):
            with db:
                row = journal_module._row(db, case.owner.identifier)
                journal_module._record(db, row, "not_dispatched", json.loads(row["receipt"]))
    finally:
        db.close()
        stream.close()
    assert cleanup(case)["reason"] == "sandbox_cleanup_event_budget_exhausted"
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("committed", [False, True])
def test_actual_commit_uncertainty_before_removal_does_not_dispatch(case, monkeypatch, committed):
    original_open = case.store._open

    class FailAdmission(sqlite3.Connection):
        def __exit__(self, kind, *args):
            if kind is None and self.execute("SELECT stage FROM attempts").fetchone()[0] == "cleanup_removal_admitted":
                if committed:
                    super().__exit__(kind, *args)
                else:
                    self.rollback()
                raise sqlite3.OperationalError("PRIVATE commit failure")
            return super().__exit__(kind, *args)

    def opened():
        db, stream = original_open()
        db.close()
        db = sqlite3.connect(case.store.path, factory=FailAdmission)
        db.row_factory = sqlite3.Row
        return db, stream

    monkeypatch.setattr(case.store, "_open", opened)
    assert cleanup(case)["outcome"] == "inconclusive"
    case.client.api.remove_container.assert_not_called()
    receipt = case.store.inspect(receipt=True)[0]["receipt"]["sandbox_cleanup"]
    assert (receipt["removal_admission"] is not None) == committed


def test_late_journal_finish_cannot_return_an_accepted_absence(case, monkeypatch):
    finish = SandboxCleanupLease.finish

    def slow(self, *args, **kwargs):
        result = finish(self, *args, **kwargs)
        case.clock.value += 6
        return result

    monkeypatch.setattr(SandboxCleanupLease, "finish", slow)
    result = cleanup(case)
    assert result["outcome"] == "inconclusive" and result["reason"] == "sandbox_cleanup_response_expired"
    assert result["journal_finish_confirmed"] and not case.store.inspect()[0]["closed"]


def test_slow_owner_close_cannot_return_an_expired_absence(case, monkeypatch):
    close = SandboxCleanupLease.close

    def slow(self):
        close(self)
        case.clock.value += 6

    monkeypatch.setattr(SandboxCleanupLease, "close", slow)
    result = cleanup(case)
    assert result["outcome"] == "inconclusive" and result["reason"] == "sandbox_cleanup_response_expired"
    assert result["journal_finish_confirmed"] and not case.store.inspect()[0]["closed"]


def test_monotonic_wall_disagreement_cannot_dispatch(case, monkeypatch):
    case.box.reload.side_effect = lambda: monkeypatch.setattr(module, "utc_now", lambda: case.wall() - timedelta(seconds=3))
    assert cleanup(case)["reason"] == "sandbox_cleanup_deadline_or_clock_changed"
    case.client.api.remove_container.assert_not_called()


def test_keyboard_interrupt_records_inconclusive_and_preserves_barrier(case):
    case.client.api.remove_container.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        cleanup(case)
    row = case.store.inspect(receipt=True)[0]
    assert row["stage"] == "cleanup_finished" and not row["closed"]
    assert row["receipt"]["sandbox_cleanup"]["report"]["reason"] == "sandbox_cleanup_interrupted"
    assert cleanup(case)["outcome"] == "rejected"


def test_journal_file_replacement_after_inspection_blocks_removal(case):
    def replace():
        case.store.path.rename(case.store.path.with_suffix(".old"))
        case.store.path.touch()

    case.box.reload.side_effect = replace
    result = cleanup(case)
    assert result["outcome"] == "inconclusive"
    case.client.api.remove_container.assert_not_called()


def test_constraints_and_id_removal_serialize_through_installed_sdk_without_io(factory, monkeypatch):
    from agent.tools import bound_executor

    case = factory()
    case.box.remove.side_effect = TimeoutError("Synthetic interrupted cleanup")
    grant = issue(case)
    assert bound_executor.execute_bound_procedure(grant, case.graph)["state"] == "not_dispatched"
    reservation = grant.snapshot()["sandbox_intent"]
    args = case.client.containers.create.call_args.kwargs
    box = request_box(reservation)
    with closing(DockerClient(version="1.45")) as sdk:
        response = Mock()
        response.json.return_value = {"Id": box.id, "Warnings": []}
        post, delete = Mock(return_value=response), Mock(return_value=response)
        monkeypatch.setattr(sdk.api, "_post_json", post)
        monkeypatch.setattr(sdk.api, "_delete", delete)
        monkeypatch.setattr(sdk.api, "inspect_container", Mock(return_value={"Id": box.id}))
        sdk.containers.create(**args)
        data = post.call_args.kwargs["data"]
        box.attrs["Config"] = {key: data[key] for key in ("Cmd", "Entrypoint", "Env", "User", "Labels")}
        # These defaults describe inspect metadata, not an actual daemon inspection.
        box.attrs["HostConfig"] = {"Privileged": False, "PidMode": "", "IpcMode": "private", **data["HostConfig"]}
        box.attrs["Mounts"] = [{"Type": "bind", "Source": source, "Destination": target["bind"], "RW": False}
                               for source, target in args["volumes"].items()]
        assert module._owned(box, reservation, {grant.plan.instance_id}) == box.id
        sdk.api.remove_container(box.id, force=True, v=False)
        assert delete.call_args.args[0].endswith("/containers/" + box.id)
        assert delete.call_args.kwargs["params"] == {"v": False, "link": False, "force": True}
