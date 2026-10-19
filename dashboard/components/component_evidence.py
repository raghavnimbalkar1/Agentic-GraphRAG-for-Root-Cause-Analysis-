"""Component history, with transaction-aligned windows and no causal attribution."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core.diagnostics import METRICS, ComponentReader, DiagnosticRequest
from core.registry import DEFAULT_DEPLOYMENT

METRIC_LABELS = {
    "cpu_percent": "CPU usage", "memory_usage_bytes": "Memory usage",
    "writable_layer_bytes": "Writable layer", "redis_maxmemory_bytes": "Redis memory cap",
    "redis_connected_clients": "Redis connections", "redis_expiring_keys": "Expiring keys",
    "frontend_http_seconds": "Frontend HTTP latency",
}


def render(directory: Path, transactions: list[dict]) -> None:
    st.subheader("Component evidence")
    left, right = st.columns(2)
    service = left.selectbox("Service", list(DEFAULT_DEPLOYMENT.by_name), key="component_service")
    anchors = {row["evidence_id"]: row for row in transactions}

    def label(identifier):
        if identifier == "latest":
            return "Now"
        row = anchors[identifier]
        return f"{row['measured_at']} | {row['outcome']} | {identifier}"

    anchor = right.selectbox("Window ends at", ["latest", *anchors], format_func=label, key="component_anchor")
    left, right = st.columns(2)
    seconds = left.selectbox("Component window", [60, 300, 900], index=1,
                              format_func=lambda value: "1 minute" if value == 60 else f"{value // 60} minutes",
                              key="component_window")
    metrics = [None, *(name for name, spec in METRICS.items() if spec[0] == service)]
    metric = right.selectbox("Measurement", metrics, key=f"component_metric_{service}",
                             format_func=lambda value: METRIC_LABELS[value] if value else "Component state")
    request = DiagnosticRequest(service_id=service, window_seconds=seconds, metric=metric,
                                end_at=None if anchor == "latest" else anchors[anchor]["measured_at"])
    try:
        result = ComponentReader(directory / "evidence.sqlite").window(request)
    except (OSError, ValueError, sqlite3.Error):
        st.warning("Component evidence unavailable or incompatible.")
        return
    summary = result["summary"]
    start, end = (datetime.fromisoformat(result[key]).strftime("%Y-%m-%d %H:%M:%S")
                  for key in ("window_start", "window_end"))
    st.caption(f"UTC window: {start} to {end}")
    st.caption("Temporal context only | Cause not attributed")
    if not result["samples"]:
        st.info("No retained component observations in this window.")
        return
    a, b = st.columns(2)
    a.metric("State at window end", summary["status_at_window_end"])
    b.metric("Retained samples", summary["returned_samples"])
    if not summary["fresh_now"]:
        st.warning("No fresh component state in this selection. Current health is unknown.")
    if result["truncated"]:
        st.warning("Latest 100 samples shown. Window summaries are partial.")
    if result["unobserved_intervals"]:
        st.warning(f"{len(result['unobserved_intervals'])} gap(s) over 30 seconds between displayed samples or window boundaries.")
    if len(summary["instance_ids"]) > 1:
        st.warning("Multiple runtime instances occur in this window.")
    if summary["latest_conflict"]:
        st.warning("Conflicting observations share the latest timestamp. Component state is unknown.")
    if result["metric"]:
        item = result["metric"]
        mean = f"{item['sample_mean']:.4g}" if item["sample_mean"] is not None else "Unavailable"
        st.caption(f"Unit: {item['unit']} | Missing values: {item['missing_samples']} | "
                   f"Sample mean: {mean}")
    rows = [{"Observed (UTC)": row["measured_at"], "State": row["status"], "Quality": row["quality"],
             **({"Value": row["value"]} if metric else {}),
             "Instance": row["instance_id"], "Evidence ID": row["evidence_id"]} for row in reversed(result["samples"])]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=min(300, 36 * (len(rows) + 1)))
    with st.expander("Component provenance and gaps"):
        st.json({key: value for key, value in result.items() if key != "samples"})
