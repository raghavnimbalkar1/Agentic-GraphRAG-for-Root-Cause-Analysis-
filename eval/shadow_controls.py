"""Offline controls for the shadow loop and a fixed policy; no external model calls."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.investigation import InvestigationSession
from agent.shadow import DependencySnapshot, ShadowRunner
from core.evidence import EvidenceStore
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation
from eval.baselines.investigation import FixedProbePolicy

CASES = {
    "payment": ({"paymentservice": "CONNECTION_REFUSED", "checkoutservice": "DEGRADED"}, "paymentservice"),
    "redis": ({"redis-cart": "OOM_KILLED", "cartservice": "DEGRADED", "checkoutservice": "DEGRADED"}, "redis-cart"),
    "payment-and-unrelated-cpu": ({"paymentservice": "CONNECTION_REFUSED", "adservice": "HIGH_CPU"}, "paymentservice"),
    "unrelated-cpu-only": ({"adservice": "HIGH_CPU"}, "unknown_condition"),
    "unknown-telemetry": ({"paymentservice": "UNKNOWN"}, "insufficient_evidence"),
    "multiple-candidates": ({"paymentservice": "CONNECTION_REFUSED", "redis-cart": "OOM_KILLED"}, "unresolved_alternatives"),
}
SYMPTOM_ID = "SYMOBS-" + "1" * 32


def seed_case(path, statuses):
    """Synthetic data only. Expected targets never enter the runner's inputs."""
    store = EvidenceStore(path)
    stamp = datetime.now(timezone.utc) - timedelta(seconds=3)
    steps = [{"step": name, "success": name != "checkout", "status_code": 500 if name == "checkout" else 200,
              "latency_s": 0.01} for name in ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]]
    provenance = {"environment_id": REGISTRY.environment_id, "registry_revision": REGISTRY.revision,
                  "registry_digest": REGISTRY.digest}
    symptom = SymptomObservation(**provenance, evidence_id=SYMPTOM_ID, started_at=stamp - timedelta(seconds=1),
        measured_at=stamp, collected_at=stamp, outcome="failed", quality="observed", steps=steps)
    store.record_symptom(symptom)
    for index, name in enumerate(REGISTRY.by_name):
        status = statuses.get(name, "HEALTHY")
        store.append_observations([ObservationRecord(**provenance, schema_version=2,
            evidence_id=f"OBS-{index + 1:032x}", service_id=name, instance_id=f"{index + 1:064x}",
            measured_at=stamp, collected_at=stamp, status=status, quality="unknown" if status == "UNKNOWN" else "observed",
            detail="Synthetic offline component observation")])
    return store


def fixture_topology(cutoff):
    fixture = Path(__file__).with_name("fixtures") / "localisation.json"
    edges = json.loads(fixture.read_text())["edges"]
    return DependencySnapshot(source="declared-fixture", revision="boutique-declared-fixture-v1",
        registry_digest=REGISTRY.digest, captured_at=cutoff, edges=edges)


def _explicit_policy_terminal(report):
    result = report["result"]
    last = report["turns"][-1] if report["turns"] else {}
    action = {"proposal": "propose", "abstained": "abstain"}.get(result["outcome"])
    return bool(action and report["termination_reason"] == result["outcome"]
                and (last.get("decision") or {}).get("action") == action
                and last.get("result") == result)


def assess_control(report, *, expected, clue_service, clue_status=None):
    """Post-run software-control scoring only; expected labels never enter a policy."""
    result = report["result"]
    explicit_terminal = _explicit_policy_terminal(report)
    prediction = result.get("proposal", {}) if result["outcome"] == "proposal" else result
    matches = result["outcome"] == expected["outcome"] and all(
        prediction.get(key) == value for key, value in expected.items() if key != "outcome")
    feedback = [item for turn in report["turns"] for item in turn.get("feedback", [])
                if item.get("outcome") == "ok" and item.get("tool") in {"component_window", "resource_window"}]
    clues = [item["data"] for item in feedback if item["data"]["service_id"] == clue_service]
    if clue_status == "UNKNOWN":
        clue_seen = any(data["summary"]["status_at_window_end"] == "UNKNOWN"
                        and data["summary"]["unknown_samples"] > 0 for data in clues)
    else:
        clue_seen = any(data["summary"]["fresh_at_cutoff"] and data["samples"]
                        and (clue_status is None or data["summary"]["status_at_window_end"] == clue_status)
                        for data in clues)
    return {"terminal_matches_control": matches, "explicit_policy_terminal": explicit_terminal,
            "component_reads": len(feedback), "required_clue_seen": clue_seen,
            "control_passed": explicit_terminal and matches and bool(feedback) and clue_seen,
            "causal_accuracy_measured": False}


def run_controls(*, screening="provisional", context_profile="standard"):
    results = []
    with TemporaryDirectory() as directory:
        for name, (statuses, expected) in CASES.items():
            store = seed_case(Path(directory) / f"{name}.sqlite", statuses)
            cutoff = datetime.now(timezone.utc)
            session = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
            report = ShadowRunner(session, fixture_topology(cutoff), FixedProbePolicy(), screening=screening,
                                  context_profile=context_profile).run()
            result = report["result"]
            actual = result.get("proposal", {}).get("service_id") if result["outcome"] == "proposal" else result.get("reason")
            results.append({"case": name, "expected": expected, "actual": actual,
                            "passed": actual == expected and _explicit_policy_terminal(report),
                            "report": report})
    return {"schema_version": 1, "kind": "synthetic-software-controls", "real_model_calls": 0,
            "screening": screening, "context_profile": context_profile,
            "causal_accuracy_measured": False, "passed": all(row["passed"] for row in results), "controls": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional local control transcript; may contain deployment metadata")
    parser.add_argument("--screened", action="store_true", help="Require declared-scope evidence screening")
    parser.add_argument("--compact", action="store_true", help="Use lossless shared metadata context")
    args = parser.parse_args()
    result = run_controls(screening="dependency" if args.screened else "provisional",
                          context_profile="compact" if args.compact else "standard")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({"passed": result["passed"], "controls": len(result["controls"]),
                      "real_model_calls": 0, "kind": result["kind"], "screening": result["screening"],
                      "context_profile": result["context_profile"]}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
