"""
dashboard/app.py — Agentic GraphRAG RCA · Live Demo Dashboard

Run:
    streamlit run dashboard/app.py

Three tabs:
  1. Live RCA Console — inject a fault, watch the dependency graph go red, then
     watch the autonomous agent resolve it and turn it green again, with a
     reconstructed ReAct pipeline timeline + RCA report.
  2. Incident History — every audit report the agent has written.
  3. Evaluation Results — RQ1/RQ2 benchmark: GraphRAG vs Zero-Shot vs Vector RAG.

Prereqs (all already part of the project):
  • docker compose stacks up (Neo4j + Online Boutique)
  • agent server running:  python -m agent.main
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Make the project importable when launched via `streamlit run`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.config import settings                       # noqa: E402
from graph.graph_client import GraphClient             # noqa: E402
from dashboard.components import graph_viz             # noqa: E402
from dashboard.components import rca_report            # noqa: E402
from dashboard.components import agent_log             # noqa: E402

BENCHMARK_FILE = PROJECT_ROOT / "eval" / "results" / "benchmark_all.json"

# Fault → sensible target choices for the demo dropdown.
#
# IMPORTANT: each fault raises a fixed error_type, and the agent only resolves a
# target that has a Skill whose trigger_condition matches that error_type. Offer
# only combos that actually resolve (otherwise the run ESCALATES with "no skill
# matched"). Trigger pairing (verified against Neo4j APPLIES_TO):
#   redis_oom            → OOM_KILLED        → redis-cart (Redis_Restart_SOP)
#   redis_oom_persistent → OOM_KILLED        → redis-cart; restart fails real
#                          verification, agent falls back via NEXT_IF_FAIL to
#                          Redis_Flush_SOP (two-SOP chain)
#   stale_data           → STALE_DATA        → redis-cart (Redis_Flush_SOP)
#   high_cpu             → HIGH_CPU          → adservice (CPU throttle, no restart)
#   service_crash        → CRASH_LOOPING     → productcatalog / recommendation /
#                                              shipping / currency / email / frontend
#   network_partition    → CONNECTION_REFUSED→ paymentservice / cartservice
FAULT_TARGETS = {
    "redis_oom":            ["redis-cart"],
    "redis_oom_persistent": ["redis-cart"],
    "stale_data":           ["redis-cart"],
    "high_cpu":             ["adservice"],
    "service_crash":        ["productcatalogservice", "recommendationservice",
                             "shippingservice", "currencyservice", "emailservice"],
    "network_partition":    ["paymentservice", "cartservice"],
}


# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Agentic GraphRAG · RCA",
    page_icon="🧭",
    layout="wide",
)


@st.cache_resource
def get_graph_client() -> GraphClient:
    """One shared GraphClient for the dashboard process (singleton-safe)."""
    return GraphClient()


# ── Header ───────────────────────────────────────────────────────────────────

def render_header() -> None:
    left, right = st.columns([0.75, 0.25])
    with left:
        st.title("🧭 Agentic GraphRAG — Autonomous RCA")
        st.caption(
            "Dual-graph Progressive Context Injection + sandboxed remediation · "
            "Online Boutique v0.10.5 · Neo4j · LangGraph"
        )
    with right:
        alive = agent_log.agent_alive()
        st.markdown("**Agent server**")
        st.markdown(
            f"{'🟢 online' if alive else '🔴 offline'} "
            f"`:{settings.alert_listen_port}`"
        )
        st.markdown(f"**LLM:** `{settings.llm_model}`")


# ── Tab 1: Live RCA Console ──────────────────────────────────────────────────

def tab_live_console(gc: GraphClient) -> None:
    ctrl, viz = st.columns([0.32, 0.68])

    with ctrl:
        st.subheader("Control Panel")

        if not agent_log.agent_alive():
            st.error(
                "Agent server is offline. Start it with:\n\n"
                "`python -m agent.main`"
            )

        fault = st.selectbox(
            "Fault type",
            list(FAULT_TARGETS.keys()),
            help="Injected into the live Online Boutique stack via Docker SDK.",
        )
        target = st.selectbox("Target service", FAULT_TARGETS[fault])

        st.markdown("")
        run = st.button("💥 Inject fault & let agent resolve",
                        type="primary", use_container_width=True)
        reset = st.button("♻️ Reset to healthy",
                          use_container_width=True)
        # The dependency graph below reads live health from Neo4j, which the
        # telemetry collector keeps in sync with real container state. Click to
        # re-pull (e.g. after pausing a container externally) and watch it react.
        refresh = st.button("🔄 Refresh real health", use_container_width=True)
        if refresh:
            st.rerun()

        st.divider()
        counts = gc.node_counts()
        st.markdown("**Neo4j graph**")
        st.markdown(
            f"Services: `{counts.get('Service', 0)}`  ·  "
            f"Skills: `{counts.get('Skill', 0)}`"
        )
        statuses = gc.get_all_service_statuses()
        unhealthy = {s: v for s, v in statuses.items() if v != "HEALTHY"}
        if unhealthy:
            st.warning(f"Unhealthy: {', '.join(unhealthy)}")
        else:
            st.success("All 12 services HEALTHY")

    with viz:
        st.subheader("Service Dependency Graph (live health)")
        graph_slot = st.empty()
        graph_slot.empty()
        components_html_in(graph_slot, gc)

    # ── Actions ───────────────────────────────────────────────────────────
    timeline_slot = st.container()

    if reset:
        err = agent_log.reset_scenario(fault, target)
        if err:
            st.error(f"Reset failed: {err}")
        else:
            st.success(f"Reset {fault} on {target} — graph restored to HEALTHY.")
        st.rerun()

    if run:
        if not agent_log.agent_alive():
            st.error("Cannot run — agent server is offline.")
            return
        _run_live_scenario(gc, fault, target, graph_slot, timeline_slot)


def components_html_in(slot, gc: GraphClient) -> None:
    """Render the pyvis graph HTML into a Streamlit slot."""
    html = graph_viz.build_network(gc)
    with slot:
        components.html(html, height=580, scrolling=False)


def _run_live_scenario(gc, fault, target, graph_slot, timeline_slot) -> None:
    """
    Inject on a background thread; poll Neo4j health on the main thread and
    redraw the graph each tick so the red→green transition is visible. When the
    agent finishes, render the timeline + RCA report from the audit file.
    """
    # Snapshot which audit reports exist so we can detect the new one.
    audit_dir = PROJECT_ROOT / "audit"
    before = {p.name for p in audit_dir.glob("rca_*.json")}

    status_box = timeline_slot.status(
        f"Injecting **{fault}** on **{target}** — the telemetry collector will "
        f"detect it and raise the incident (no manual alert)…",
        expanded=True,
    )

    handle = agent_log.start_scenario(fault, target)

    # Live poll loop. The injector only breaks things; the telemetry collector
    # detects the degradation (~5-10s) and fires the alert, then the agent
    # resolves it. So we wait for a NEW audit report to appear (the resolution
    # signal) rather than for the injection thread to finish — redrawing the
    # graph each tick so the red→green transition is visible throughout.
    report = None
    last_signature = None
    max_wait = 150  # seconds hard cap (covers detect + multi-SOP fallback)
    t0 = time.time()
    while time.time() - t0 < max_wait:
        # Redraw graph if observed health changed.
        try:
            statuses = gc.get_all_service_statuses()
            signature = tuple(sorted(statuses.items()))
            if signature != last_signature:
                components_html_in(graph_slot, gc)
                last_signature = signature
                unhealthy = [s for s, v in statuses.items() if v != "HEALTHY"]
                if unhealthy:
                    status_box.update(
                        label=f"🔴 Collector detected {', '.join(unhealthy)} "
                              f"unhealthy — agent remediating…",
                        state="running",
                    )
        except Exception:
            pass

        if handle.error:
            status_box.update(label=f"❌ Injection error: {handle.error}",
                              state="error")
            return

        # Has the agent written a new audit report (incident concluded)?
        new = {p.name for p in audit_dir.glob("rca_*.json")} - before
        if new:
            newest = max(new, key=lambda n: (audit_dir / n).stat().st_mtime)
            try:
                with open(audit_dir / newest) as f:
                    report = json.load(f)
                break
            except Exception:
                pass

        time.sleep(0.6)

    # Final graph redraw (should be all-green again post-resolution).
    components_html_in(graph_slot, gc)

    if report is None:
        status_box.update(
            label="No incident concluded within the wait window. Is the telemetry "
                  "collector running (`python -m simulation.telemetry_collector`)?",
            state="error",
        )
        return

    resolved = report.get("resolution_status") == "RESOLVED"
    status_box.update(
        label=f"{'🏁 RESOLVED' if resolved else '⚠️ ' + report.get('resolution_status', '')} "
              f"— root cause: {report.get('root_cause_node')} "
              f"in {report.get('mttr_seconds', 0):.2f}s",
        state="complete" if resolved else "error",
        expanded=True,
    )

    # ReAct pipeline timeline (reconstructed from real report data).
    with status_box:
        for stage in agent_log.build_timeline(report):
            st.markdown(f"{stage['icon']} **{stage['title']}** — {stage['detail']}")
            time.sleep(0.25)

    st.divider()
    st.subheader("📋 RCA Report")
    rca_report.render_report(report)


# ── Tab 2: Incident History ──────────────────────────────────────────────────

def tab_history() -> None:
    st.subheader("Incident History")
    reports = rca_report.all_reports()
    if not reports:
        st.info("No incidents recorded yet. Run a scenario in the Live Console.")
        return

    rows = []
    for r in reports:
        rows.append({
            "Alert ID":   r.get("alert_id"),
            "Root Cause": r.get("root_cause_node"),
            "Alert Svc":  r.get("alert_service"),
            "Error":      r.get("alert_error_type"),
            "Status":     r.get("resolution_status"),
            "MTTR (s)":   r.get("mttr_seconds"),
            "Tokens":     r.get("tokens_used"),
            "Hops":       r.get("total_hops"),
            "SOPs":       ", ".join(r.get("skills_executed", [])),
        })
    df = pd.DataFrame(rows)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total incidents", len(df))
    resolved = (df["Status"] == "RESOLVED").sum()
    c2.metric("Auto-resolved", f"{resolved}/{len(df)}")
    valid_mttr = df["MTTR (s)"].dropna()
    c3.metric("Avg MTTR", f"{valid_mttr.mean():.2f}s" if len(valid_mttr) else "—")

    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("#### Inspect a report")
    pick = st.selectbox("Alert ID", df["Alert ID"].tolist())
    rep = rca_report.load_report(pick)
    if rep:
        rca_report.render_report(rep)


# ── Tab 3: Evaluation Results ────────────────────────────────────────────────

def tab_evaluation() -> None:
    st.subheader("Phase 7 Evaluation — RQ1 / RQ2")
    if not BENCHMARK_FILE.exists():
        st.info(f"No benchmark file at {BENCHMARK_FILE}.")
        return

    data = json.loads(BENCHMARK_FILE.read_text())
    meta = data.get("metadata", {})
    st.caption(
        f"LLM: `{meta.get('llm', '—')}` · "
        f"Scenarios: {', '.join(meta.get('scenarios', []))}"
    )

    agg = data["aggregate"]
    agg_df = pd.DataFrame(agg).T
    agg_df.index.name = "System"

    # Headline metric cards for our system.
    ours = agg.get("Agentic GraphRAG (Ours)", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Root Accuracy (Ours)", f"{ours.get('root_accuracy', 0)*100:.0f}%")
    c2.metric("Blast-Radius F1 (Ours)", f"{ours.get('avg_blast_f1', 0):.3f}")
    best_baseline_f1 = max(
        agg.get("Zero-Shot LLM (B1)", {}).get("avg_blast_f1", 0),
        agg.get("Vector RAG (B2)", {}).get("avg_blast_f1", 0),
    )
    delta = ours.get("avg_blast_f1", 0) - best_baseline_f1
    c3.metric("F1 vs best baseline", f"+{delta:.3f}")
    c4.metric("Avg MTTR (Ours)", f"{ours.get('avg_latency_s', 0):.2f}s")

    st.markdown("#### Aggregate comparison")
    st.dataframe(
        agg_df.style.format({
            "root_accuracy": "{:.1%}",
            "avg_blast_f1": "{:.3f}",
            "avg_latency_s": "{:.2f}",
            "avg_tokens": "{:.0f}",
        }),
        use_container_width=True,
    )

    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**Root-cause accuracy**")
        st.bar_chart(agg_df["root_accuracy"], color="#2ecc71")
        st.markdown("**Blast-radius F1**")
        st.bar_chart(agg_df["avg_blast_f1"], color="#3498db")
    with cc2:
        st.markdown("**Avg tokens / call**")
        st.bar_chart(agg_df["avg_tokens"], color="#f39c12")
        st.markdown("**Avg latency / MTTR (s)**")
        st.bar_chart(agg_df["avg_latency_s"], color="#9b59b6")

    st.markdown("#### Per-scenario breakdown")
    rows = []
    for sid, sc in data["per_scenario"].items():
        for sysname, res in sc["results"].items():
            rows.append({
                "Scenario": sid,
                "Fault": sc.get("fault_type"),
                "System": sysname,
                "Root ✓": "✓" if res.get("root_correct") else "✗",
                "Predicted Root": res.get("predicted_root"),
                "Blast F1": res.get("blast_f1"),
                "Latency (s)": res.get("latency_s"),
                "Tokens": res.get("tokens_used"),
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with st.expander("Key findings"):
        st.markdown(
            "- **Graph topology is the differentiator.** On S-04 (depth-3 root, "
            "ambiguous frontend-only alert) both baselines mispredict `cartservice`; "
            "only Q1 graph traversal reaches `redis-cart`.\n"
            "- **Vector RAG adds no accuracy over zero-shot** yet uses ~43% more tokens.\n"
            "- **Blast-radius F1 = 1.000** for GraphRAG vs 0.74–0.77 baselines — "
            "consistent across every scenario (the gap is systematic, not noise)."
        )


# ── Main ─────────────────────────────────────────────────────────────────────

# ── Tab 4: Autonomy Run (chaos) ──────────────────────────────────────────────

def tab_autonomy() -> None:
    import glob
    st.subheader("Autonomy Run — Unattended Chaos")
    st.caption("The chaos daemon injects faults and never fires an alert; the telemetry "
               "collector detects each one and the agent resolves it. This is the "
               "autonomy proof. Artifact: `eval/results/chaos_run_*.log`.")
    files = sorted(glob.glob(str(PROJECT_ROOT / "eval" / "results" / "chaos_run_*.json")),
                   reverse=True)
    if not files:
        st.info("No chaos run recorded yet. Run:\n\n"
                "`python -m simulation.chaos_daemon --duration 600 --min-incidents 15`")
        return

    pick = st.selectbox("Run", [Path(f).name for f in files])
    data = json.loads((PROJECT_ROOT / "eval" / "results" / pick).read_text())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Detection rate", f"{data['detection_rate_pct']:.0f}%")
    c2.metric("Faults injected", data["total_injected"])
    c3.metric("Resolved", f"{data['resolved']}/{data['total_injected']}")
    c4.metric("🔔 Manual alerts fired", data["manual_alerts_fired"])

    st.success(
        f"🤖 **{data['detected']}/{data['total_injected']} faults detected autonomously** "
        f"and **{data['resolved']} resolved** — with **{data['manual_alerts_fired']} alerts "
        f"manually fired** (every incident was raised by the collector alone). "
        f"Mean detection latency {data['mean_detect_latency_s']}s · "
        f"mean MTTR {data['mean_mttr_s']}s · escalated {data['escalated']}."
    )

    rows = [{
        "Fault": i["fault"], "Service": i["service"], "Condition": i["condition"],
        "Detect (s)": i["detect_latency_s"], "Root": i["root"], "Depth": i["depth"],
        "SOP": ", ".join(i["sop"]), "Status": i["status"], "MTTR (s)": i["mttr_s"],
    } for i in data["incidents"]]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def main() -> None:
    gc = get_graph_client()
    render_header()
    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs([
        "🚨 Live RCA Console",
        "📜 Incident History",
        "📊 Evaluation Results",
        "🤖 Autonomy Run",
    ])
    with tab1:
        tab_live_console(gc)
    with tab2:
        tab_history()
    with tab3:
        tab_evaluation()
    with tab4:
        tab_autonomy()


if __name__ == "__main__":
    main()
else:
    # `streamlit run` imports the module rather than running __main__.
    main()
