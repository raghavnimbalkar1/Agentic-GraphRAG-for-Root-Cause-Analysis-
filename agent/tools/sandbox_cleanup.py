"""Explicit one-shot removal of an exact interrupted request sandbox. No service repair."""

from datetime import datetime
from pathlib import PurePosixPath
import re
from time import monotonic

from agent.procedure_execution import utc_now
from core.operation_journal import JournalDenied, OperationJournal, sandbox_reservation
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY

DEADLINE_SECONDS = 20


def _docker_client():
    from agent.tools.bound_executor import _docker_client as create

    return create()


def _owned(box, reservation, protected_ids):
    """Labels alone cannot turn a managed service or writable host mount into a request."""
    attrs = box.attrs
    config, host = attrs.get("Config", {}), attrs.get("HostConfig", {})
    labels = config.get("Labels") or {}
    if (not isinstance(box.id, str) or re.fullmatch(r"[a-f0-9]{64}", box.id) is None or box.id in protected_ids
            or attrs.get("Id") != box.id or box.name != reservation["name"]
            or attrs.get("Name") != "/" + reservation["name"] or attrs.get("Image") != reservation["image_id"]
            or labels.get("rca.role") != reservation["role"] or labels.get("rca.attempt") != reservation["nonce"]
            or REGISTRY.ownership_label in labels or any(key.startswith("com.docker.compose.") for key in labels)
            or config.get("Cmd") != ["bash", "/script/sop.sh"] or config.get("Entrypoint") not in (None, [])
            or config.get("User") != "1000:1000"
            or (config.get("Env") or []).count("RCA_CONTROL_MODE=intent-only") != 1
            or host.get("NetworkMode") != "none" or host.get("Privileged") is not False
            or host.get("ReadonlyRootfs") is not True or host.get("CapDrop") != ["ALL"]
            or host.get("Memory") != 128 * 1024 * 1024 or host.get("MemorySwap") != 128 * 1024 * 1024
            or host.get("NanoCpus") != 250_000_000 or host.get("PidsLimit") != 32
            or host.get("Tmpfs") != {"/tmp": "size=16m"}
            or host.get("CapAdd") not in (None, [])
            or host.get("SecurityOpt") not in (["no-new-privileges"], ["no-new-privileges:true"])
            or host.get("PidMode", "") not in ("", "private")
            or host.get("IpcMode", "") not in ("", "private")
            or any(host.get(key) for key in ("VolumesFrom", "Devices", "DeviceRequests"))
            or set(attrs.get("NetworkSettings", {}).get("Networks", {})) - {"none"}):
        raise JournalDenied("request_sandbox_identity_or_isolation_conflict")
    mounts = attrs.get("Mounts")
    if not isinstance(mounts, list):
        raise JournalDenied("request_sandbox_mount_conflict")
    paths = {}
    for mount in mounts:
        if mount.get("Type") == "tmpfs" and mount.get("Destination") == "/tmp":
            continue
        destination, source = mount.get("Destination"), mount.get("Source")
        if (mount.get("Type") != "bind" or mount.get("RW") is not False
                or destination not in {"/script/sop.sh", "/script/control_client.py"}
                or destination in paths or not isinstance(source, str)):
            raise JournalDenied("request_sandbox_mount_conflict")
        path = PurePosixPath(source)
        if not path.is_absolute() or ".." in path.parts or path.name != PurePosixPath(destination).name:
            raise JournalDenied("request_sandbox_mount_conflict")
        paths[destination] = path.parent
    if (set(paths) != {"/script/sop.sh", "/script/control_client.py"}
            or len(set(paths.values())) != 1 or not next(iter(paths.values())).name.startswith("rca-bound-sop-")):
        raise JournalDenied("request_sandbox_mount_conflict")
    return box.id


def cleanup_request_sandbox(journal, identifier, *, expected_version, approve_cleanup=False):
    """No retry, broad prune, model call, health probe, SOP or deployment-barrier release."""
    report = {"outcome": "rejected", "reason": "explicit_versioned_sandbox_cleanup_required",
        "execution_authorized": False, "automatic_retry_allowed": False, "recovery_verified": False,
        "current_state_closed": False, "real_model_calls": 0, "new_application_probes": 0,
        "remove_dispatched": False, "removal_acknowledged": False, "absence_observed": False,
        "journal_finish_confirmed": False}
    if type(journal) is not OperationJournal or approve_cleanup is not True:
        return report
    started, wall = monotonic(), utc_now()
    lease, client = None, None

    def fresh():
        elapsed = monotonic() - started
        if not 0 <= elapsed < DEADLINE_SECONDS or abs((utc_now() - wall).total_seconds() - elapsed) > 2:
            raise JournalDenied("sandbox_cleanup_deadline_or_clock_changed")

    def absent(client, name):
        from docker.errors import NotFound

        fresh()
        try:
            client.containers.get(name)
        except NotFound:
            fresh()
            return
        raise JournalDenied("request_sandbox_still_present_or_name_reused")

    try:
        from docker.errors import NotFound

        lease = journal.begin_sandbox_cleanup(identifier, expected_version=expected_version, approve_cleanup=True)
        report.update(outcome="inconclusive", reason="sandbox_cleanup_unavailable")
        identity = lease.snapshot["identity"]
        if identity["environment_id"] != REGISTRY.environment_id or identity["registry_digest"] != REGISTRY.digest:
            raise JournalDenied("sandbox_cleanup_deployment_mismatch")
        reservation = sandbox_reservation(lease.snapshot["receipt"]["sandbox_intent"])
        protected_ids = {identity["instance_id"], *lease.snapshot["receipt"].get(
            "reconciliation_basis", {}).get("instances", {}).values()}
        fresh()
        client = _docker_client()
        fresh()
        try:
            box = client.containers.get(reservation["name"])
        except NotFound:
            report["reason"] = "reserved_request_already_absent"
        else:
            box.reload()
            fresh()
            container_id = _owned(box, reservation, protected_ids)
            lease.admit_removal(container_id)
            fresh()  # Durable I/O cannot extend the mutation deadline.
            # Pin the inspected immutable ID. A reused name must never redirect deletion.
            box = client.containers.get(container_id)
            box.reload()
            fresh()
            if _owned(box, reservation, protected_ids) != container_id:
                raise JournalDenied("request_sandbox_instance_changed")
            lease.check()
            fresh()
            report["remove_dispatched"] = True
            client.api.remove_container(container_id, force=True, v=False)
            report["removal_acknowledged"] = True
            absent(client, container_id)
            report["reason"] = "reserved_request_removal_observed"
        absent(client, reservation["name"])
        report["absence_observed"] = True
        report["checked_at"] = utc_now().isoformat()
        closing_client, client = client, None
        closing_client.close()
        fresh()
        if not 0 <= (utc_now() - datetime.fromisoformat(report["checked_at"])).total_seconds() < 5:
            raise JournalDenied("sandbox_absence_observation_expired")
        report["outcome"] = "absent"
    except JournalDenied as error:
        report["reason"] = str(error)
    except Exception:
        report["reason"] = "sandbox_cleanup_runtime_unconfirmed"
    except BaseException:
        report.update(outcome="inconclusive", reason="sandbox_cleanup_interrupted")
        raise
    finally:
        try:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    report.update(outcome="inconclusive", reason="sandbox_cleanup_resource_close_unconfirmed")
            if lease is not None:
                try:
                    result = lease.finish(dict(report))
                    report.update(journal_finish_confirmed=True, journal=result)
                except Exception:
                    report.update(outcome="inconclusive", reason="sandbox_cleanup_finish_unconfirmed")
        finally:
            if lease is not None:
                try:
                    lease.close()
                except Exception:
                    report.update(outcome="inconclusive", reason="sandbox_cleanup_owner_close_unconfirmed")
    if report["outcome"] == "absent":
        try:
            fresh()
            if not 0 <= (utc_now() - datetime.fromisoformat(report["checked_at"])).total_seconds() < 5:
                raise JournalDenied("sandbox_absence_observation_expired")
        except JournalDenied:
            report.update(outcome="inconclusive", reason="sandbox_cleanup_response_expired")
    return report
