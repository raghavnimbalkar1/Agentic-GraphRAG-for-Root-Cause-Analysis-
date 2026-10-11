"""Versioned controlled-localization trials. No fault injection or live writes."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from core.audit import write_json_atomic
from eval.controlled import ControlledGraph, load_fixture
from eval.metrics import score_prediction, summarize
from eval.protocol import check_call_budget, matched_fixture, select_scenarios

RESULTS = Path(__file__).parent / "results"


def metadata(kind: str) -> dict:
    from core.config import settings
    packages = {}
    for package in ("langgraph", "neo4j", "docker", "pydantic", "langchain-core"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parents[1], check=False).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                           cwd=Path(__file__).parents[1], check=False)
    return {"schema_version": 2, "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": revision, "provider": settings.llm_provider.value,
            "model": settings.llm_model, "temperature": 0,
            "llm_timeout_s": settings.llm_timeout, "llm_max_tokens": settings.llm_max_tokens,
            "working_tree_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
            "packages": packages}


def result_path(kind: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RESULTS / f"{kind}_{stamp}_{uuid4().hex[:8]}.json"


def measure(system: str, scenario: dict, rep: int, predict) -> dict:
    started = time.perf_counter()
    try:
        prediction = dict(predict())
        prediction.setdefault("outcome", "root" if prediction.get("root") is not None else "review")
        if ("root" not in prediction or (prediction["root"] is not None and not isinstance(prediction["root"], str))
                or not isinstance(prediction.get("potential_blast"), list)
                or not all(isinstance(name, str) for name in prediction["potential_blast"])
                or prediction["outcome"] not in {"root", "none", "review"}
                or ((prediction["outcome"] == "root") != (prediction["root"] is not None))
                or (prediction["outcome"] != "root" and prediction["potential_blast"])):
            raise ValueError("Prediction does not match the root/blast schema")
    except Exception as exc:
        prediction = {"root": None, "outcome": "review", "potential_blast": [], "tokens": None,
                      "error": f"{type(exc).__name__}: {exc}"}
    prediction["latency_s"] = time.perf_counter() - started
    return {"system": system, "scenario_id": scenario["id"], "rep": rep,
            "depth": scenario["depth"], "truth_root": scenario["truth_root"],
            "block": scenario.get("block", "legacy_positive"),
            "alert_specificity": scenario.get("alert_specificity"),
            "truth_outcome": scenario.get("truth_outcome", "root"),
            "truth_potential_blast": scenario["truth_potential_blast"],
            **score_prediction(prediction, scenario)}


def run(reps: int, with_llm: bool = False, with_vector: bool = False,
        identifiers: list[str] | None = None, max_calls: int = 30, protocol: str = "matched") -> Path:
    from graph.graph_client import GraphClient
    if not 1 <= reps <= 100 or (with_vector and not with_llm):
        raise ValueError("Invalid repetition count or vector/model combination")
    fixture = select_scenarios(matched_fixture() if protocol == "matched" else load_fixture(), identifiers)
    planned_calls = len(fixture["scenarios"]) * reps * ((2 + int(with_vector)) if with_llm else 0)
    check_call_budget(planned_calls, max_calls)
    client = GraphClient()
    baselines = {}
    if with_llm:
        from eval.baselines.zero_shot import ZeroShotBaseline
        from eval.baselines.topology_text import TopologyTextBaseline
        baselines["ZeroShotWithObservations"] = ZeroShotBaseline()
        baselines["TopologyTextWithObservations"] = TopologyTextBaseline(fixture["edges"])
    if with_vector:
        if not with_llm:
            raise ValueError("Vector inference requires --with-llm")
        from eval.baselines.vector_rag import VectorRAGBaseline
        vector = VectorRAGBaseline()
        vector.build_index()
        baselines["VectorRAGWithObservations"] = vector
    trials = []
    path = result_path("localisation_v3")
    output = {"metadata": metadata("controlled_localisation"), "fixture": fixture,
              "call_budget": {"maximum": max_calls, "planned": planned_calls, "attempted": 0},
              "methodology": {
                  "common_inputs": "Same alert and controlled per-service health snapshot for every system",
                  "topology_treatment": "GraphTraversal and TopologyText have identical edges; ZeroShot and VectorRAG do not",
                  "control": "GraphTraversal is deterministic Q1, not evidence of an LLM localization contribution",
                  "blast": "Potential dependency reachability, excluding root; not observed live impact",
                  "timing": "Localization latency only; no execution, detection or MTTR measured",
                  "failures": "All attempted trials retained; failures score zero; missing usage is null",
                  "negative_controls": "No false root credit; negative decisions scored separately from positive root accuracy",
              }, "trials": trials}
    try:
        for scenario in fixture["scenarios"]:
            names = sorted({name for edge in fixture["edges"] for name in edge})
            snapshot = {name: scenario["observations"].get(name, "HEALTHY") for name in names}
            ages = scenario.get("observation_age_seconds", {name: 0 for name in names})
            alert = {**scenario["alert"], "observations": snapshot, "observation_age_seconds": ages}
            with ControlledGraph(client, fixture["edges"], snapshot) as graph:
                def predict_graph():
                    return graph.predict(alert, ages)

                for rep in range(reps):
                    trials.append(measure("GraphTraversalNoLLM", scenario, rep, predict_graph))
                    for name, baseline in baselines.items():
                        def predict_baseline(baseline=baseline):
                            output["call_budget"]["attempted"] += 1
                            result = baseline.resolve(alert)
                            return {"root": result.root_cause, "potential_blast": result.blast_radius,
                                    "outcome": result.outcome,
                                    "latency_s": result.latency_s, "tokens": result.tokens_used,
                                    "error": result.error, "reasoning": result.reasoning,
                                    "raw_response": result.raw_response}
                        trials.append(measure(name, scenario, rep, predict_baseline))
                    output["summary"] = summarize(trials)
                    output["summary_by_block"] = {block: summarize([row for row in trials if row["block"] == block])
                                                  for block in sorted({row["block"] for row in trials})}
                    write_json_atomic(path, output)
        output["completed"] = True
        write_json_atomic(path, output)
    finally:
        client.close()
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate fixtures without Neo4j or model calls")
    parser.add_argument("--with-llm", action="store_true", help="Make configured model calls")
    parser.add_argument("--with-vector", action="store_true", help="Load embeddings and run vector baseline")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=30, help="Explicit maximum number of model requests")
    parser.add_argument("--scenario", action="append", help="Select scenario ID; repeat for a pilot subset")
    parser.add_argument("--protocol", choices=["matched", "legacy"], default="matched")
    args = parser.parse_args()
    if not 1 <= args.reps <= 100:
        parser.error("--reps must be 1..100")
    if args.with_vector and not args.with_llm:
        parser.error("--with-vector requires --with-llm")
    if args.dry_run:
        fixture = select_scenarios(matched_fixture() if args.protocol == "matched" else load_fixture(), args.scenario)
        planned = len(fixture["scenarios"]) * args.reps * ((2 + int(args.with_vector)) if args.with_llm else 0)
        check_call_budget(planned, args.max_calls)
        print(f"Validated {len(fixture['scenarios'])} controlled scenarios; {planned} model calls planned. No results measured.")
        return
    print(run(args.reps, args.with_llm, args.with_vector, args.scenario, args.max_calls, args.protocol))


if __name__ == "__main__":
    main()
