"""Freshness views shared by the collector, preflight and dashboard."""

import json
import math
from pathlib import Path

from core.config import settings
from core.health import SERVICES
from core.schemas import ServiceStatus
from core.timestamps import age_seconds as age_seconds


def fresh_service_statuses(snapshot: dict, max_age: float | None = None) -> dict[str, str]:
    """Retain last-known graph data, but never display expired data as live health."""
    limit = settings.observation_max_age if max_age is None else max_age
    statuses = {}
    for name, row in snapshot.items():
        if not isinstance(row, dict):
            statuses[name] = "UNKNOWN"
            continue
        status, age = row.get("status"), row.get("age_seconds")
        if status not in {item.value for item in ServiceStatus} or status == "UNKNOWN":
            statuses[name] = "UNKNOWN"
        elif isinstance(age, bool) or not isinstance(age, (int, float)):
            statuses[name] = "UNKNOWN"
        elif not math.isfinite(age) or age < 0:
            statuses[name] = "UNKNOWN"
        elif age > limit:
            statuses[name] = "STALE"
        else:
            statuses[name] = status
    return statuses


def collector_status(directory: Path | None = None, max_age: float = 30,
                     *, now: float | None = None) -> dict:
    """Read both legacy status files and the stricter v2 collector heartbeat."""
    path = (directory or settings.audit_dir) / "collector_status.json"
    result = {"state": "missing", "heartbeat_fresh": False, "sensing_ready": False,
              "delivery_enabled": False, "graph_synced": False, "age_seconds": None,
              "message": "No collector heartbeat"}
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("Status must be an object")
        age = age_seconds(payload.get("timestamp"), now=now)
        version = payload.get("schema_version", 1)
        result.update(age_seconds=age, schema_version=version,
                      delivery_enabled=payload.get("delivery_enabled") is True,
                      graph_synced=payload.get("graph_synced") is True)
        if version not in (1, 2) or isinstance(version, bool):
            raise ValueError("Unsupported status schema")
        if age is None or age > max_age:
            result.update(state="stale", message="Collector heartbeat is stale or invalid")
            return result
        if version == 2 and payload.get("state") == "stopped":
            result.update(state="stopped", message="Collector stopped")
            return result
        result["heartbeat_fresh"] = True
        if version == 1:
            ready = result["graph_synced"]
        else:
            dependencies = payload.get("dependencies", {})
            observations = payload.get("observations", {})
            cycle_age = age_seconds(payload.get("cycle_completed_at"), now=now)
            evidence_ready = all(
                name in observations
                and observations[name].get("quality") == "observed"
                and observations[name].get("status") in {s.value for s in ServiceStatus} - {"UNKNOWN"}
                and (sample_age := age_seconds(observations[name].get("measured_at"), now=now)) is not None
                and sample_age <= settings.observation_max_age
                for name in SERVICES
            )
            ready = (payload.get("state") in {"ready", "polling"}
                     and cycle_age is not None and cycle_age <= max_age
                     and result["graph_synced"] and evidence_ready
                     and all(dependencies.get(name, {}).get("state") == "connected"
                             for name in ("docker", "neo4j")))
            result["dependencies"] = dependencies
            result["evidence_store"] = payload.get("evidence_store", {"enabled": False, "state": "disabled"})
        if ready:
            result.update(state="ready" if result["delivery_enabled"] else "observe_only",
                          sensing_ready=True,
                          message="Sensing; delivery enabled" if result["delivery_enabled"]
                          else "Sensing; observe-only")
        else:
            result.update(state="degraded", message="Collector active; evidence or graph sync unavailable")
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        result.update(state="invalid" if path.exists() else "missing", sensing_ready=False,
                      heartbeat_fresh=False, message="Collector status unavailable or invalid")
        return result
