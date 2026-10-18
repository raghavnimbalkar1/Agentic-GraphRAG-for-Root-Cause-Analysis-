"""Read-only symptom episodes and transaction evidence, independent of Neo4j."""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from core.config import settings
from core.registry import DEFAULT_DEPLOYMENT
from core.symptom_view import SymptomReader, monitor_view, transaction_row

WINDOWS = {"15 minutes": 900, "1 hour": 3600, "24 hours": 86400}
STATES = {"missing": "Not started", "invalid": "Status unavailable", "different_deployment": "Different deployment",
          "ready": "Observing", "polling": "Checking", "degraded": "Degraded", "stopped": "Stopped", "stale": "Stale"}


def _table(records: list[dict]) -> None:
    rows = [transaction_row(row) for row in records]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     height=min(350, 36 * (len(rows) + 1)))


@st.fragment(run_every=5)
def render(directory: Path | None = None) -> None:
    directory = directory or settings.audit_dir
    st.subheader("Transaction symptoms")
    st.caption(f"{DEFAULT_DEPLOYMENT.environment_id} | Observation only | Automatic repairs disabled for this monitor")
    view = monitor_view(directory)
    window = st.selectbox("Observation window", list(WINDOWS), index=1, key="symptom_window")
    reader = SymptomReader(directory / "evidence.sqlite")
    try:
        data = reader.snapshot(seconds=WINDOWS[window])
    except (OSError, ValueError, sqlite3.Error):
        data = None
    c1, c2, c3 = st.columns(3)
    c1.metric("Monitor", STATES[view["state"]])
    last = view["last_observation"]
    last_label = "Unavailable" if last is None else last["outcome"].capitalize()
    if last and not view["observation_fresh"]:
        last_label += " (historical)"
    c2.metric("Last checkout observation", last_label)
    c3.metric("Open symptom episodes", sum(row["state"] == "open" for row in data["episodes"]) if data else "Unavailable")
    if last:
        st.caption(f"Last observed: {last['measured_at']} | Evidence: {last['evidence_id']}")
    if view["state"] in {"stale", "invalid", "different_deployment", "degraded"}:
        st.warning("Current transaction health is unknown.")
    elif view["state"] in {"missing", "stopped"}:
        st.info("No active transaction monitoring.")
    if view["evidence_store"] == "unavailable":
        st.warning("Monitor evidence writes are unavailable. Retained records may be incomplete.")
    if data is None:
        st.warning("Evidence store unavailable or incompatible.")
        return

    st.subheader("Checkout observations")
    if not data["observations"]:
        st.info("No retained checkout observations in this window.")
    else:
        _table(data["observations"])
        points = pd.DataFrame([transaction_row(row) for row in reversed(data["observations"])])
        points["Observed (UTC)"] = pd.to_datetime(points["Observed (UTC)"], utc=True)
        st.scatter_chart(points, x="Observed (UTC)", y="Duration (ms)", color="Outcome", height=220)
        if data["truncated"]:
            st.caption("Latest 200 observations shown; additional retained records are outside this view.")

    st.subheader("Symptom episodes")
    episodes = data["episodes"]
    if not episodes:
        st.info("No retained symptom episodes. This does not establish fault-free operation.")
        return
    by_id = {row["incident_id"]: row for row in episodes}

    def label(identifier):
        row = by_id[identifier]
        state = "Open" if row["state"] == "open" else "Observed recovery"
        return f"{row['detected_at']} | {state} | {identifier}"

    selected = st.selectbox("Episode", list(by_id), format_func=label, key="symptom_episode")
    episode = by_id[selected]
    st.markdown("**Symptom:** frontend checkout failure")
    st.markdown("**Root cause:** not attributed")
    st.markdown("**Action:** no agent repair submitted by the symptom monitor")
    state = "Open" if episode["state"] == "open" else "Observed recovery"
    st.write(f"State: {state}")
    if episode["state"] == "recovered":
        st.caption(f"Recovery observed: {episode['recovered_at']} | Not an autonomous-repair result")
    try:
        evidence = reader.evidence(episode["evidence_ids"])
    except (OSError, ValueError, sqlite3.Error):
        st.warning("Episode evidence unavailable.")
        return
    missing = len(set(episode["evidence_ids"]) - {row["evidence_id"] for row in evidence})
    if missing:
        st.warning(f"{missing} referenced observation(s) expired or unavailable.")
    _table(evidence)
    if evidence:
        rows = {row["evidence_id"]: row for row in evidence}
        identifier = st.selectbox("Transaction evidence", list(rows), key=f"symptom_evidence_{selected}")
        st.dataframe(pd.DataFrame([{"Step": row["step"], "Passed": row["success"],
                                    "HTTP": row["status_code"], "Latency (ms)": round(row["latency_s"] * 1000, 1),
                                    "Error": row["error"] or "None"} for row in rows[identifier]["steps"]]),
                     hide_index=True, use_container_width=True)
    with st.expander("Provenance"):
        st.json({key: episode[key] for key in ("incident_id", "environment_id", "registry_revision",
                                              "registry_digest", "workflow", "detected_at", "updated_at")})
