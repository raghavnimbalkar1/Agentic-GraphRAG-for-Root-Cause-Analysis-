"""Read-only procedure/runtime inspection. Never dispatches an action or model."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from core.procedure_contracts import PreflightPermissions, RuntimeIdentity, bind_plan, check_preflight
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord
from graph.procedure_catalog import capture_procedure_catalog
from graph.scripts.audit_procedures import _write_new


def inspect_procedure(graph, docker, *, skill_name, service_id, incident_id, revision,
                      permissions=None, observe_fn=None, now_fn=None):
    """Caller-owned runtime clients. All target/effect permissions remain local."""
    if observe_fn is None:
        from core.health import observe

        observe_fn = observe
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    result = {"kind": "procedure-preflight-inspection", "skill_name": skill_name, "service_id": service_id,
              "real_model_calls": 0, "action_dispatched": False, "execution_authorized": False,
              "eligible": False}
    try:
        if service_id not in REGISTRY.by_name:
            raise ValueError("Unregistered service")
        catalog = capture_procedure_catalog(graph)
        contract = catalog.get(skill_name)
        result.update(catalog_digest=catalog.digest, contract_digest=contract.digest,
                      expected_effects=list(contract.policy.effects), durability=contract.policy.durability,
                      required_permission=contract.policy.permission)
        if service_id not in contract.applies_to:
            result["reason"] = "procedure_target_mismatch"
            return result

        def probe():
            observation = observe_fn(docker, service_id)
            return ObservationRecord(schema_version=2, service_id=service_id,
                instance_id=observation.instance_id, measured_at=observation.measured_at,
                collected_at=now_fn(), status=observation.status, detail=observation.detail,
                measurements=observation.measurements,
                quality="unknown" if observation.status == "UNKNOWN" else "observed",
                environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
                registry_digest=REGISTRY.digest)

        first = probe()
        result["observed_status"] = first.status
        result["observations"] = [first.model_dump(mode="json")]
        if first.status != contract.trigger_condition:
            result["reason"] = "no_matching_observed_condition"
            return result
        plan = bind_plan(catalog, skill_name=skill_name, observation=first,
                         incident_id=incident_id, revision=revision, now=now_fn())
        result["plan"] = plan.model_dump(mode="json")
        # Capture a second observation and catalog. The old plan cannot substitute
        # its cached condition, identity or implementation for this preflight.
        current = capture_procedure_catalog(graph)
        result["current_catalog_digest"] = current.digest
        fresh = probe()
        result["observations"].append(fresh.model_dump(mode="json"))
        target = docker.containers.get(fresh.instance_id)
        target.reload()
        labels = target.attrs.get("Config", {}).get("Labels", {})
        runtime = RuntimeIdentity(service_id=service_id, container_name=target.name,
            instance_id=target.id, ownership_label=REGISTRY.ownership_label,
            ownership_value=labels.get(REGISTRY.ownership_label), captured_at=now_fn(),
            cpu_quota=target.attrs.get("HostConfig", {}).get("CpuQuota", 0))
        result["runtime"] = runtime.model_dump(mode="json")
        check = check_preflight(plan, catalog=current, observation=fresh, incident_id=incident_id,
            revision=revision, now=now_fn(), runtime=runtime, permissions=permissions)
        return {**result, **check, "plan": plan.model_dump(mode="json"),
                "runtime": runtime.model_dump(mode="json"),
                "fresh_status": fresh.status, "fresh_evidence_id": fresh.evidence_id,
                "current_catalog_digest": current.digest}
    except Exception:
        return {**result, "reason": "invalid_or_unavailable_preflight"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--revision", required=True, help="Trusted plan revision, 64 hex characters")
    parser.add_argument("--output", required=True, type=Path, help="New private report; never overwrite")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Use a new output path")
    # Validate identifiers before loading settings or opening a runtime client.
    import re

    if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", args.incident_id) is None
            or re.fullmatch(r"[a-f0-9]{64}", args.revision) is None
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", args.skill) is None
            or args.service not in REGISTRY.by_name):
        parser.error("Invalid procedure, service, incident or revision")
    try:
        _write_new(args.output.with_name(args.output.name + ".started.json"), {
            "kind": "read-only-procedure-preflight-start", "skill": args.skill, "service": args.service,
            "revision": args.revision, "real_model_calls": 0, "execution_authorized": False})
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "output_unavailable_or_reused"}))
        return 1
    client = graph = None
    try:
        import docker
        from core.config import settings
        from core.procedure_contracts import require_bundled_layout
        from graph.graph_client import GraphClient

        require_bundled_layout(settings.sops_dir)
        graph = GraphClient()
        client = docker.DockerClient(base_url=settings.docker_host, timeout=10)
        result = inspect_procedure(graph, client, skill_name=args.skill, service_id=args.service,
            incident_id=args.incident_id, revision=args.revision, permissions=PreflightPermissions())
    except Exception:
        result = {"kind": "procedure-preflight-inspection", "reason": "runtime_unavailable",
                  "eligible": False, "real_model_calls": 0, "action_dispatched": False, "execution_authorized": False}
    finally:
        if client is not None:
            client.close()
        if graph is not None:
            graph.close()
    try:
        _write_new(args.output, result)
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "report_write_failed", "start_marker_retained": True}))
        return 1
    print(json.dumps({key: result[key] for key in ("eligible", "reason", "real_model_calls", "action_dispatched", "execution_authorized")}))
    return 0 if result["eligible"] else 2


if __name__ == "__main__":
    main()
