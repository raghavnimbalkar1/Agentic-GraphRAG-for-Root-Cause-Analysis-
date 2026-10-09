"""Incident-addressed, atomic audit storage shared by the agent and readers."""

import json
import os
import re
import tempfile
from pathlib import Path

from core.config import settings
from core.schemas import RCAReport


def report_path(alert_id: str, directory: Path | None = None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", alert_id):
        raise ValueError("Invalid incident ID")
    return (directory or settings.audit_dir).resolve() / f"rca_{alert_id}.json"


def read_report(alert_id: str, directory: Path | None = None) -> RCAReport | None:
    path = report_path(alert_id, directory)
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    if payload.get("alert_id") != alert_id:
        raise ValueError("Report incident ID does not match its filename")
    payload.setdefault("schema_version", 1)
    return RCAReport.model_validate(payload)


def write_report(report: RCAReport) -> None:
    write_json_atomic(report_path(report.alert_id), report.model_dump(mode="json"))


def write_json_atomic(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            json.dump(payload, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
