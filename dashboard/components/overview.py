"""Operational overview built from current collector evidence and retained reports."""

import json
import sqlite3
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core.config import settings
from core.registry import DEFAULT_DEPLOYMENT
from core.schemas import ObservationRecord
from core.symptom_view import SymptomReader, monitor_view
from core.telemetry import age_seconds
from dashboard.components import graph_viz, rca_report
from dashboard.theme import graph_legend

ROOT = Path(__file__).resolve().parents[2]


def collector_snapshot(directory: Path, *, now: float | None = None) -> dict:
    """An overview can render without waiting for Neo4j to connect."""
    snapshot = {name: {"status": "UNKNOWN", "age_seconds": None} for name in DEFAULT_DEPLOYMENT.by_name}
    try:
        with (directory / "collector_status.json").open("rb") as stream:
            raw = stream.read(262145)
        if len(raw) > 262144:
            return snapshot
        payload = json.loads(raw)
        heartbeat = age_seconds(payload.get("timestamp"), now=now)
        if heartbeat is None or heartbeat > 30 or payload.get("state") == "stopped":
            return snapshot
        for name in snapshot:
            try:
                record = ObservationRecord.model_validate(payload.get("observations", {}).get(name))
                if (record.service_id != name or record.schema_version != 2
                        or record.environment_id != DEFAULT_DEPLOYMENT.environment_id
                        or record.registry_digest != DEFAULT_DEPLOYMENT.digest
                        or record.registry_revision != DEFAULT_DEPLOYMENT.revision):
                    continue
                age = age_seconds(record.measured_at.isoformat(), now=now)
                snapshot[name] = {"status": record.status, "age_seconds": age}
            except (ValueError, TypeError):
                continue
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return snapshot


def reviewed_edges() -> list[tuple[str, str]]:
    """The existing reviewed fixture is a declared map, never live-discovered topology."""
    data = json.loads((ROOT / "eval/fixtures/localisation.json").read_text())
    names = set(DEFAULT_DEPLOYMENT.by_name)
    return [(a, b) for a, b in data["edges"] if a in names and b in names]


def _service_health(statuses: dict) -> None:
    healthy = sum(status == "HEALTHY" for status in statuses.values())
    faults = sum(status not in {"HEALTHY", "UNKNOWN", "STALE"} for status in statuses.values())
    unknown = len(statuses) - healthy - faults
    total = max(1, len(statuses))
    st.html('<div class="health-meter">' + ''.join(
        f'<span class="{tone}" style="width:{count / total * 100}%"></span>'
        for tone, count in [("good", healthy), ("bad", faults), ("neutral", unknown)]) + '</div>')
    rows = []
    for name, status in sorted(statuses.items()):
        tone = "good" if status == "HEALTHY" else "neutral" if status in {"UNKNOWN", "STALE"} else "bad"
        label = "Healthy" if status == "HEALTHY" else status.replace("_", " ").title()
        rows.append(f'<div class="health-row"><span class="health-name">{escape(name)}</span>'
                    f'<span class="health-value {tone}"><i class="status-dot {tone}"></i>{escape(label)}</span></div>')
    st.html(''.join(rows))


@st.fragment(run_every=5)
def render(directory: Path | None = None) -> None:
    from core.telemetry import fresh_service_statuses

    directory = directory or settings.audit_dir
    snapshot = collector_snapshot(directory)
    statuses = fresh_service_statuses(snapshot)
    healthy = sum(value == "HEALTHY" for value in statuses.values())
    faults = sum(value not in {"HEALTHY", "UNKNOWN", "STALE"} for value in statuses.values())
    unknown = len(statuses) - healthy - faults
    with st.container(key="overview_metrics"):
        a, b, c, d = st.columns(4)
        a.metric("Registered services", len(DEFAULT_DEPLOYMENT.services))
        b.metric("Healthy services", f"{healthy} / {len(statuses)}")
        c.metric("Observed faults", faults)
        d.metric("Unknown / stale", unknown)

    st.write("")
    topology, health = st.columns([0.74, 0.26], gap="large")
    with topology:
        st.subheader("Service topology")
        st.caption("Reviewed dependency map / Current collector observations")
        graph_legend()
        components.html(graph_viz.build_network(None, height="465px", snapshot=snapshot, edges=reviewed_edges()),
                        height=475, scrolling=False)
    with health:
        st.subheader("Service health")
        st.caption("Latest fresh component conditions")
        _service_health(statuses)

    st.divider()
    incidents, observation = st.columns([0.67, 0.33], gap="large")
    with incidents:
        st.subheader("Recent incidents")
        reports = rca_report.all_reports()[:5]
        if reports:
            st.dataframe(pd.DataFrame([{
                "Incident": row.get("alert_id", "Unavailable"), "Service": row.get("alert_service"),
                "Root": row.get("root_cause_node"), "Outcome": row.get("resolution_status"),
                "Handling (s)": row.get("handling_seconds", row.get("mttr_seconds"))} for row in reports]),
                hide_index=True, use_container_width=True, height=220)
            st.caption("Retained report outcomes / Ordered by report file update")
        else:
            st.html('<div class="empty-evidence">No incident reports recorded.</div>')
    with observation:
        st.subheader("Transaction monitor")
        view = monitor_view(directory)
        label = {"ready": "Observing", "polling": "Checking", "stopped": "Stopped", "missing": "Not started"}.get(
            view["state"], view["state"].replace("_", " ").title())
        st.markdown(f"**{label}**")
        last = view["last_observation"]
        if last:
            outcome = last["outcome"].capitalize()
            if not view["observation_fresh"]:
                outcome += " (historical)"
            st.caption(f"Last checkout: {outcome}")
            st.caption(last["measured_at"])
        try:
            episodes = SymptomReader(directory / "evidence.sqlite").snapshot(seconds=900)["episodes"]
            st.caption(f"Open symptom episodes: {sum(row['state'] == 'open' for row in episodes)}")
        except (OSError, ValueError, sqlite3.Error):
            st.caption("Symptom evidence unavailable")
    st.html('<div class="workspace-footer">LOCAL RESEARCH WORKSPACE &nbsp; / &nbsp; '
            'Online Boutique &nbsp; / &nbsp; Neo4j dual graph + LangGraph</div>')
