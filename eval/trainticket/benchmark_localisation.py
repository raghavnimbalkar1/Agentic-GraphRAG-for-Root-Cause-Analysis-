"""
eval/trainticket/benchmark_localisation.py

Depth-stratified LOCALISATION benchmark on the TrainTicket topology (depths 1→7).
Runs the EXACT same Q1 traversal used in production (GraphClient.get_root_cause,
pointed at the isolated :TTService graph) against a topology-blind zero-shot LLM
given the identical alert and the TrainTicket service catalogue.

Vector-RAG is intentionally omitted here: it retrieves SOP documents, and there is
no TrainTicket SOP corpus (that is the remediation layer, i.e. future work). This
is a localisation study — does dependency-graph traversal beat text inference as the
cascade deepens — which is exactly the paper's central question, now at 7 hops.

Run:  python -m eval.trainticket.benchmark_localisation
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS = PROJECT_ROOT / "eval" / "results" / "trainticket_localisation.json"

from eval.trainticket.topology import (  # noqa: E402
    TT_SERVICES, TT_SCENARIOS, TT_LABEL, load_topology, reset_topology,
)

_TT_CONDITION = "DOWN"          # any non-HEALTHY value makes Q1 treat the node as root
_TT_SURFACE_ERROR = "HIGH_ERROR_RATE"


def _graphrag_localise(gc, sc: dict) -> dict:
    """GraphRAG localisation on TrainTicket: briefly mark the scenario root
    unhealthy in the isolated :TTService graph, run the real Q1 traversal
    (node_label=TTService), restore. Never touches the live :Service demo."""
    t0 = time.perf_counter()
    gc._run(f"MATCH (s:{TT_LABEL} {{name:$n}}) SET s.status=$c",
            n=sc["root"], c=_TT_CONDITION)
    try:
        res = gc.get_root_cause(sc["alert_service"], _TT_SURFACE_ERROR,
                                node_label=TT_LABEL, max_hops=12)
    finally:
        gc._run(f"MATCH (s:{TT_LABEL} {{name:$n}}) SET s.status='HEALTHY'", n=sc["root"])
    return {"root": res.root_cause_node, "chain": res.dependency_chain,
            "depth": res.depth, "correct": res.root_cause_node == sc["root"],
            "latency_s": round(time.perf_counter() - t0, 3)}


def run_tt_comparison(gc, sc: dict, zero_shot=None) -> dict:
    """One scenario through GraphRAG traversal + zero-shot LLM. `zero_shot` is a
    ZeroShotBaseline(known_services=TT_SERVICES); pass one in to reuse it."""
    if zero_shot is None:
        from eval.baselines.zero_shot import ZeroShotBaseline
        zero_shot = ZeroShotBaseline(known_services=TT_SERVICES)
    gr = _graphrag_localise(gc, sc)
    alert = {"service": sc["alert_service"], "error_type": _TT_SURFACE_ERROR,
             "message": sc["message"]}
    b1 = zero_shot.resolve(alert)
    return {
        "id": sc["id"], "depth": sc["depth"], "truth": sc["root"],
        "alert_service": sc["alert_service"], "message": sc["message"],
        "graphrag": gr,
        "zeroshot": {"root": b1.root_cause, "correct": b1.root_cause == sc["root"],
                     "why": b1.reasoning, "latency_s": b1.latency_s},
    }


def main() -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from graph.graph_client import GraphClient
    from eval.baselines.zero_shot import ZeroShotBaseline

    gc = GraphClient()
    stats = load_topology(gc)
    print(f"TrainTicket topology loaded: {stats['services']} :TTService nodes, "
          f"{stats['edges']} DEPENDS_ON edges (isolated from the live :Service demo)\n")

    zs = ZeroShotBaseline(known_services=TT_SERVICES)
    rows = []
    gr_hits = zs_hits = 0
    print(f"{'scenario':10} {'depth':>5}  {'root(truth)':14}  "
          f"{'GraphRAG':22} {'Zero-Shot':16}")
    print("-" * 78)
    for sc in TT_SCENARIOS:
        r = run_tt_comparison(gc, sc, zero_shot=zs)
        rows.append(r)
        gr_hits += r["graphrag"]["correct"]
        zs_hits += r["zeroshot"]["correct"]
        gr = f"{r['graphrag']['root']} {'OK' if r['graphrag']['correct'] else 'X'} " \
             f"({r['graphrag']['depth']}h)"
        zs_c = f"{r['zeroshot']['root']} {'OK' if r['zeroshot']['correct'] else 'X'}"
        print(f"{r['id']:10} {r['depth']:>5}  {r['truth']:14}  {gr:22} {zs_c:16}")

    n = len(TT_SCENARIOS)
    print("-" * 78)
    print(f"GraphRAG traversal: {gr_hits}/{n} correct   |   "
          f"Zero-shot LLM: {zs_hits}/{n} correct")

    reset_topology(gc)
    out = {"topology": "trainticket", "services": stats["services"],
           "edges": stats["edges"], "n_scenarios": n,
           "graphrag_correct": gr_hits, "zeroshot_correct": zs_hits,
           "scenarios": rows}
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2))
    print(f"\nWritten: {RESULTS}")


if __name__ == "__main__":
    main()
