"""
dashboard/app.py — Agentic GraphRAG RCA live demo dashboard.

Run:
    streamlit run dashboard/app.py

Native sidebar navigation renders only the selected workspace view. Overview
and retained evidence remain available when infrastructure is disconnected.

Prerequisites:
  - Neo4j and the Online Boutique stack running (see README, "Running it")
  - agent server running:      python -m agent.main
  - telemetry collector running: python -m simulation.telemetry_collector
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
from core.health import SERVICES                       # noqa: E402
from core.telemetry import collector_status, fresh_service_statuses  # noqa: E402
from graph.graph_client import GraphClient             # noqa: E402
from dashboard.components import graph_viz             # noqa: E402
from dashboard.components import rca_report            # noqa: E402
from dashboard.components import agent_log             # noqa: E402
from dashboard.components import skill_graph           # noqa: E402
from dashboard.components import comparison            # noqa: E402
from dashboard.components import symptoms              # noqa: E402
from dashboard.components import overview              # noqa: E402
from dashboard import theme                            # noqa: E402

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
    page_icon=":material/hub:",
    layout="wide",
    initial_sidebar_state="auto",
)


@st.cache_resource
def get_graph_client() -> GraphClient:
    """One shared GraphClient for the dashboard process (singleton-safe)."""
    return GraphClient()


# ── Header + system status strip ────────────────────────────────────────────

@st.cache_data(ttl=5)
def _collector_alive(require_delivery: bool = True) -> bool:
    from simulation.incident_tracking import collector_ready
    return collector_ready(require_delivery=require_delivery)


@st.fragment(run_every=5)
def render_header() -> None:
    agent_ok = agent_log.agent_alive()
    telemetry = collector_status()
    synced = telemetry["heartbeat_fresh"] and telemetry["graph_synced"]
    sensing = telemetry["sensing_ready"]
    theme.runtime_strip([
        ("Agent", "Online" if agent_ok else "Offline", "good" if agent_ok else "bad"),
        ("Telemetry", "Sensing" if sensing else telemetry["state"].replace("_", " ").title(),
         "good" if sensing else "warn"),
        ("Graph sync", "Synchronized" if synced else "Unavailable", "good" if synced else "neutral"),
        ("Execution", "Delivery enabled" if telemetry["delivery_enabled"] else "Observation only",
         "good" if telemetry["delivery_enabled"] else "warn"),
    ])


# ── Tab 1: Live RCA Console ──────────────────────────────────────────────────

def tab_live_console(gc: GraphClient) -> None:
    ctrl, viz = st.columns([0.32, 0.68])

    with ctrl:
        st.subheader("Scenario controls")

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
        run = st.button("Inject fault", icon=":material/play_arrow:",
                        type="primary", use_container_width=True, disabled=not _collector_alive())
        reset = st.button("Reset scenario", icon=":material/restart_alt:",
                          use_container_width=True)
        # The dependency graph below reads live health from Neo4j, which the
        # telemetry collector keeps in sync with real container state. Click to
        # re-pull (e.g. after pausing a container externally) and watch it react.
        refresh = st.button("Refresh observations", icon=":material/refresh:", use_container_width=True)
        if refresh:
            st.rerun()

        st.divider()
        counts = gc.node_counts()
        st.markdown("**Neo4j graph**")
        st.markdown(
            f"Services: `{counts.get('Service', 0)}`  ·  "
            f"Skills: `{counts.get('Skill', 0)}`"
        )
        statuses = fresh_service_statuses(gc.get_service_snapshot())
        unknown = [name for name in SERVICES if statuses.get(name, "UNKNOWN") in {"UNKNOWN", "STALE"}]
        unhealthy = {s: v for s, v in statuses.items() if v not in {"HEALTHY", "UNKNOWN", "STALE"}}

    with viz:
        st.subheader("Live dependency graph")
        theme.graph_legend()
        graph_slot = render_live_health(gc)

    # ── Actions ───────────────────────────────────────────────────────────
    timeline_slot = st.container()

    if reset:
        err = agent_log.reset_scenario(fault, target)
        if err:
            st.error(f"Reset failed: {err}")
        else:
            st.success(f"Reset requested for {fault} on {target}. Awaiting observed recovery.")
        st.rerun()

    if run:
        if not agent_log.agent_alive():
            st.error("Cannot run — agent server is offline.")
            return
        if not _collector_alive() or unknown or unhealthy:
            st.error("A fresh, complete healthy baseline and active collector are required.")
            return
        _run_live_scenario(gc, fault, target, graph_slot, timeline_slot)


@st.fragment(run_every=5)
def render_live_health(gc: GraphClient):
    try:
        snapshot = gc.get_service_snapshot()
        statuses = fresh_service_statuses(snapshot)
        unknown = [name for name in SERVICES if statuses.get(name, "UNKNOWN") in {"UNKNOWN", "STALE"}]
        unhealthy = [name for name, value in statuses.items() if value not in {"HEALTHY", "UNKNOWN", "STALE"}]
        if unknown:
            st.warning(f"Stale or unavailable health: {', '.join(unknown)}")
        if unhealthy:
            st.warning(f"Unhealthy: {', '.join(unhealthy)}")
        elif not unknown:
            st.success(f"All {len(SERVICES)} monitored services have fresh HEALTHY observations")
        slot = st.empty()
        components_html_in(slot, gc, snapshot=snapshot)
        return slot
    except Exception:
        st.warning("Live graph observations unavailable")
        return st.empty()


def components_html_in(slot, gc: GraphClient, snapshot: dict | None = None) -> None:
    """Render the pyvis graph HTML into a Streamlit slot."""
    html = graph_viz.build_network(gc, snapshot=snapshot)
    with slot:
        components.html(html, height=580, scrolling=False)


def _run_live_scenario(gc, fault, target, graph_slot, timeline_slot) -> None:
    """
    Inject on a background thread; poll Neo4j health on the main thread and
    redraw the graph each tick so the red→green transition is visible. When the
    agent finishes, render the timeline + RCA report from the audit file.
    """
    # Snapshot which audit reports exist so we can detect the new one.
    from simulation.incident_tracking import matching_incident

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
            statuses = fresh_service_statuses(gc.get_service_snapshot())
            signature = tuple(sorted(statuses.items()))
            if signature != last_signature:
                components_html_in(graph_slot, gc)
                last_signature = signature
                unknown = [name for name in SERVICES if statuses.get(name, "UNKNOWN") in {"UNKNOWN", "STALE"}]
                unhealthy = [s for s, v in statuses.items() if v not in {"HEALTHY", "UNKNOWN", "STALE"}]
                if unknown:
                    status_box.update(label="Waiting for fresh service observations", state="running")
                elif unhealthy:
                    status_box.update(
                        label=f"Observed unhealthy: {', '.join(unhealthy)}; awaiting incident outcome",
                        state="running",
                    )
        except Exception:
            pass

        if handle.error:
            status_box.update(label=f"Injection error: {handle.error}",
                              state="error")
            return

        # Has the agent written a new audit report (incident concluded)?
        try:
            event, report = matching_incident(target, handle.started_at)
        except ValueError as exc:
            status_box.update(label=str(exc), state="error")
            return
        if report is not None:
            break

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
        label=f"{'RESOLVED' if resolved else report.get('resolution_status', '')} "
              f"— root cause: {report.get('root_cause_node')} "
              f"(incident {report.get('alert_id')})",
        state="complete" if resolved else "error",
        expanded=True,
    )

    # ReAct pipeline timeline (reconstructed from real report data).
    with status_box:
        for stage in agent_log.build_timeline(report):
            st.markdown(f"**{stage['title']}**")
            st.caption(stage["detail"])

    st.divider()
    st.subheader("RCA Report")
    rca_report.render_report(report)


# ── Tab 2: Incident History ──────────────────────────────────────────────────

def tab_history() -> None:
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
            "Handling (s)": r.get("handling_seconds", r.get("mttr_seconds")),
            "Tokens":     r.get("tokens_used"),
            "Hops":       r.get("total_hops"),
            "SOPs":       ", ".join(r.get("skills_executed", [])),
        })
    df = pd.DataFrame(rows)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total incidents", len(df))
    resolved = (df["Status"] == "RESOLVED").sum()
    c2.metric("Reported resolved", f"{resolved}/{len(df)}")
    valid_mttr = df["Handling (s)"].dropna()
    c3.metric("Avg handling time", f"{valid_mttr.mean():.2f}s" if len(valid_mttr) else "—")

    f1, f2 = st.columns(2)
    status_filter = f1.multiselect(
        "Status", sorted(df["Status"].dropna().unique()), default=[],
        placeholder="All statuses",
    )
    root_filter = f2.multiselect(
        "Root cause", sorted(df["Root Cause"].dropna().unique()), default=[],
        placeholder="All services",
    )
    view = df
    if status_filter:
        view = view[view["Status"].isin(status_filter)]
    if root_filter:
        view = view[view["Root Cause"].isin(root_filter)]

    st.dataframe(view, use_container_width=True, hide_index=True)

    st.markdown("#### Inspect a report")
    if view.empty:
        st.info("No incidents match these filters.")
        return
    pick = st.selectbox("Alert ID", view["Alert ID"].tolist())
    rep = rca_report.load_report(pick)
    if rep:
        rca_report.render_report(rep)


# ── Tab 3: Evaluation Results ────────────────────────────────────────────────

BENCHMARK_FULL_FILE = PROJECT_ROOT / "eval" / "results" / "benchmark_full.json"

SYSTEM_LABELS = {
    "GraphRAG": "Agentic GraphRAG (Ours)",
    "ZeroShot": "Zero-Shot LLM (B1)",
    "VectorRAG": "Vector RAG (B2)",
}


def _render_expanded_eval() -> None:
    """Section-5 expanded benchmark: 21 scenarios × 3 reps, depth-stratified."""
    data = json.loads(BENCHMARK_FULL_FILE.read_text())
    meta = data.get("metadata", {})

    st.markdown("### Historical root accuracy by cascade depth")
    st.caption(
        f"{meta.get('scenarios', '?')} scenarios × {meta.get('reps', '?')} reps · "
        f"10 fault types · Q1 traversal depths 1–4 · LLM {meta.get('llm', '—')} · "
        "deeper root = alert fires further from the fault with a more generic symptom"
    )

    by_depth = data.get("by_depth", {})
    depths = sorted(by_depth, key=int)

    # Headline chips: the flat-vs-collapse story in numbers.
    overall = data.get("overall", {})
    deepest = by_depth.get(depths[-1], {}) if depths else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recorded graph root accuracy", f"{overall.get('GraphRAG', {}).get('root_acc', [0])[0]*100:.0f}%",
              help="Root-cause accuracy of graph traversal, flat across depth 1–4")
    if deepest:
        c2.metric(f"Baselines @ depth {depths[-1]}",
                  f"{deepest['ZeroShot']['root_acc'][0]*100:.0f}% / "
                  f"{deepest['VectorRAG']['root_acc'][0]*100:.0f}%",
                  help="Recorded text-baseline results in the historical experiment")
    if overall:
        c3.metric("Overall root accuracy (ours vs best baseline)",
                  f"{overall['GraphRAG']['root_acc'][0]*100:.0f}% vs {max(overall['ZeroShot']['root_acc'][0], overall['VectorRAG']['root_acc'][0])*100:.0f}%")
        c4.metric("Historical handling time (mean)",
                  f"{overall['GraphRAG']['mttr'][0]:.1f}s",
                  help="Old manual-alert measurements exclude automatic detection and need revalidation")

    # The depth chart: baselines collapse, ours stays flat.
    chart_rows = {
        SYSTEM_LABELS[sysname]: [by_depth[d][sysname]["root_acc"][0] * 100 for d in depths]
        for sysname in ("GraphRAG", "ZeroShot", "VectorRAG")
    }
    chart_df = pd.DataFrame(chart_rows, index=[f"depth {d}" for d in depths])
    st.line_chart(chart_df, height=260,
                  color=["#4F8F79", "#C57D6E", "#B49A59"])

    depth_table = pd.DataFrame([
        {
            "Q1 depth": f"{d}  (n={sum(1 for s in data['per_scenario'].values() if s['depth'] == int(d))})",
            "GraphRAG (Ours)": f"{by_depth[d]['GraphRAG']['root_acc'][0]*100:.0f}%",
            "Zero-Shot (B1)": f"{by_depth[d]['ZeroShot']['root_acc'][0]*100:.0f}%",
            "Vector RAG (B2)": f"{by_depth[d]['VectorRAG']['root_acc'][0]*100:.0f}%",
        }
        for d in depths
    ])
    st.dataframe(depth_table, use_container_width=True, hide_index=True)
    st.caption(
        "Historical controlled localization. Graph and baseline evidence differed; "
        "these numbers do not establish general causal accuracy or the contribution of the LLM."
    )

    st.markdown("### Historical Fault Coverage")
    by_fault = data.get("by_fault", {})
    mttr_by_fault = data.get("mttr_by_fault", {})
    fault_rows = []
    for fault in sorted(by_fault):
        a = by_fault[fault]
        m = mttr_by_fault.get(fault, {}).get("mttr", (0, 0))
        fault_rows.append({
            "Fault type": fault,
            "Ours acc": f"{a['GraphRAG']['root_acc'][0]*100:.0f}%",
            "B1 acc": f"{a['ZeroShot']['root_acc'][0]*100:.0f}%",
            "B2 acc": f"{a['VectorRAG']['root_acc'][0]*100:.0f}%",
            "Handling time (s)": f"{m[0]:.1f} ± {m[1]:.1f}",
        })
    st.dataframe(pd.DataFrame(fault_rows), use_container_width=True, hide_index=True)

    with st.expander("Honest methodology notes (read before citing)"):
        st.markdown(
            "- **Root accuracy & blast F1** measure the exact agent mechanism (Q1 traversal "
            "+ transitive closure) vs the baselines' LLM inference on the same alert. "
            "GraphRAG is deterministic, so its std is ~0; the 3 reps capture baseline LLM variance.\n"
            "- **One fault at a time** — with a single unhealthy node, deterministic traversal "
            "will find it; the claim is *robustness to alert ambiguity*, not solved RCA.\n"
            "- **Timing**: old agent handling measurements begin at a manual alert, not injection. "
            "Baselines measure inference only. Recovery and correlation require revalidation.\n"
            "- **Tokens**: historical measurements do not establish a scaling law. "
            "Candidate filtering must be evaluated with paired same-model runs; "
            "description length, valid candidate count and attempt history also affect context size."
        )


def tab_evaluation() -> None:
    from eval.metrics import summarize
    directory = PROJECT_ROOT / "eval" / "results"
    files = sorted([*directory.glob("localisation_v[23]_*.json"),
                    *directory.glob("verified/**/localisation_v[23]_*.json")])
    st.subheader("Versioned runs")
    if files:
        pick = st.selectbox("Run", files, format_func=lambda path: path.name)
        try:
            data = json.loads(pick.read_text())
            st.json(data["metadata"], expanded=False)
            summary = summarize(data["trials"])
            rows = [{"System": name, "Attempts": row["n_attempted"], "Errors": row["n_failed"],
                     "Positive root accuracy": row["root_accuracy"],
                     "Negative control accuracy": row.get("negative_control_accuracy"),
                     "Localization seconds (mean)": row["localisation_seconds"]["mean"],
                     "Tokens (mean)": row["tokens"]["mean"]} for name, row in summary.items()]
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.caption("Controlled localization only. Graph-only trials do not measure an LLM contribution or live recovery.")
            with st.expander("Raw trials"):
                st.dataframe(data["trials"], use_container_width=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            st.error(f"Result artifact could not be read: {exc}")
    else:
        st.info("No corrected evaluation run has been measured yet.")
    if BENCHMARK_FULL_FILE.exists():
        with st.expander("Historical results, not revalidated"):
            st.warning("Known scoring, evidence-parity, timing and verification limitations. Not final thesis evidence.")
            _render_expanded_eval()
        st.divider()
        with st.expander("Original RQ1/RQ2 benchmark (4 scenarios — legacy run)"):
            _render_legacy_eval()
    else:
        _render_legacy_eval()


def _render_legacy_eval() -> None:
    st.warning("Historical benchmark with known scoring/evidence limitations; not corrected research evidence.")
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
    c4.metric("Historical handling (ours)", f"{ours.get('avg_latency_s', 0):.2f}s")

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
        st.bar_chart(agg_df["root_accuracy"], color="#4F8F79")
        st.markdown("**Blast-radius F1**")
        st.bar_chart(agg_df["avg_blast_f1"], color="#7193A8")
    with cc2:
        st.markdown("**Avg tokens / call**")
        st.bar_chart(agg_df["avg_tokens"], color="#B49A59")
        st.markdown("**Avg latency / MTTR (s)**")
        st.bar_chart(agg_df["avg_latency_s"], color="#87958A")

    st.markdown("#### Per-scenario breakdown")
    rows = []
    for sid, sc in data["per_scenario"].items():
        for sysname, res in sc["results"].items():
            rows.append({
                "Scenario": sid,
                "Fault": sc.get("fault_type"),
                "System": sysname,
                "Root correct": "yes" if res.get("root_correct") else "no",
                "Predicted Root": res.get("predicted_root"),
                "Blast F1": res.get("blast_f1"),
                "Latency (s)": res.get("latency_s"),
                "Tokens": res.get("tokens_used"),
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with st.expander("Interpretation limits"):
        st.write("These saved values precede corrected scoring and evidence parity. "
                 "They cannot establish a general accuracy, cost or recovery advantage.")


# ── Main ─────────────────────────────────────────────────────────────────────

# ── Tab 4: Autonomy Run (chaos) ──────────────────────────────────────────────

def tab_autonomy() -> None:
    import glob
    st.caption("Collector-driven trials; outcomes below are specific to the selected artifact.")
    files = sorted(glob.glob(str(PROJECT_ROOT / "eval" / "results" / "chaos_run_*.json")),
                   reverse=True)
    if not files:
        st.info("No chaos run recorded yet. Run:\n\n"
                "`python -m simulation.chaos_daemon --duration 600 --min-incidents 15`")
        return

    pick = st.selectbox("Run", [Path(f).name for f in files])
    data = json.loads((PROJECT_ROOT / "eval" / "results" / pick).read_text())
    if data.get("schema_version", 1) < 2:
        st.warning("Historical run: incident correlation and recovery verification were not independently revalidated.")

    c1, c2, c3, c4 = st.columns(4)
    rate = data.get("detection_rate_pct")
    attempted = data.get("total_attempted", data["total_injected"])
    c1.metric("Detected / attempted", f"{rate:.0f}%" if rate is not None else "Unavailable")
    c2.metric("Injection commands completed", data["total_injected"])
    c3.metric("Resolved / attempted", f"{data['resolved']}/{attempted}")
    c4.metric("Manual alerts fired", data["manual_alerts_fired"])

    st.info(
        f"**{data['detected']}/{attempted} trials detected** "
        f"and **{data['resolved']} resolved** — with **{data['manual_alerts_fired']} alerts "
        f"manually fired** (every incident was raised by the collector alone). "
        f"Mean detection latency {data['mean_detect_latency_s']}s · "
        f"mean detection-to-confirmation {data['mean_mttr_s']}s · escalated {data['escalated']}."
    )

    rows = [{
        "Fault": i["fault"], "Service": i["service"], "Condition": i["condition"],
        "Detect (s)": i["detect_latency_s"], "Root": i["root"], "Depth": i["depth"],
        "SOP": ", ".join(i["sop"]), "Status": i["status"], "Recovery delay (s)": i["mttr_s"],
    } for i in data["incidents"]]
    if not rows:
        st.info("No completed trials in this artifact.")
        return
    inc_df = pd.DataFrame(rows)

    chart_col, table_col = st.columns([0.35, 0.65])
    with chart_col:
        st.markdown("**Per-incident latency (s)**")
        latency_df = inc_df[["Fault", "Detect (s)", "Recovery delay (s)"]].copy()
        latency_df.index = [f"{i+1:02d}. {f}" for i, f in enumerate(latency_df.pop("Fault"))]
        st.bar_chart(latency_df, height=360, horizontal=True,
                     color=["#7193A8", "#4F8F79"])
    with table_col:
        st.markdown("**Incident lifecycle table**")
        st.dataframe(inc_df, use_container_width=True, hide_index=True, height=360)


# ── Tab 5: Dual Graph & Architecture ─────────────────────────────────────────

def tab_architecture(gc: GraphClient) -> None:
    counts = gc.node_counts()
    summ = skill_graph.skill_summary(gc)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Services (infra graph)", counts.get("Service", 0))
    c2.metric("SOP skills (skill graph)", counts.get("Skill", 0))
    c3.metric("Trigger conditions", summ["n_triggers"])
    c4.metric("Fallback chains", summ["n_fallbacks"])

    st.info(
        f"`{summ['max_candidates_service']}` has {summ['max_candidates']} linked "
        "procedures across its conditions. Each incident receives only the "
        "applicable candidates; some conditions have just one. Model selection "
        "benefit requires a separate comparison with a deterministic policy.",
    )

    graph_mode = st.radio("Graph", ["Infrastructure", "Remediation skills"], horizontal=True,
                          label_visibility="collapsed", key="architecture_graph")
    if graph_mode == "Infrastructure":
        theme.graph_legend()
        components.html(graph_viz.build_network(gc, height="520px"), height=530, scrolling=False)
    else:
        theme.graph_legend(skills=True)
        components.html(skill_graph.build_skill_network(gc, height="520px"), height=530, scrolling=False)

    catalog = pd.DataFrame(summ["skills"]).rename(columns={"name": "Procedure", "risk": "Risk",
                                                          "trigger": "Condition", "services": "Applies to"})
    if not catalog.empty:
        with st.expander("Procedure catalog"):
            st.dataframe(catalog[["Procedure", "Risk", "Condition", "Applies to"]],
                         hide_index=True, use_container_width=True)

    st.divider()
    st.markdown("##### The autonomous loop (5 layers)")
    steps = [
        ("1 · Ingest", "Alert arrives from the telemetry collector (never from the fault injector)."),
        ("2 · Retrieve", "Q1 traverses `DEPENDS_ON` to the deepest unhealthy node; Q2 returns every SOP whose trigger matches — the candidate set."),
        ("3 · Reason", "LLM picks ONE candidate by exact name (or escalates). It can only choose from the graph set — the allowlist invariant."),
        ("4 · Execute", "The chosen SOP runs in an ephemeral, capability-dropped Docker sandbox; the script path comes from the graph, never the LLM."),
        ("5 · Evaluate", "The real failed condition is re-probed. If still broken, walk `NEXT_IF_FAIL` to the next SOP; else write the audit report."),
    ]
    for title, body in steps:
        st.markdown(f"**{title}** — {body}")


# ── Tab: Live Duel (GraphRAG vs baselines) ───────────────────────────────────

def _system_column(col, name: str, subtitle: str, res: dict, *, is_ours: bool) -> None:
    correct = res["correct"]
    with col:
        st.markdown(f"##### {name}")
        st.caption(subtitle)
        box = st.success if correct else st.error
        box(f"**{res['root']}** — {'correct' if correct else 'wrong'}")
        why = (res.get("why") or "").strip()
        if why:
            st.caption(why[:240])
        lat = res.get("latency_s")
        tail = f"⏱ {lat:.2f}s" if lat else ""
        if res.get("matched_sop"):
            tail += f" · matched SOP: {res['matched_sop']}"
        if tail:
            st.caption(tail)


@st.cache_resource
def _tt_zero_shot():
    from eval.baselines.zero_shot import ZeroShotBaseline
    from eval.trainticket.topology import TT_SERVICES
    return ZeroShotBaseline(known_services=TT_SERVICES)


@st.cache_resource
def _tt_loaded(_gc) -> dict:
    from eval.trainticket.topology import load_topology
    return load_topology(_gc)   # idempotent — MERGEs the isolated :TTService graph


def tab_comparison(gc: GraphClient) -> None:
    st.caption(
        "One ambiguous alert, same LLM, run through each system at once. "
        "Graph traversal follows `DEPENDS_ON` edges to the true root; the "
        "topology-blind baselines must guess from the alert text. The "
        "depth-stratified result from the Evaluation tab, made live."
    )
    topo = st.radio(
        "Topology",
        ["Online Boutique · 12 services · depth 1-4",
         "TrainTicket · 36 services · depth 1-7"],
        horizontal=True,
        help="TrainTicket is a larger published benchmark (FudanSELab), loaded as "
             "an isolated graph. Its deeper cascades stress localisation further.",
    )
    if topo.startswith("TrainTicket"):
        _tt_duel(gc)
    else:
        _boutique_duel(gc)


def _tt_duel(gc: GraphClient) -> None:
    from eval.trainticket import topology as tt
    from eval.trainticket.benchmark_localisation import run_tt_comparison

    stats = _tt_loaded(gc)
    st.caption(
        f"TrainTicket loaded as an **isolated `:TTService` graph** "
        f"({stats['services']} services, {stats['edges']} dependency edges) — the "
        f"live Online Boutique demo is untouched. **Localisation only:** GraphRAG "
        f"traversal (the *same* `get_root_cause`, pointed at this graph) vs a "
        f"topology-blind zero-shot LLM. Vector-RAG is omitted — it needs a "
        f"TrainTicket SOP corpus, which is the remediation / future-work tier."
    )
    labels = {f"{s['id']} · depth {s['depth']} · root `{s['root']}`": s
              for s in tt.TT_SCENARIOS}
    pick = st.selectbox("Scenario (deeper = alert further from the root)",
                        list(labels), index=len(labels) - 1, key="tt_pick")
    sc = labels[pick]
    st.markdown(f"**The alert** (both systems see exactly this):\n\n"
                f"> `{sc['alert_service']}` — *{sc['message']}*")
    st.caption(f"Ground-truth root cause: **{sc['root']}** — "
               f"**{sc['depth']} dependency hops** from `{sc['alert_service']}`, "
               f"across a 36-service graph. The alert text names neither.")

    if st.button("Run comparison", icon=":material/compare_arrows:", type="primary", use_container_width=True,
                 key="tt_run"):
        with st.spinner("GraphRAG traverses the TrainTicket graph; zero-shot calls the LLM…"):
            try:
                r = run_tt_comparison(gc, sc, zero_shot=_tt_zero_shot())
            except Exception as e:  # noqa: BLE001
                st.error(f"Comparison failed (usually an LLM key issue): {e}")
                return
        gr, zs = r["graphrag"], r["zeroshot"]
        c1, c2 = st.columns(2)
        _system_column(c1, "GraphRAG (ours)", "Q1 graph traversal, 36-node graph",
                       {"root": gr["root"], "correct": gr["correct"],
                        "latency_s": gr.get("latency_s"),
                        "why": f"Traversed {gr['depth']} DEPENDS_ON hop(s): "
                               + " → ".join(reversed(gr["chain"]))},
                       is_ours=True)
        _system_column(c2, "Zero-Shot (B1)", "LLM from alert text",
                       {"root": zs["root"], "correct": zs["correct"],
                        "latency_s": zs["latency_s"], "why": zs["why"]},
                       is_ours=False)
        st.divider()
        if gr["correct"] and not zs["correct"]:
            st.success(
                f"**GraphRAG localised `{r['truth']}` via a {gr['depth']}-hop "
                f"traversal across 36 services** — the zero-shot LLM guessed "
                f"`{zs['root']}`. The depth axis now reaches {gr['depth']}, nearly "
                f"double Online Boutique's — and traversal still holds.")
        elif gr["correct"] and zs["correct"]:
            st.info("Both correct at this depth — pick a deeper scenario to watch "
                    "the text baseline break.")


def _boutique_duel(gc: GraphClient) -> None:
    labels = {f"{s['id']} · depth {s['depth']} · {s['fault']}": s
              for s in comparison.SCENARIOS}
    pick = st.selectbox("Scenario (alert fires further from the root as depth grows)",
                        list(labels.keys()), index=len(labels) - 1)
    sc = labels[pick]

    st.markdown(
        f"**The alert** (all three systems see exactly this):\n\n"
        f"> `{sc['alert_service']}` reports **{sc['error_type']}** — "
        f"*{sc['message']}*"
    )
    st.caption(f"Ground-truth root cause: **{sc['root']}** "
               f"({sc['depth']} dependency hop{'s' if sc['depth'] != 1 else ''} "
               f"from `{sc['alert_service']}`). "
               f"Notice the alert text names neither the root nor the path to it"
               if sc["depth"] >= 2 else
               f"Ground-truth root cause: **{sc['root']}** (direct dependency — "
               f"the easy case where everyone should win).")

    if st.button("Run comparison", icon=":material/compare_arrows:", type="primary", use_container_width=True):
        with st.spinner("Localising — GraphRAG traverses the graph; baselines call the LLM…"):
            r = run_comparison_safe(gc, sc)
        if r is None:
            return

        c1, c2, c3 = st.columns(3)
        _system_column(c1, "GraphRAG (ours)", "Q1 graph traversal",
                       r["graphrag"], is_ours=True)
        _system_column(c2, "Zero-Shot (B1)", "LLM from alert text",
                       r["zeroshot"], is_ours=False)
        _system_column(c3, "Vector RAG (B2)", "FAISS SOP retrieval + LLM",
                       r["vectorrag"], is_ours=False)

        st.divider()
        ours = r["graphrag"]["correct"]
        b1, b2 = r["zeroshot"]["correct"], r["vectorrag"]["correct"]
        if ours and not (b1 or b2):
            st.success(
                f"**GraphRAG localised `{r['truth']}` via a "
                f"{r['graphrag']['depth']}-hop traversal — both baselines missed it.** "
                f"The graph followed the dependency edges the alert text never mentions.",
            )
        elif ours and (b1 or b2):
            st.info(
                f"All correct at this depth — the root `{r['truth']}` is close enough "
                f"to the alert that text alone can reach it. Try a deeper scenario to "
                f"watch the baselines break.",
            )
        elif not ours:
            st.warning("GraphRAG missed — check that the stack is clean (no other "
                       "unhealthy service is confusing the traversal).")


def run_comparison_safe(gc: GraphClient, sc: dict):
    """Wrap the comparison so an LLM/provider error surfaces cleanly, not as a crash."""
    try:
        return comparison.run_comparison(gc, sc)
    except Exception as e:  # noqa: BLE001
        st.error(f"Comparison failed (usually an LLM provider/key issue): {e}")
        return None


def _graph_page(renderer) -> None:
    telemetry = collector_status()
    dependency = telemetry.get("dependencies", {}).get("neo4j", {})
    if dependency.get("state") == "retrying":
        st.warning("Neo4j connection unavailable." if telemetry["heartbeat_fresh"]
                   else "Neo4j was unavailable at the last collector check.")
        if not st.button("Check connection", icon=":material/refresh:"):
            return
    try:
        gc = get_graph_client()
    except Exception:
        st.warning("Neo4j connection unavailable.")
        return
    renderer(gc)


def main() -> None:
    theme.apply_theme()
    pages = {
        "Operations": [
            st.Page(overview.render, title="Overview", icon=":material/space_dashboard:", default=True),
            st.Page(lambda: symptoms.render(show_title=False), title="Transaction symptoms",
                    icon=":material/monitoring:", url_path="transactions"),
            st.Page(lambda: _graph_page(tab_live_console), title="Incident console",
                    icon=":material/emergency_home:", url_path="console"),
            st.Page(tab_history, title="Incident history", icon=":material/receipt_long:", url_path="incidents"),
        ],
        "Knowledge": [
            st.Page(lambda: _graph_page(tab_architecture), title="Dual graph", icon=":material/hub:", url_path="graphs"),
        ],
        "Research": [
            st.Page(tab_evaluation, title="Evaluation", icon=":material/science:", url_path="evaluation"),
            st.Page(lambda: _graph_page(tab_comparison), title="Baseline comparisons",
                    icon=":material/compare_arrows:", url_path="comparisons"),
            st.Page(tab_autonomy, title="Autonomy runs", icon=":material/experiment:", url_path="autonomy"),
        ],
    }
    page = st.navigation(pages, expanded=True)
    with st.sidebar:
        st.caption("LOCAL WORKSPACE")
        st.caption("Online Boutique\n\nMIT World Peace University")
    section = next(group for group, views in pages.items() if page in views)
    subtitles = {
        "Overview": "Online Boutique / Registered deployment / Local environment",
        "Transaction symptoms": "Checkout observations, component evidence and symptom episodes",
        "Incident console": "Controlled scenarios and live incident outcomes",
        "Incident history": "Retained RCA reports and verification evidence",
        "Dual graph": "Infrastructure dependencies and approved remediation procedures",
        "Evaluation": "Versioned experiments, source artifacts and measured outcomes",
        "Baseline comparisons": "Controlled localization across graph and text baselines",
        "Autonomy runs": "Recorded collector-driven chaos campaigns",
    }
    theme.page_header("Operations overview" if page.title == "Overview" else page.title,
                      section, subtitles[page.title], settings.llm_provider.value, settings.llm_model)
    render_header()
    page.run()


if __name__ == "__main__":
    main()
else:
    # `streamlit run` imports the module rather than running __main__.
    main()
