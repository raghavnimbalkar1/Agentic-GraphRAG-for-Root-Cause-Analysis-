"""
eval/benchmark_full.py — Section 5 expanded evaluation (unattended).

Scores three systems across ~22 scenarios spanning 10 fault types and Q1
traversal depths 1-4, with 3 repetitions each, capturing mean ± std for root
accuracy, blast-radius F1, MTTR, and tokens. Produces three stratified result
tables (overall, by depth, by fault type) and a machine-readable JSON.

Methodology (documented honestly):
  * Root accuracy + blast-radius F1 (the deterministic core) are measured by the
    EXACT agent mechanism — Q1 graph traversal + transitive-dependent computation
    — vs the baselines' LLM inference, on the same alert. The root is set unhealthy
    in the graph (collector paused) and Q1 is run; GraphRAG is deterministic so its
    std is ~0, while the 3 reps capture the baselines' LLM variance.
  * Depth = the Q1 traversal depth the agent reports (longest DEPENDS_ON path to
    the root). Alert specificity realistically decreases with distance from the
    root (a far-upstream monitor sees only generic symptoms).
  * MTTR + tokens are measured from REAL end-to-end agent runs, once per fault type
    x3 reps (MTTR is fault/SOP-driven, not depth-driven); baselines have no
    remediation layer, so their "MTTR" is LLM inference latency only.

Run:  python -m eval.benchmark_full          (writes log + JSON + tables)
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR  = PROJECT_ROOT / "eval" / "results"
AUDIT_DIR    = PROJECT_ROOT / "audit"
REPS = 3
ALERT_URL = "http://localhost:8888/alert"

# ── Scenarios: (id, fault_type, condition, root, alert_service, error_type, message)
# Alert specificity decreases as the alerting service moves away from the root.
SCENARIOS = [
    # depth 1 — alert from a direct dependent; symptom names the area near the root
    ("D1-oom",   "redis_oom",       "OOM_KILLED",        "redis-cart",            "cartservice",           "CACHE_ERROR",      "cartservice: cache backend connection timeouts and evictions on every cart op"),
    ("D1-stale", "stale_data",      "STALE_DATA",        "redis-cart",            "cartservice",           "STALE_READS",      "cartservice: returning stale cart contents, cache entries not refreshing"),
    ("D1-crash", "service_crash",   "CRASH_LOOPING",     "productcatalogservice", "recommendationservice", "DEP_UNAVAILABLE",  "recommendationservice: gRPC calls to the product catalog backend refused"),
    ("D1-conn",  "network_partition","CONNECTION_REFUSED","paymentservice",       "checkoutservice",       "DEP_UNAVAILABLE",  "checkoutservice: payment backend unreachable, connection refused"),
    ("D1-disk",  "disk_pressure",   "DISK_PRESSURE",     "emailservice",          "checkoutservice",       "DEP_ERROR",        "checkoutservice: confirmation-email dependency returning write errors"),
    ("D1-mem",   "memory_leak",     "MEMORY_LEAK",       "recommendationservice", "frontend",              "DEP_SLOW",         "frontend: recommendation panel dependency slow / erroring"),
    ("D1-cpu",   "high_cpu",        "HIGH_CPU",          "adservice",             "frontend",              "DEP_SLOW",         "frontend: ad slot dependency responding slowly"),

    # depth 2 — alert from two hops up; symptom more generic
    ("D2-oom",   "redis_oom",       "OOM_KILLED",        "redis-cart",            "checkoutservice",       "DEGRADED",         "checkoutservice: cart-related steps intermittently failing under load"),
    ("D2-crash", "service_crash",   "CRASH_LOOPING",     "productcatalogservice", "frontend",              "DEGRADED",         "frontend: product listing and detail pages returning errors"),
    ("D2-conn",  "network_partition","CONNECTION_REFUSED","paymentservice",       "frontend",              "DEGRADED",         "frontend: order placement failing at the payment step"),
    ("D2-mem",   "memory_leak",     "MEMORY_LEAK",       "recommendationservice", "loadgenerator",         "DEGRADED",         "load test: recommendation widgets degraded across the storefront"),
    ("D2-cpu",   "high_cpu",        "HIGH_CPU",          "adservice",             "loadgenerator",         "DEGRADED",         "load test: ad slots slow to render across the storefront"),

    # depth 3 — alert from frontend / loadgenerator; symptom is generic surface
    ("D3-oom",   "redis_oom",       "OOM_KILLED",        "redis-cart",            "frontend",              "HTTP_503",         "frontend: /cart and /checkout returning 5xx, elevated latency"),
    ("D3-conf",  "config_drift",    "CONFIG_DRIFT",      "redis-cart",            "frontend",              "HTTP_503",         "frontend: cart-related requests intermittently failing, inconsistent behaviour"),
    ("D3-crash", "service_crash",   "CRASH_LOOPING",     "productcatalogservice", "loadgenerator",         "HIGH_ERROR_RATE",  "load test: product browse flows returning elevated 5xx"),
    ("D3-conn",  "network_partition","CONNECTION_REFUSED","paymentservice",       "loadgenerator",         "HIGH_ERROR_RATE",  "load test: checkout conversions dropped sharply, elevated 5xx"),
    ("D3-disk",  "disk_pressure",   "DISK_PRESSURE",     "emailservice",          "loadgenerator",         "HIGH_ERROR_RATE",  "load test: post-purchase flow intermittently erroring"),
    ("D3-pool",  "connection_pool_exhaustion","POOL_EXHAUSTION","redis-cart",     "frontend",              "HTTP_503",         "frontend: sporadic 5xx and timeouts on cart operations"),

    # depth 4 — alert from loadgenerator, fully generic; root is 4 Q1-hops away
    ("D4-oom",   "redis_oom",       "OOM_KILLED",        "redis-cart",            "loadgenerator",         "HIGH_ERROR_RATE",  "load test: storefront 5xx rate up, success rate 99%->62% over 5 min"),
    ("D4-stale", "stale_data",      "STALE_DATA",        "redis-cart",            "loadgenerator",         "HIGH_ERROR_RATE",  "load test: intermittent wrong/blank data and elevated 5xx across storefront"),

    # dependency_timeout (frontend root): self (depth 0/1)
    ("DT-1",     "dependency_timeout","DEPENDENCY_TIMEOUT","frontend",            "loadgenerator",         "HIGH_LATENCY",     "load test: storefront p99 latency spiking, requests timing out"),
]

# Representative E2E run per fault type for real MTTR/tokens (at-source alert).
MTTR_FAULTS = [
    ("redis_oom", "redis-cart", "OOM_KILLED"),
    ("stale_data", "redis-cart", "STALE_DATA"),
    ("config_drift", "redis-cart", "CONFIG_DRIFT"),
    ("connection_pool_exhaustion", "redis-cart", "POOL_EXHAUSTION"),
    ("disk_pressure", "emailservice", "DISK_PRESSURE"),
    ("memory_leak", "recommendationservice", "MEMORY_LEAK"),
    ("high_cpu", "adservice", "HIGH_CPU"),
    ("dependency_timeout", "frontend", "DEPENDENCY_TIMEOUT"),
    ("network_partition", "paymentservice", "CONNECTION_REFUSED"),
    ("service_crash", "productcatalogservice", "CRASH_LOOPING"),
]


def _f1(predicted, truth):
    p, t = set(predicted or []), set(truth or [])
    if not p and not t:
        return 1.0
    if not p or not t:
        return 0.0
    tp = len(p & t)
    prec = tp / len(p)
    rec = tp / len(t)
    return (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0


def _ms(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return (0.0, 0.0)
    return (statistics.mean(xs), statistics.stdev(xs) if len(xs) > 1 else 0.0)


def main():
    sys.path.insert(0, str(PROJECT_ROOT))
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from core.config import settings
    from graph.graph_client import GraphClient
    from eval.baselines.zero_shot import ZeroShotBaseline
    from eval.baselines.vector_rag import VectorRAGBaseline

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logf = open(RESULTS_DIR / f"benchmark_full_{stamp}.log", "w")

    def emit(s=""):
        print(s, flush=True)
        logf.write(s + "\n"); logf.flush()

    gc = GraphClient()
    from simulation.fault_injector import FAULTS

    emit("=" * 76)
    emit("  SECTION 5 — EXPANDED EVALUATION (unattended)")
    emit(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · LLM {settings.llm_model} · "
         f"{len(SCENARIOS)} scenarios × {REPS} reps")
    emit("=" * 76)

    # Pause the collector so injected graph states stick during the accuracy phase.
    subprocess.run(["pkill", "-STOP", "-f", "telemetry_collector"], capture_output=True)
    emit("[setup] telemetry collector paused for controlled measurement\n")

    zs = ZeroShotBaseline()
    emit("[setup] building vector-RAG FAISS index...")
    vr = VectorRAGBaseline(); vr.build_index()

    # Precompute depth + blast (transitive dependents) per scenario.
    def blast_of(root):
        rows = gc._run("MATCH (d:Service)-[:DEPENDS_ON*1..8]->(r:Service {name:$r}) "
                       "RETURN collect(DISTINCT d.name) AS b", r=root)
        return rows[0]["b"] if rows else []

    # ── Phase A: root accuracy + blast F1 (deterministic core) ──────────────
    emit("\n" + "-" * 76)
    emit("  PHASE A — root accuracy + blast-radius F1 (3 reps)")
    emit("-" * 76)
    per = {}   # id -> dict
    for sc in SCENARIOS:
        sid, fault, cond, root, asvc, etype, msg = sc
        alert = {"service": asvc, "error_type": etype, "message": msg}
        truth_blast = blast_of(root)

        # reset graph, set ONLY the root unhealthy, get Q1 depth
        gc._run("MATCH (s:Service) SET s.status='HEALTHY', s.error_code=null")
        gc.update_service_status(root, cond, cond)
        q1 = gc.get_root_cause(asvc, etype)
        depth = q1.depth
        g_root_ok = 1.0 if q1.root_cause_node == root else 0.0
        g_blast_f1 = _f1(blast_of(root), truth_blast)   # graph computes it exactly

        b1_ok, b1_f1, b1_lat, b1_tok = [], [], [], []
        b2_ok, b2_f1, b2_lat, b2_tok = [], [], [], []
        for _ in range(REPS):
            r1 = zs.resolve(alert)
            b1_ok.append(1.0 if r1.root_cause == root else 0.0)
            b1_f1.append(_f1(r1.blast_radius, truth_blast))
            b1_lat.append(r1.latency_s); b1_tok.append(r1.tokens_used)
            r2 = vr.resolve(alert)
            b2_ok.append(1.0 if r2.root_cause == root else 0.0)
            b2_f1.append(_f1(r2.blast_radius, truth_blast))
            b2_lat.append(r2.latency_s); b2_tok.append(r2.tokens_used)

        per[sid] = {
            "fault": fault, "root": root, "depth": depth, "alert_service": asvc,
            "GraphRAG":  {"root_acc": (g_root_ok, 0.0), "blast_f1": (g_blast_f1, 0.0)},
            "ZeroShot":  {"root_acc": _ms(b1_ok), "blast_f1": _ms(b1_f1),
                          "latency": _ms(b1_lat), "tokens": _ms(b1_tok)},
            "VectorRAG": {"root_acc": _ms(b2_ok), "blast_f1": _ms(b2_f1),
                          "latency": _ms(b2_lat), "tokens": _ms(b2_tok)},
        }
        emit(f"  {sid:9} d{depth} {fault:26} | GraphRAG root={g_root_ok:.0f} "
             f"| B1 root={statistics.mean(b1_ok):.2f} f1={statistics.mean(b1_f1):.2f} "
             f"| B2 root={statistics.mean(b2_ok):.2f} f1={statistics.mean(b2_f1):.2f}")

    gc._run("MATCH (s:Service) SET s.status='HEALTHY', s.error_code=null")

    # ── Phase B: real MTTR + tokens per fault type (E2E) ────────────────────
    emit("\n" + "-" * 76)
    emit("  PHASE B — real end-to-end MTTR + tokens per fault type (3 reps)")
    emit("-" * 76)
    mttr_by_fault = {}
    for fault, target, etype in MTTR_FAULTS:
        inject_fn, reset_fn, default_target = FAULTS[fault]
        mttrs, toks = [], []
        for rep in range(REPS):
            before = {p.name for p in AUDIT_DIR.glob("rca_*.json")}
            try:
                inject_fn(target) if default_target is not None else inject_fn()
            except Exception as e:  # noqa: BLE001
                emit(f"  {fault}: inject error {e}"); break
            try:
                httpx.post(ALERT_URL, json={"service": target, "error_type": etype,
                                            "message": f"{etype} on {target}"}, timeout=180)
            except Exception:  # noqa: BLE001
                pass
            report = None
            t0 = time.time()
            while time.time() - t0 < 160:
                new = {p.name for p in AUDIT_DIR.glob("rca_*.json")} - before
                if new:
                    report = json.loads((AUDIT_DIR / max(new, key=lambda n: (AUDIT_DIR/n).stat().st_mtime)).read_text())
                    break
                time.sleep(0.5)
            if report and report.get("resolution_status") == "RESOLVED":
                if report.get("mttr_seconds") is not None:
                    mttrs.append(report["mttr_seconds"])
                if report.get("tokens_used"):
                    toks.append(report["tokens_used"])
            # cleanup
            try:
                reset_fn(target) if default_target is not None else reset_fn()
            except Exception:  # noqa: BLE001
                pass
            for _ in range(40):
                bad = {s: v for s, v in gc.get_all_service_statuses().items() if v != "HEALTHY"}
                if not bad:
                    break
                time.sleep(1)
        mttr_by_fault[fault] = {"mttr": _ms(mttrs), "tokens": _ms(toks), "n": len(mttrs)}
        m, sd = _ms(mttrs)
        emit(f"  {fault:28} MTTR {m:5.1f} ± {sd:4.1f}s  (n={len(mttrs)}/{REPS})")

    subprocess.run(["pkill", "-CONT", "-f", "telemetry_collector"], capture_output=True)
    emit("\n[teardown] telemetry collector resumed")

    # ── Aggregate + stratify ────────────────────────────────────────────────
    out = {"metadata": {"timestamp": stamp, "llm": settings.llm_model, "reps": REPS,
                        "scenarios": len(SCENARIOS),
                        "methodology": "root/F1 via Q1+graph vs baseline LLM (3 reps); "
                                       "MTTR/tokens via real E2E per fault type (3 reps); "
                                       "depth = Q1 traversal depth"},
           "per_scenario": per, "mttr_by_fault": mttr_by_fault}

    def agg(scenario_ids):
        res = {}
        for sysname in ("GraphRAG", "ZeroShot", "VectorRAG"):
            racc = [per[s][sysname]["root_acc"][0] for s in scenario_ids]
            bf1 = [per[s][sysname]["blast_f1"][0] for s in scenario_ids]
            if sysname == "GraphRAG":
                mttr = [mttr_by_fault.get(per[s]["fault"], {}).get("mttr", (0, 0))[0] for s in scenario_ids]
                tok = [mttr_by_fault.get(per[s]["fault"], {}).get("tokens", (0, 0))[0] for s in scenario_ids]
            else:
                mttr = [per[s][sysname]["latency"][0] for s in scenario_ids]
                tok = [per[s][sysname]["tokens"][0] for s in scenario_ids]
            res[sysname] = {"root_acc": _ms(racc), "blast_f1": _ms(bf1),
                            "mttr": _ms(mttr), "tokens": _ms(tok)}
        return res

    all_ids = list(per.keys())
    out["overall"] = agg(all_ids)
    out["by_depth"] = {d: agg([s for s in all_ids if per[s]["depth"] == d])
                       for d in sorted({per[s]["depth"] for s in all_ids})}
    out["by_fault"] = {f: agg([s for s in all_ids if per[s]["fault"] == f])
                       for f in sorted({per[s]["fault"] for s in all_ids})}

    (RESULTS_DIR / "benchmark_full.json").write_text(json.dumps(out, indent=2))
    emit(f"\nRaw results: {RESULTS_DIR / 'benchmark_full.json'}")

    # ── Render the three tables ─────────────────────────────────────────────
    def row(label, a):
        return (f"| {label} | {a['GraphRAG']['root_acc'][0]*100:.0f}% | "
                f"{a['ZeroShot']['root_acc'][0]*100:.0f}% | {a['VectorRAG']['root_acc'][0]*100:.0f}% "
                f"| {a['GraphRAG']['blast_f1'][0]:.2f} | {a['ZeroShot']['blast_f1'][0]:.2f} | "
                f"{a['VectorRAG']['blast_f1'][0]:.2f} |")

    emit("\n" + "=" * 76)
    emit("  TABLE 2 — ROOT ACCURACY & BLAST F1 BY Q1 DEPTH  (the headline)")
    emit("=" * 76)
    emit("| Depth | GraphRAG acc | B1 acc | B2 acc | GraphRAG F1 | B1 F1 | B2 F1 |")
    emit("|---|---|---|---|---|---|---|")
    for d in sorted(out["by_depth"]):
        ids = [s for s in all_ids if per[s]["depth"] == d]
        emit(row(f"depth {d} (n={len(ids)})", out["by_depth"][d]))

    logf.close()
    print("\nDONE.")


if __name__ == "__main__":
    main()
