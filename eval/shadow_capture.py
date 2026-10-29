"""Opt-in local evidence capture for shadow diagnosis/rechecks; no model, probes or repairs."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from agent.shadow import DependencySnapshot
from core.diagnostics import DiagnosticRequest, TransactionReader
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from eval.shadow_replay import _encoded, _write_new, freeze_inputs, implementation_manifest, save_inputs


class CaptureRejected(ValueError):
    """Fixed local eligibility category; never include raw SDK or application text."""


def declared_topology(export: dict, *, captured_at: datetime) -> DependencySnapshot:
    """Bind exact registered identities and declared edges, not observed call discovery."""
    if not isinstance(export, dict) or set(export) != {"services", "edges"}:
        raise CaptureRejected("invalid_topology_export")
    names, edges = export["services"], export["edges"]
    if (not isinstance(names, list) or len(names) != len(REGISTRY.services)
            or any(not isinstance(name, str) for name in names)
            or sorted(names) != sorted(REGISTRY.by_name)):
        raise CaptureRejected("topology_registry_mismatch")
    if (not isinstance(edges, list) or not 1 <= len(edges) <= 128
            or any(not isinstance(edge, (list, tuple)) or len(edge) != 2
                   or any(not isinstance(name, str) or name not in REGISTRY.by_name for name in edge)
                   for edge in edges)):
        raise CaptureRejected("invalid_topology_edges")
    edges = sorted(tuple(edge) for edge in edges)
    if len(set(edges)) != len(edges):
        raise CaptureRejected("duplicate_topology_edge")
    revision = sha256(_encoded({"services": sorted(names), "edges": edges,
                               "registry_digest": REGISTRY.digest})).hexdigest()
    return DependencySnapshot(source="neo4j-export", revision=f"neo4j-declared-{revision}",
        registry_digest=REGISTRY.digest, captured_at=captured_at, edges=tuple(edges))


def _latest_failed(rows, cutoff):
    if not rows:
        raise CaptureRejected("no_recent_transaction")
    latest = max(datetime.fromisoformat(row["measured_at"]) for row in rows)
    current = [row for row in rows if datetime.fromisoformat(row["measured_at"]) == latest]
    if len(current) != 1:
        raise CaptureRejected("ambiguous_latest_transaction")
    row = current[0]
    if not 0 <= (cutoff - latest).total_seconds() <= 90:
        raise CaptureRejected("no_recent_transaction")
    if row["outcome"] != "failed" or row["quality"] != "observed":
        raise CaptureRejected("latest_transaction_not_failed")
    return row["evidence_id"]


def capture_latest_inputs(path: Path, *, topology: DependencySnapshot, as_of: datetime):
    """Freeze a fresh latest failure, without accepting a root or injector-provided trigger."""
    if as_of.tzinfo is None or as_of > datetime.now(timezone.utc):
        raise CaptureRejected("invalid_capture_clock")
    topology = DependencySnapshot.model_validate(topology.model_dump(mode="json"))
    if topology.source != "neo4j-export" or not 0 <= (as_of - topology.captured_at).total_seconds() <= 30:
        raise CaptureRejected("fresh_graph_export_required")
    history = TransactionReader(path).window(
        DiagnosticRequest(service_id="frontend", window_seconds=900), now=as_of.timestamp())
    trigger = _latest_failed(history["observations"], as_of)
    inputs = freeze_inputs(path, symptom_evidence_id=trigger, as_of=as_of,
                           topology=topology, source_kind="retained-observation")
    # Reconcile the selection against the single-transaction bundle, not mutable episode summaries.
    if _latest_failed([row.payload for row in inputs.records if row.kind == "symptom"], as_of) != trigger:
        raise CaptureRejected("transaction_selection_changed")
    return inputs


def _require_observe_only(status):
    if not isinstance(status, dict):
        raise CaptureRejected("collector_not_ready")
    if status.get("delivery_enabled") is not False:
        raise CaptureRejected("automatic_delivery_not_disabled")
    evidence = status.get("evidence_store")
    if (type(status.get("schema_version")) is not int or status["schema_version"] != 2
            or status.get("heartbeat_fresh") is not True or not isinstance(status.get("state"), str)
            or status["state"] not in {"observe_only", "degraded"}
            or not isinstance(evidence, dict) or evidence.get("enabled") is not True
            or evidence.get("state") != "ready"):
        raise CaptureRejected("collector_not_ready")


def _load_topology():
    from graph.graph_client import GraphClient

    client = GraphClient()
    try:
        return client.get_dependency_topology()
    finally:
        client.close()


def capture_observations(audit_dir: Path, output_dir: Path, *, topology_loader=None,
                         status_reader=None, utc_now=None):
    """One exclusive local capture. A rejected capture cannot start diagnosis or be overwritten."""
    from core.telemetry import collector_status

    audit_dir, output_dir = Path(audit_dir), Path(output_dir)
    clock = utc_now or (lambda: datetime.now(timezone.utc))
    status_reader = status_reader or (lambda: collector_status(audit_dir))
    topology_loader = topology_loader or _load_topology
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    _write_new(output_dir / "started.json", {"kind": "retained-shadow-capture-started",
        "started_at": clock().isoformat(), "implementation": implementation_manifest(),
        "capture_source_sha256": {name: sha256((root / name).read_bytes()).hexdigest()
            for name in ("eval/shadow_capture.py", "graph/graph_client.py", "core/telemetry.py")},
        "real_model_calls": 0, "execution_authorized": False})
    result = {"schema_version": 1, "kind": "retained-shadow-capture", "outcome": "failed",
              "reason": None, "source_kind": "retained-observation", "input_digest": None,
              "as_of": None, "symptom_evidence_id": None, "real_model_calls": 0,
              "new_application_probes": 0, "execution_authorized": False,
              "causal_attribution": False, "live_freshness_verified": False,
              "recovery_verified": False, "source_authenticated": False}
    try:
        _require_observe_only(status_reader())
        export = topology_loader()
        topology = declared_topology(export, captured_at=clock())
        _write_new(output_dir / "declared-topology.json", topology.model_dump(mode="json"))
        cutoff = clock()
        result["as_of"] = cutoff.isoformat()
        inputs = capture_latest_inputs(audit_dir / "evidence.sqlite", topology=topology, as_of=cutoff)
        _require_observe_only(status_reader())
        if not 0 <= (clock() - cutoff).total_seconds() < 10:
            raise CaptureRejected("capture_time_exhausted")
        save_inputs(output_dir / "inputs.json", inputs)
        result.update(outcome="captured", reason="fresh_latest_failed_transaction",
                      input_digest=inputs.digest, symptom_evidence_id=inputs.symptom_evidence_id)
    except CaptureRejected as error:
        result.update(outcome="not_eligible", reason=str(error))
    except Exception:
        result.update(reason="invalid_or_unavailable_source")
    except BaseException:
        result.update(outcome="interrupted", reason="capture_interrupted")
        _write_new(output_dir / "result.json", result)
        raise
    _write_new(output_dir / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private capture directory; cannot be reused")
    parser.add_argument("--audit-dir", type=Path, help="Trusted local collector/evidence directory")
    args = parser.parse_args()
    if args.audit_dir is None:
        from core.config import settings

        args.audit_dir = settings.audit_dir
    try:
        result = capture_observations(args.audit_dir, args.output)
    except FileExistsError:
        parser.error("Use a new output directory; previous captures must be preserved")
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["outcome"] == "failed" else 0)


if __name__ == "__main__":
    main()
