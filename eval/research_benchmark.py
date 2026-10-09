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
        prediction = predict()
        if (not isinstance(prediction.get("root"), str)
                or not isinstance(prediction.get("potential_blast"), list)
                or not all(isinstance(name, str) for name in prediction["potential_blast"])):
            raise ValueError("Prediction does not match the root/blast schema")
    except Exception as exc:
        prediction = {"root": None, "potential_blast": [], "tokens": None,
                      "error": f"{type(exc).__name__}: {exc}"}
    prediction["latency_s"] = time.perf_counter() - started
    return {"system": system, "scenario_id": scenario["id"], "rep": rep,
            "depth": scenario["depth"], "truth_root": scenario["truth_root"],
            "truth_potential_blast": scenario["truth_potential_blast"],
            **score_prediction(prediction, scenario)}


def run(reps: int, with_llm: bool = False, with_vector: bool = False) -> Path:
    from graph.graph_client import GraphClient
    fixture = load_fixture()
    client = GraphClient()
    baselines = {}
    if with_llm:
        from eval.baselines.zero_shot import ZeroShotBaseline
        baselines["ZeroShotWithObservations"] = ZeroShotBaseline()
    if with_vector:
        if not with_llm:
            raise ValueError("Vector inference requires --with-llm")
        from eval.baselines.vector_rag import VectorRAGBaseline
        vector = VectorRAGBaseline()
        vector.build_index()
        baselines["VectorRAGWithObservations"] = vector
    trials = []
    path = result_path("localisation_v2")
    output = {"metadata": metadata("controlled_localisation"), "fixture": fixture,
              "methodology": {
                  "common_inputs": "Same alert and controlled per-service health snapshot for every system",
                  "topology_treatment": "GraphTraversal has dependency edges; other systems do not",
                  "control": "GraphTraversal is deterministic Q1, not evidence of an LLM localization contribution",
                  "blast": "Potential dependency reachability, excluding root; not observed live impact",
                  "timing": "Localization latency only; no execution, detection or MTTR measured",
                  "failures": "All attempted trials retained; failures score zero; missing usage is null",
              }, "trials": trials}
    try:
        for scenario in fixture["scenarios"]:
            names = sorted({name for edge in fixture["edges"] for name in edge})
            snapshot = {name: scenario["observations"].get(name, "HEALTHY") for name in names}
            alert = {**scenario["alert"], "observations": snapshot}
            with ControlledGraph(client, fixture["edges"], snapshot) as graph:
                def predict_graph():
                    result = graph.localise(alert)
                    return {"root": result.root_cause_node,
                            "potential_blast": graph.blast(result.root_cause_node), "tokens": 0,
                            "chain": result.dependency_chain, "error": None}

                for rep in range(reps):
                    trials.append(measure("GraphTraversalNoLLM", scenario, rep, predict_graph))
                    for name, baseline in baselines.items():
                        def predict_baseline(baseline=baseline):
                            result = baseline.resolve(alert)
                            return {"root": result.root_cause, "potential_blast": result.blast_radius,
                                    "latency_s": result.latency_s, "tokens": result.tokens_used,
                                    "error": result.error, "reasoning": result.reasoning,
                                    "raw_response": result.raw_response}
                        trials.append(measure(name, scenario, rep, predict_baseline))
                    output["summary"] = summarize(trials)
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
    args = parser.parse_args()
    if not 1 <= args.reps <= 100:
        parser.error("--reps must be 1..100")
    if args.with_vector and not args.with_llm:
        parser.error("--with-vector requires --with-llm")
    if args.dry_run:
        fixture = load_fixture()
        print(f"Validated {len(fixture['scenarios'])} controlled scenarios. No results measured.")
        return
    print(run(args.reps, args.with_llm, args.with_vector))


if __name__ == "__main__":
    main()
