"""Read-only Neo4j/local implementation audit. Never runs a SOP or model."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def audit(client, *, project_root=None):
    from graph.procedure_catalog import capture_procedure_catalog

    catalog = capture_procedure_catalog(client, project_root=project_root)
    return {"kind": "procedure-contract-audit", "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(), "catalog_digest": catalog.digest,
        "skills": len(catalog.contracts), "bindings": sum(len(row.applies_to) for row in catalog.contracts),
        "fallbacks": len(catalog.fallbacks), "catalog": catalog.model_dump(mode="json"),
        "contract_digests": {row.name: row.digest for row in catalog.contracts},
        "real_model_calls": 0, "execution_authorized": False, "runtime_preconditions_checked": False,
        "durability_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private local audit path; no overwrite")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Use a new output path")
    marker = args.output.with_name(args.output.name + ".started.json")
    try:
        _write_new(marker, {"kind": "procedure-contract-audit-start", "real_model_calls": 0,
                            "execution_authorized": False})
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "output_unavailable_or_reused"}))
        return 1
    client = None
    try:
        from core.config import settings
        from core.procedure_contracts import require_bundled_layout
        from graph.graph_client import GraphClient

        require_bundled_layout(settings.sops_dir)
        client = GraphClient()
        report = audit(client)
    except Exception:
        report = {"kind": "procedure-contract-audit-failed", "reason": "catalog_or_implementation_unavailable",
                  "real_model_calls": 0, "execution_authorized": False}
    finally:
        if client is not None:
            client.close()
    try:
        _write_new(args.output, report)
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "report_write_failed", "start_marker_retained": True}))
        return 1
    passed = report["kind"] == "procedure-contract-audit"
    print(json.dumps({"outcome": "audited" if passed else "rejected", "skills": report.get("skills", 0),
        "bindings": report.get("bindings", 0), "real_model_calls": 0, "execution_authorized": False}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
