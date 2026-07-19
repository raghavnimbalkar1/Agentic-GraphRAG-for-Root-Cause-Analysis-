"""
dashboard/components/comparison.py

The live 3-way duel: given ONE ambiguous alert, run all three systems and show
who localises the true root cause.

    GraphRAG (ours)  — Q1 dependency-graph traversal from the alerting service
    Zero-Shot  (B1)  — the LLM guesses from the alert text alone
    Vector RAG (B2)  — FAISS retrieves top-k SOP docs by text similarity, then the LLM

This dramatises the paper's central result live: at depth 1 everyone wins; as the
alert fires further from the fault with a more generic symptom, the topology-blind
baselines pick the wrong service while graph traversal follows DEPENDS_ON edges to
the real root.

Methodology note (honest, matches the benchmark's Phase A): GraphRAG localises by
traversing the LIVE graph, so this panel briefly reflects the scenario's root
condition in Neo4j (as the collector would on a real fault), runs Q1, and restores
it. The collector observes real container state, so this never triggers a spurious
remediation. All three systems receive the identical alert.
"""

from __future__ import annotations

import time

import streamlit as st

from graph.graph_client import GraphClient

# Curated scenarios spanning depths 1→4. Each is a real cascade in the Online
# Boutique topology; the alert message carries only the SURFACE symptom (it names
# neither the root nor any intermediate service on the deep ones).
SCENARIOS = [
    {"id": "D1", "depth": 1, "fault": "Redis OOM",
     "alert_service": "cartservice", "root": "redis-cart",
     "condition": "OOM_KILLED", "error_type": "CACHE_ERROR",
     "message": "cartservice: cache backend connection timeouts and evictions on every cart op"},
    {"id": "D2", "depth": 2, "fault": "Payment partition",
     "alert_service": "frontend", "root": "paymentservice",
     "condition": "CONNECTION_REFUSED", "error_type": "DEGRADED",
     "message": "frontend: order placement failing at the payment step"},
    {"id": "D3", "depth": 3, "fault": "Redis OOM (deep)",
     "alert_service": "frontend", "root": "redis-cart",
     "condition": "OOM_KILLED", "error_type": "HTTP_503",
     "message": "frontend: /cart and /checkout returning 5xx, elevated latency"},
    {"id": "D3b", "depth": 3, "fault": "Product-catalog crash (deep)",
     "alert_service": "loadgenerator", "root": "productcatalogservice",
     "condition": "CRASH_LOOPING", "error_type": "HIGH_ERROR_RATE",
     "message": "load test: product browse flows returning elevated 5xx"},
    {"id": "D4", "depth": 4, "fault": "Redis OOM (deepest)",
     "alert_service": "loadgenerator", "root": "redis-cart",
     "condition": "OOM_KILLED", "error_type": "HIGH_ERROR_RATE",
     "message": "load test: storefront 5xx rate up, success rate 99%->62% over 5 min"},
]


@st.cache_resource(show_spinner="Building the Vector-RAG FAISS index (once)…")
def _vector_rag():
    from eval.baselines.vector_rag import VectorRAGBaseline
    vr = VectorRAGBaseline()
    vr.build_index()
    return vr


@st.cache_resource
def _zero_shot():
    from eval.baselines.zero_shot import ZeroShotBaseline
    return ZeroShotBaseline()


def _graphrag_localise(gc: GraphClient, sc: dict) -> dict:
    """GraphRAG's localisation = Q1 traversal. Briefly reflect the root condition
    in the graph (as the collector would), run Q1, restore. See module docstring."""
    t0 = time.perf_counter()
    gc.update_service_status(sc["root"], sc["condition"], sc["condition"])
    try:
        res = gc.get_root_cause(sc["alert_service"], sc["error_type"])
    finally:
        gc.update_service_status(sc["root"], "HEALTHY", None)
    return {
        "root": res.root_cause_node,
        "chain": res.dependency_chain,
        "depth": res.depth,
        "latency_s": round(time.perf_counter() - t0, 3),
    }


def run_comparison(gc: GraphClient, sc: dict) -> dict:
    alert = {"service": sc["alert_service"], "error_type": sc["error_type"],
             "message": sc["message"]}
    gr = _graphrag_localise(gc, sc)
    b1 = _zero_shot().resolve(alert)
    b2 = _vector_rag().resolve(alert)
    truth = sc["root"]
    return {
        "truth": truth,
        "graphrag": {"root": gr["root"], "correct": gr["root"] == truth,
                     "chain": gr["chain"], "depth": gr["depth"],
                     "latency_s": gr["latency_s"],
                     "why": f"Traversed {gr['depth']} DEPENDS_ON hop(s): "
                            + " → ".join(reversed(gr["chain"]))},
        "zeroshot": {"root": b1.root_cause, "correct": b1.root_cause == truth,
                     "latency_s": b1.latency_s, "why": b1.reasoning},
        "vectorrag": {"root": b2.root_cause, "correct": b2.root_cause == truth,
                      "latency_s": b2.latency_s, "why": b2.reasoning,
                      "matched_sop": getattr(b2, "matched_sop", "")},
    }
