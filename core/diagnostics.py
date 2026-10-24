"""Bounded, read-only diagnostic windows over already collected evidence."""

import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from core.registry import DEFAULT_DEPLOYMENT, DeploymentRegistry
from core.schemas import ObservationRecord, SymptomObservation

# These names describe the existing health-probes-v1 measurements, not new probes.
METRICS = {
    "cpu_percent": ("adservice", "cpu_percent", "percent of one CPU"),
    "memory_usage_bytes": ("recommendationservice", "bytes", "bytes (Docker memory usage)"),
    "writable_layer_bytes": ("emailservice", "bytes", "bytes (writable layer)"),
    "redis_maxmemory_bytes": ("redis-cart", "maxmemory", "bytes (0 means unlimited)"),
    "redis_connected_clients": ("redis-cart", "clients", "clients"),
    "redis_expiring_keys": ("redis-cart", "expires", "keys with expiry"),
    "frontend_http_seconds": ("frontend", "seconds", "seconds (home-page request)"),
}
SAMPLE_LIMIT = 100
FRESHNESS_SECONDS = 30


class DiagnosticRequest(BaseModel):
    """No caller-supplied paths, queries, URLs or executable operations."""

    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    window_seconds: Literal[60, 300, 900] = 300
    end_at: AwareDatetime | None = None
    metric: Literal["cpu_percent", "memory_usage_bytes", "writable_layer_bytes",
                    "redis_maxmemory_bytes", "redis_connected_clients", "redis_expiring_keys",
                    "frontend_http_seconds"] | None = None


def _utc(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


class ComponentReader:
    """Trusted code supplies the store and registry; a request selects only evidence."""

    def __init__(self, path: Path, *, registry: DeploymentRegistry = DEFAULT_DEPLOYMENT):
        self.path, self.registry = path, registry

    def _connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported evidence schema")
        except Exception:
            db.close()
            raise
        return db

    def _sample(self, row: tuple, request: DiagnosticRequest, now: float) -> dict:
        identifier, service, measured, payload = row
        if payload is None:
            raise ValueError("Evidence payload exceeds size limit")
        record = ObservationRecord.model_validate_json(payload)
        if (record.schema_version != 2 or record.environment_id != self.registry.environment_id
                or record.registry_digest != self.registry.digest or record.registry_revision != self.registry.revision
                or record.service_id != service or service != request.service_id or record.evidence_id != identifier
                or record.measured_at.timestamp() != measured):
            raise ValueError("Evidence provenance does not match its storage scope")
        if not record.measured_at.timestamp() <= record.collected_at.timestamp() <= now:
            raise ValueError("Evidence collection time is inconsistent")
        if (not identifier.startswith("OBS-") or len(identifier) > 128
                or (record.instance_id is not None and len(record.instance_id) > 128)):
            raise ValueError("Evidence identity exceeds its contract")
        value = None
        if request.metric and record.quality == "observed":
            value = record.measurements.get(METRICS[request.metric][1])
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid numeric evidence")
        # Project only reviewed fields. Never echo arbitrary detail, measurements or artifact paths.
        return {"evidence_id": identifier, "instance_id": record.instance_id,
                "measured_at": _utc(measured), "collected_at": _utc(record.collected_at.timestamp()),
                "status": record.status, "quality": record.quality, "value": value}

    def window(self, request: DiagnosticRequest, *, now: float | None = None) -> dict:
        request = DiagnosticRequest.model_validate(request)
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("Invalid diagnostic clock")
        if request.service_id not in self.registry.by_name:
            raise ValueError("Target is not in the selected deployment")
        if request.metric and METRICS[request.metric][0] != request.service_id:
            raise ValueError("Metric is not collected for this target")
        end = request.end_at.timestamp() if request.end_at is not None else now
        if end > now:
            raise ValueError("Diagnostic windows cannot end in the future")
        start = end - request.window_seconds
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT id, service, measured, CASE WHEN length(CAST(payload AS BLOB))<=8192 "
                "THEN payload ELSE NULL END FROM evidence WHERE environment=? AND registry=? "
                "AND service=? AND kind='component' AND measured BETWEEN ? AND ? "
                "AND stored BETWEEN ? AND ? ORDER BY measured DESC, id DESC LIMIT ?",
                (self.registry.environment_id, self.registry.digest, request.service_id, start, end,
                 now - 86400, now, SAMPLE_LIMIT + 1)).fetchall()
        samples = [self._sample(row, request, now) for row in reversed(rows[:SAMPLE_LIMIT])]
        times = [datetime.fromisoformat(row["measured_at"]).timestamp() for row in samples]
        gaps = [{"start_at": _utc(left), "end_at": _utc(right), "seconds": right - left}
                for left, right in zip([start, *times], [*times, end]) if right - left > FRESHNESS_SECONDS]
        values = [row["value"] for row in samples if row["value"] is not None]
        latest = samples[-1] if samples else None
        latest_variants = {(row["status"], row["quality"], row["instance_id"]) for row in samples
                           if latest and row["measured_at"] == latest["measured_at"]}
        latest_truncated = bool(latest and len(rows) > SAMPLE_LIMIT and rows[SAMPLE_LIMIT][2] == times[-1])
        # An omitted latest-time peer can hide a contradictory state or identity.
        conflict = len(latest_variants) > 1 or latest_truncated
        recent = bool(latest and not conflict and end - times[-1] <= FRESHNESS_SECONDS
                      and latest["quality"] == "observed")
        summary = {"returned_samples": len(samples), "unknown_samples": sum(r["quality"] == "unknown" for r in samples),
                   "status_at_window_end": latest["status"] if recent else "UNKNOWN",
                   "latest_age_seconds": now - times[-1] if times else None,
                   "fresh_now": bool(recent and now - times[-1] <= FRESHNESS_SECONDS),
                   "latest_conflict": conflict,
                   "latest_truncated": latest_truncated,
                   "instance_ids": sorted({r["instance_id"] for r in samples if r["instance_id"]}),
                   "missing_instance_samples": sum(r["instance_id"] is None for r in samples)}
        metric_summary = None
        if request.metric:
            metric_summary = {"name": request.metric, "unit": METRICS[request.metric][2], "samples": len(values),
                              "missing_samples": len(samples) - len(values), "minimum": min(values) if values else None,
                              "maximum": max(values) if values else None,
                              "sample_mean": sum(values) / len(values) if values else None}
        result = {"schema_version": 1, "mode": "retained-evidence-only", "environment_id": self.registry.environment_id,
                  "registry_digest": self.registry.digest, "registry_revision": self.registry.revision,
                  "service_id": request.service_id, "source": "builtin-health-probe",
                  "interpretation_version": "health-probes-v1", "read_at": _utc(now),
                  "window_start": _utc(start), "window_end": _utc(end), "sample_limit": SAMPLE_LIMIT,
                  "truncated": len(rows) > SAMPLE_LIMIT, "summary": summary, "metric": metric_summary,
                  "unobserved_intervals": gaps, "samples": samples}
        if len(json.dumps(result, allow_nan=False).encode()) > 131072:
            raise ValueError("Diagnostic response exceeds size limit")
        return result


class TransactionReader:
    """Bounded transaction history; never runs a checkout or reads mutable episodes."""

    def __init__(self, path: Path):
        self.path = path

    def window(self, request: DiagnosticRequest, *, now: float | None = None) -> dict:
        request = DiagnosticRequest.model_validate(request)
        if request.service_id != "frontend" or request.metric is not None:
            raise ValueError("Transaction evidence supports only the frontend workflow")
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("Invalid diagnostic clock")
        end = request.end_at.timestamp() if request.end_at else now
        if end > now:
            raise ValueError("Transaction windows cannot end in the future")
        registry = DEFAULT_DEPLOYMENT
        with closing(ComponentReader(self.path)._connect()) as db:
            rows = db.execute(
                "SELECT id, measured, CASE WHEN length(CAST(payload AS BLOB))<=8192 THEN payload ELSE NULL END "
                "FROM evidence WHERE environment=? AND registry=? AND service='frontend' AND kind='symptom' "
                "AND measured BETWEEN ? AND ? AND stored BETWEEN ? AND ? "
                "ORDER BY measured DESC, id DESC LIMIT 21",
                (registry.environment_id, registry.digest, end - request.window_seconds, end,
                 now - 86400, now)).fetchall()
        observations = []
        for identifier, measured, payload in reversed(rows[:20]):
            record = SymptomObservation.model_validate_json(payload or "{}")
            if (record.evidence_id != identifier or re.fullmatch(r"SYMOBS-[a-f0-9]{32}", identifier) is None
                    or record.environment_id != registry.environment_id or record.registry_digest != registry.digest
                    or record.registry_revision != registry.revision or record.measured_at.timestamp() != measured
                    or not measured <= record.collected_at.timestamp() <= now):
                raise ValueError("Transaction evidence provenance or collection time is inconsistent")
            observations.append(record.model_dump(mode="json"))
        result = {"schema_version": 1, "mode": "retained-evidence-only", "service_id": "frontend",
                  "environment_id": registry.environment_id, "registry_digest": registry.digest,
                  "registry_revision": registry.revision, "read_at": _utc(now),
                  "window_start": _utc(end - request.window_seconds), "window_end": _utc(end),
                  "sample_limit": 20, "truncated": len(rows) > 20, "observations": observations}
        if len(json.dumps(result, allow_nan=False).encode()) > 131072:
            raise ValueError("Transaction response exceeds size limit")
        return result
