"""
dashboard/components/rca_report.py

Renders a structured RCA report (the JSON the agent writes to audit/) as a
presentable Streamlit card: headline metrics, the dependency chain, the SOP(s)
executed, and the raw sandbox output.
"""

from __future__ import annotations

from pathlib import Path
import json

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = PROJECT_ROOT / "audit"

def load_report(alert_id: str) -> dict | None:
    """Load a single audit report by alert_id."""
    path = AUDIT_DIR / f"rca_{alert_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def latest_report() -> dict | None:
    """Return the most recently modified audit report, or None."""
    reports = sorted(AUDIT_DIR.glob("rca_*.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        return None
    with open(reports[-1]) as f:
        return json.load(f)


def all_reports() -> list[dict]:
    """Load every audit report, newest first."""
    out = []
    for p in sorted(AUDIT_DIR.glob("rca_*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def render_metrics(report: dict) -> None:
    """Top metric row: status, MTTR, hops, tokens."""
    status = report.get("resolution_status", "UNKNOWN")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Resolution", status)
    mttr = report.get("mttr_seconds")
    c2.metric("MTTR", f"{mttr:.2f}s" if mttr is not None else "—")
    c3.metric("Hops / Attempts", report.get("total_hops", "—"))
    c4.metric("LLM Tokens", report.get("tokens_used", "—") or "—")


def render_report(report: dict) -> None:
    """Full RCA report card."""
    render_metrics(report)

    # The graph-grounded WHY — the most defensible artifact the agent produces:
    # the deterministic Q1 path plus the LLM's labelled rationale.
    explanation = (report.get("root_cause_explanation") or "").strip()
    if explanation:
        st.info(f"**Why this root cause:** {explanation}")

    st.markdown("#### Root Cause Analysis")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Alert ID:** `{report.get('alert_id', '—')}`")
        st.markdown(f"**Alerting service:** `{report.get('alert_service', '—')}`")
        st.markdown(f"**Error type:** `{report.get('alert_error_type', '—')}`")
    with col2:
        st.markdown(f"**Root cause:** :red[`{report.get('root_cause_node', '—')}`]")
        st.markdown(f"**Services healthy:** "
                    f"{'all healthy' if report.get('all_services_healthy') else 'not all healthy'}")
        # skills_executed holds every SOP *considered* (visited); the sandbox
        # execution_history below is the record of what actually ran.
        st.markdown(f"**SOP(s) attempted:** "
                    f"{', '.join(report.get('skills_executed', [])) or '—'}")

    chain = report.get("dependency_chain", [])
    if chain:
        st.markdown("**Dependency chain (root → symptom):**")
        st.markdown(" &nbsp;→&nbsp; ".join(f"`{c}`" for c in chain))

    # ── The autonomous decision, made auditable ───────────────────────────
    # This is the proof that remediation was CHOSEN from graph-vetted options,
    # not looked up from a hardcoded fault->fix table.
    considered = report.get("candidates_considered") or []   # null-safe for pre-upgrade reports
    chosen = report.get("skills_executed") or []
    reason = (report.get("llm_selection_reason") or "").strip()
    if considered:
        st.markdown("#### Agent decision (graph-vetted candidates to LLM choice)")
        chips = " ".join(
            (f":green[**{c}** (executed)]" if c in chosen else f":gray[{c}]")
            for c in considered
        )
        st.markdown(f"**Considered ({len(considered)} option"
                    f"{'s' if len(considered) != 1 else ''}):** {chips}")
        st.caption("The LLM could only pick from this graph-derived set "
                   "(the allowlist invariant).")
        if reason:
            st.markdown(f"**Why:** {reason}")

    history = report.get("execution_history", [])
    if history:
        st.markdown("#### Sandbox Execution")
        for i, ex in enumerate(history, 1):
            ok = ex.get("success")
            with st.expander(
                f"Step {i}: {ex.get('skill_name', '?')} "
                f"(exit {ex.get('exit_code')}, {ex.get('duration_s', 0):.2f}s)",
                expanded=(i == len(history)),
            ):
                if ex.get("stdout"):
                    st.markdown("**stdout**")
                    st.code(ex["stdout"].strip(), language="json")
                if ex.get("stderr"):
                    st.markdown("**stderr**")
                    st.code(ex["stderr"].strip(), language="text")
