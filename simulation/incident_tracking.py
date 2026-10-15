"""Correlate a physical injection with its collector-created incident ID."""

import json
from pathlib import Path

from core import settings
from core.audit import read_report
from core.telemetry import collector_status


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


def collector_ready(directory: Path | None = None, max_age: float = 30,
                    require_delivery: bool = True) -> bool:
    state = collector_status(directory, max_age)
    return state["sensing_ready"] and (not require_delivery or state["delivery_enabled"])
