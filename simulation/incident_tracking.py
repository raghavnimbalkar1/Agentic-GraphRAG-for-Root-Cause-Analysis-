"""Correlate a physical injection with its collector-created incident ID."""

import json
from pathlib import Path
import time

from core import settings
from core.audit import read_report


def collector_events(service: str, since: float, directory: Path | None = None) -> list[dict]:
    path = (directory or settings.audit_dir) / "collector_events.json"
    if not path.exists():
        return []
    events = json.loads(path.read_text())
    return [event for event in events if event.get("source") == "telemetry"
            and event.get("service") == service and event.get("detected_at", 0) >= since]


def matching_incident(service: str, since: float, directory: Path | None = None):
    events = collector_events(service, since, directory)
    if len(events) > 1:
        raise ValueError("Multiple collector incidents overlap this injection; trial is ambiguous")
    if not events:
        return None, None
    event = events[0]
    report = read_report(event["alert_id"], directory=directory)
    return event, report.model_dump(mode="json") if report else None


def collector_ready(directory: Path | None = None, max_age: float = 30) -> bool:
    path = (directory or settings.audit_dir) / "collector_status.json"
    try:
        state = json.loads(path.read_text())
        age = time.time() - state["timestamp"]
        return 0 <= age <= max_age and state.get("graph_synced", False)
    except (OSError, ValueError, KeyError, TypeError):
        return False
