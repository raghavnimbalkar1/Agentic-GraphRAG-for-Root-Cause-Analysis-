"""Read-only, deployment-scoped views of retained symptom evidence."""

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from core.registry import DEFAULT_DEPLOYMENT
from core.schemas import SymptomObservation
from core.telemetry import age_seconds


class SymptomEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    incident_id: str = Field(pattern=r"^SYM-[a-f0-9]{32}$")
    mode: Literal["shadow"]
    environment_id: str
    registry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    registry_revision: str
    workflow: Literal["boutique-checkout-v1"]
    symptom_service: Literal["frontend"]
    symptom: Literal["CHECKOUT_FAILED"]
    state: Literal["open", "recovered"]
    detected_at: AwareDatetime
    updated_at: AwareDatetime
    recovered_at: AwareDatetime | None = None
    last_outcome: Literal["healthy", "failed", "unknown"]
    root_cause_node: None = None
    candidate_roots: list[str] = Field(max_length=0)
    evidence_ids: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def consistent_times(self):
        if self.updated_at < self.detected_at:
            raise ValueError("Episode update precedes detection")
        if self.state == "recovered" and (
                self.recovered_at is None or not self.detected_at <= self.recovered_at <= self.updated_at):
            raise ValueError("Recovered episode needs an observed recovery time")
        if any(len(value) > 128 for value in self.evidence_ids):
            raise ValueError("Invalid evidence identifier")
        return self


class SymptomReader:
    """Never initializes a database, prunes records or imports the executor."""

    def __init__(self, path: Path, *, environment: str = DEFAULT_DEPLOYMENT.environment_id,
                 registry: str = DEFAULT_DEPLOYMENT.digest):
        self.path, self.environment, self.registry = path, environment, registry

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

    def _observation(self, payload: str) -> dict:
        record = SymptomObservation.model_validate_json(payload)
        if record.environment_id != self.environment or record.registry_digest != self.registry:
            raise ValueError("Evidence provenance does not match its storage scope")
        return record.model_dump(mode="json")

    def snapshot(self, *, seconds: int = 3600, now: float | None = None) -> dict:
        if seconds not in (900, 3600, 86400):
            raise ValueError("Unsupported dashboard evidence window")
        now = time.time() if now is None else now
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT payload FROM evidence WHERE environment=? AND registry=? AND kind='symptom' "
                "AND measured BETWEEN ? AND ? AND stored>=? ORDER BY measured DESC, id DESC LIMIT 201",
                (self.environment, self.registry, now - seconds, now, now - 86400)).fetchall()
            key = f"{self.environment}:{self.registry}:boutique-checkout-v1"
            raw_episodes = db.execute(
                "SELECT payload FROM symptom_episodes WHERE key=? ORDER BY updated DESC LIMIT 201", (key,)).fetchall()
        episodes = []
        for row in raw_episodes:
            episode = SymptomEpisode.model_validate_json(row[0])
            if episode.environment_id != self.environment or episode.registry_digest != self.registry:
                raise ValueError("Episode provenance does not match its storage scope")
            if episode.updated_at.timestamp() > now:
                raise ValueError("Episode update is future-dated")
            episodes.append(episode.model_dump(mode="json"))
        return {"observations": [self._observation(row[0]) for row in rows[:200]],
                "episodes": episodes, "truncated": len(rows) > 200}

    def evidence(self, identifiers: list[str], *, now: float | None = None) -> list[dict]:
        if len(identifiers) > 32 or any(not isinstance(value, str) or len(value) > 128 for value in identifiers):
            raise ValueError("Episode evidence request exceeds its limit")
        if not identifiers:
            return []
        now = time.time() if now is None else now
        placeholders = ",".join("?" for _ in identifiers)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT payload FROM evidence WHERE environment=? AND registry=? AND kind='symptom' "
                f"AND id IN ({placeholders}) AND stored>=? AND measured<=? ORDER BY measured",
                (self.environment, self.registry, *identifiers, now - 86400, now)).fetchall()
        return [self._observation(row[0]) for row in rows]


def monitor_view(directory: Path, *, now: float | None = None) -> dict:
    """A stopped/stale monitor is not current application health."""
    view = {"state": "missing", "last_observation": None, "observation_fresh": False,
            "evidence_store": "unknown", "age_seconds": None}
    path = directory / "symptom_monitor_status.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Monitor status exceeds size limit")
        status = json.loads(raw)
        interval = status["interval_seconds"]
        if (status.get("schema_version") != 1 or isinstance(status.get("schema_version"), bool)
                or status.get("mode") != "shadow" or status.get("delivery_enabled") is not False
                or not isinstance(interval, (int, float)) or isinstance(interval, bool)
                or not math.isfinite(interval) or not 30 <= interval <= 300
                or status.get("state") not in {"ready", "polling", "degraded", "stopped"}):
            raise ValueError("Invalid shadow monitor status")
        sample = status.get("last_observation")
        record = SymptomObservation.model_validate(sample) if sample else None
        environment = status.get("environment_id", record.environment_id if record else None)
        registry = status.get("registry_digest", record.registry_digest if record else None)
        if environment != DEFAULT_DEPLOYMENT.environment_id or registry != DEFAULT_DEPLOYMENT.digest:
            return {**view, "state": "different_deployment"}
        if record and (record.environment_id != environment or record.registry_digest != registry):
            raise ValueError("Monitor and observation provenance differ")
        heartbeat_age = age_seconds(status.get("timestamp"), now=now)
        age = age_seconds(record.measured_at.isoformat(), now=now) if record else None
        fresh = age is not None and age <= interval + 35
        state = status["state"]
        if state != "stopped" and (heartbeat_age is None or heartbeat_age > interval + 35):
            state = "stale"
        elif state in {"ready", "polling"} and (not fresh or record.quality != "observed"):
            state = "degraded"
        return {"state": state, "last_observation": record.model_dump(mode="json") if record else None,
                "observation_fresh": fresh, "age_seconds": age,
                "evidence_store": status.get("evidence_store", "unknown")}
    except FileNotFoundError:
        return view
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {**view, "state": "invalid"}


def transaction_row(record: dict) -> dict:
    failed = [step["step"] for step in record["steps"] if not step["success"] and step["step"] != "cleanup_cart"]
    return {"Observed (UTC)": record["measured_at"], "Outcome": record["outcome"].capitalize(),
            "Failed step": ", ".join(failed) or "None",
            "Duration (ms)": (round(sum(step["latency_s"] for step in record["steps"]) * 1000, 1)
                              if record["steps"] else None),
            "Evidence ID": record["evidence_id"]}
