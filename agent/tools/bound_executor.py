"""Operator-only controlled SOP handoff. No network, credentials or Docker socket in the sandbox."""

from hashlib import sha256
from datetime import datetime
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from agent.procedure_execution import ExecutionDenied, ProcedureExecutionGrant, utc_now
from core.procedure_contracts import RuntimeIdentity, require_bundled_layout
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord

ROOT = Path(__file__).resolve().parents[2]


def _docker_client():
    import docker
    from core.config import settings

    return docker.DockerClient(base_url=settings.docker_host, timeout=5)


def _observe_scope(client, services):
    from core.health import observe_many

    return observe_many(client, services)


def _execution_context(graph, grant):
    """Read the current collector/transaction/topology, not only the old diagnosis."""
    from core.config import settings
    from core.diagnostics import DiagnosticRequest, TransactionReader
    from core.telemetry import collector_status
    from eval.shadow_capture import _latest_failed, _require_observe_only, declared_topology

    try:
        _require_observe_only(collector_status(settings.audit_dir))
        with grant._issuer._lock:
            context, _ = grant._current()
        topology = declared_topology(graph.get_dependency_topology(), captured_at=utc_now())
        old = {key: value for key, value in context["topology"].items() if key != "captured_at"}
        if topology.model_dump(mode="json", exclude={"captured_at"}) != old:
            raise ExecutionDenied("execution_topology_changed")
        cutoff = utc_now()
        rows = TransactionReader(settings.audit_dir / "evidence.sqlite").window(
            DiagnosticRequest(service_id="frontend", window_seconds=900), now=cutoff.timestamp())["observations"]
        identifier = _latest_failed(rows, cutoff)
        latest = next(row for row in rows if row["evidence_id"] == identifier)
        prior = context["symptom"]
        if (latest["workflow"] != prior["workflow"] or latest["environment_id"] != prior["environment_id"]
                or datetime.fromisoformat(latest["measured_at"]) < datetime.fromisoformat(prior["measured_at"])
                or [(row["step"], row["success"]) for row in latest["steps"]] !=
                   [(row["step"], row["success"]) for row in prior["steps"]]):
            raise ExecutionDenied("latest_failed_symptom_changed")
        _require_observe_only(collector_status(settings.audit_dir))
        return {"checked_at": cutoff.isoformat(), "symptom": latest, "topology_revision": topology.revision}
    except ExecutionDenied:
        raise
    except Exception:
        raise ExecutionDenied("current_collector_transaction_or_topology_unavailable") from None


def _freeze_scripts(contract, directory):
    expected = {row.path: row.sha256 for row in contract.implementation}
    paths = {"sop.sh": contract.script_path.removeprefix("/"), "control_client.py": "sops/control_client.py"}
    volumes = {}
    for name, relative in paths.items():
        path = ROOT / relative
        if path.is_symlink() or relative not in expected:
            raise ExecutionDenied("script_snapshot_rejected")
        with path.open("rb") as stream:
            data = stream.read(131073)
        if len(data) > 131072 or sha256(data).hexdigest() != expected[relative]:
            raise ExecutionDenied("script_implementation_changed")
        frozen = directory / name
        frozen.write_bytes(data)
        frozen.chmod(0o444)
        volumes[str(frozen)] = {"bind": f"/script/{name}", "mode": "ro"}
    return volumes


def _intent(raw, expected):
    def exact(pairs):
        if pairs != [("operation", expected)]:
            raise ExecutionDenied("sandbox_intent_rejected")
        return dict(pairs)

    if not raw or len(raw) > 512:
        raise ExecutionDenied("sandbox_intent_rejected")
    try:
        value = json.loads(raw, object_pairs_hook=exact)
    except (ValueError, UnicodeError):
        raise ExecutionDenied("sandbox_intent_rejected") from None
    if value != {"operation": expected}:
        raise ExecutionDenied("sandbox_intent_rejected")


def _sandbox_intent(client, grant, volumes, image):
    from docker.errors import NotFound

    nonce = uuid4().hex
    name, box, cleaned = f"sop-intent-{nonce[:12]}", None, False
    stream = None
    try:
        timeout = min(grant.contract.timeout_seconds, math.floor(grant.remaining_seconds()))
        if timeout < 1:
            raise ExecutionDenied("sandbox_deadline_exhausted")
        box = client.containers.create(image=image, command=["bash", "/script/sop.sh"], entrypoint=[],
            name=name, network_mode="none", user="1000:1000", privileged=False, cap_drop=["ALL"],
            security_opt=["no-new-privileges"], read_only=True, tmpfs={"/tmp": "size=16m"},
            mem_limit="128m", memswap_limit="128m", nano_cpus=250_000_000, pids_limit=32,
            volumes=volumes, environment={"RCA_CONTROL_MODE": "intent-only"},
            labels={"rca.role": "bound-sop-intent", "rca.attempt": nonce}, healthcheck={"test": ["NONE"]})
        box.start()
        if box.wait(timeout=timeout).get("StatusCode") != 0:
            raise ExecutionDenied("sandbox_request_failed")
        stream = box.logs(stream=True, stdout=True, stderr=False, tail=2)
        raw = bytearray()
        for chunk in stream:
            if not isinstance(chunk, bytes) or len(raw) + len(chunk) > 512:
                raise ExecutionDenied("sandbox_intent_rejected")
            raw.extend(chunk)
        _intent(bytes(raw), grant.contract.policy.operation)
    finally:
        stream_closed = stream is None
        try:
            if stream is not None:
                if hasattr(stream, "close"):
                    stream.close()
                stream_closed = True
        except Exception:
            pass
        finally:
            try:
                if box is None:
                    candidate = client.containers.get(name)
                    labels = candidate.attrs.get("Config", {}).get("Labels", {})
                    if labels.get("rca.attempt") != nonce or labels.get("rca.role") != "bound-sop-intent":
                        raise ExecutionDenied("sandbox_cleanup_identity_unknown")
                    box = candidate
                box.remove(force=True)
                cleaned = stream_closed
            except NotFound:
                cleaned = stream_closed
            except Exception:
                cleaned = False
            finally:
                grant.record_sandbox(cleaned=cleaned, image_id=image)
    if not cleaned:
        raise ExecutionDenied("sandbox_cleanup_not_confirmed")


def execute_bound_procedure(grant, graph):
    """Caller must hold a real grant from its current controller, not an eligible plan.

    Executes one fixed host-controller operation after a network-isolated SOP
    request and fresh scope preflight. Does not verify recovery or retry effects.
    Not exposed through the dashboard, alert worker, model or public API.
    """
    if type(grant) is not ProcedureExecutionGrant:
        return {"state": "refused", "reason": "opaque_execution_grant_required", "action_dispatched": False,
                "operation_confirmed": False, "recovery_verified": False, "execution_authorized": False,
                "request_outcome": "rejected", "request_action_dispatched": False}
    try:
        grant.begin()
    except Exception:
        return {**grant.snapshot(), "request_rejected": "grant_not_startable",
                "request_outcome": "rejected", "request_action_dispatched": False}

    client, reason = None, "execution_adapter_unavailable_or_failed"
    try:
        from core.config import settings
        from graph.procedure_catalog import capture_procedure_catalog
        from agent.evidence_policy import screening_contract
        from agent.tools.control_gateway import ControlSession

        require_bundled_layout(settings.sops_dir)
        catalog = capture_procedure_catalog(graph)
        if catalog.digest != grant.plan.catalog_digest:
            raise ExecutionDenied("catalog_changed")
        with TemporaryDirectory(prefix="rca-bound-sop-") as directory:
            volumes = _freeze_scripts(grant.contract, Path(directory))
            client = _docker_client()
            image = client.images.get(settings.sop_executor_image).id
            if not isinstance(image, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None:
                raise ExecutionDenied("executor_image_identity_required")
            _sandbox_intent(client, grant, volumes, image)
        # The sandbox is removed before any target mutation is attempted.
        with grant._issuer._lock:
            context, _ = grant._current()
            scope = screening_contract(context)["required_scope"]
        readings = _observe_scope(client, scope)
        observations = {name: ObservationRecord(schema_version=2, service_id=name, instance_id=row.instance_id,
            measured_at=row.measured_at, collected_at=utc_now(), status=row.status, detail=f"Built-in health probe: {row.status}",
            measurements=row.measurements, quality="unknown" if row.status == "UNKNOWN" else "observed",
            environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest)
                        for name, row in readings.items()}
        target = client.containers.get(grant.plan.instance_id)
        target.reload()
        runtime = RuntimeIdentity(service_id=grant.plan.target, container_name=target.name, instance_id=target.id,
            ownership_label=REGISTRY.ownership_label,
            ownership_value=target.attrs.get("Config", {}).get("Labels", {}).get(REGISTRY.ownership_label),
            captured_at=utc_now(), cpu_quota=target.attrs.get("HostConfig", {}).get("CpuQuota", 0))
        catalog = capture_procedure_catalog(graph)
        context_check = _execution_context(graph, grant)
        control = ControlSession(client, ROOT / grant.contract.script_path.removeprefix("/"),
                                 grant.plan.target, grant.contract.timeout_seconds)
        try:
            if control.container_id != grant.plan.instance_id:
                raise ExecutionDenied("controlled_target_instance_changed")
            with grant.operation(catalog=catalog, observations=observations, runtime=runtime, context_check=context_check):
                result = control.authorize(control.token, grant.contract.policy.operation)
                if result != {"success": True, "action": grant.contract.policy.operation, "target": grant.plan.target}:
                    raise ExecutionDenied("controlled_operation_not_confirmed")
        finally:
            control.__exit__(None, None, None)
        reason = "verification_required"
    except ExecutionDenied as error:
        reason = str(error)
    except Exception:
        pass
    except BaseException:
        reason = "execution_interrupted"
        raise
    finally:
        grant.finish(reason)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    result = grant.snapshot()
    return {**result, "request_outcome": "finished", "request_action_dispatched": result["action_dispatched"]}
