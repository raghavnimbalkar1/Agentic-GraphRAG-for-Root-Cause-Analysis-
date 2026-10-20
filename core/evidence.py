"""Bounded local evidence and transactional, non-executing symptom episodes."""

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from core.schemas import ObservationRecord, SymptomObservation
from core.telemetry import age_seconds


class EvidenceStore:
    def __init__(self, path: Path, *, max_rows: int = 20000, retention_seconds: int = 86400):
        if not 10 <= max_rows <= 100000 or not 60 <= retention_seconds <= 604800:
            raise ValueError("Evidence retention limits are outside the supported range")
        self.path, self.max_rows, self.retention_seconds = path, max_rows, retention_seconds
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported evidence-store schema")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, environment TEXT NOT NULL, "
                       "registry TEXT NOT NULL, service TEXT NOT NULL, kind TEXT NOT NULL, "
                       "measured REAL NOT NULL, stored REAL NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS evidence_window ON evidence(environment, registry, service, measured)")
            db.execute("CREATE TABLE IF NOT EXISTS symptom_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS symptom_episodes (id TEXT PRIMARY KEY, key TEXT NOT NULL, "
                       "updated REAL NOT NULL, payload TEXT NOT NULL)")
            db.execute("PRAGMA user_version=1")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def _put(self, db, record, kind):
        payload = record.model_dump(mode="json")
        if kind == "component":
            # Do not retain arbitrary exception text, credentials or future probe fields.
            payload["detail"] = f"Built-in health probe: {record.status}"
            payload["measurements"] = {
                key: value for key, value in record.measurements.items()
                if key in {"maxmemory", "clients", "expires", "bytes", "cpu_percent", "seconds"}
                and isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0
            }
            payload["raw_artifact_ref"] = None
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 8192:
            raise ValueError("Evidence record exceeds the payload limit")
        old = db.execute("SELECT payload FROM evidence WHERE id=?", (record.evidence_id,)).fetchone()
        if old and old[0] != encoded:
            raise ValueError("Evidence ID already identifies different data")
        db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (record.evidence_id, record.environment_id, record.registry_digest, record.service_id,
                    kind, record.measured_at.timestamp(), time.time(), encoded))

    def _prune(self, db):
        db.execute("DELETE FROM evidence WHERE stored < ?", (time.time() - self.retention_seconds,))
        db.execute("DELETE FROM evidence WHERE id IN (SELECT id FROM evidence ORDER BY stored DESC, id DESC "
                   "LIMIT -1 OFFSET ?)", (self.max_rows,))
        db.execute("DELETE FROM symptom_episodes WHERE id IN (SELECT id FROM symptom_episodes "
                   "WHERE json_extract(payload, '$.state')='recovered' ORDER BY updated DESC LIMIT -1 OFFSET 200)")

    def append_observations(self, records: list[ObservationRecord]) -> None:
        with closing(self._connect()) as db, db:
            for record in records:
                if record.schema_version != 2:
                    raise ValueError("New stored observations require version 2 provenance")
                self._put(db, record, "component")
            self._prune(db)

    def append_log_observation(self, record) -> None:
        from core.log_signals import LogObservation

        # Revalidate even constructed/copied models at the persistence boundary.
        record = LogObservation.model_validate(record.model_dump(mode="json"))
        with closing(self._connect()) as db, db:
            self._put(db, record, "log")
            self._prune(db)

    def window(self, environment: str, registry: str, *, service: str | None = None,
               since: float = 0, until: float | None = None, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("Window limit must be between 1 and 1000")
        with closing(self._connect()) as db:
            rows = db.execute("SELECT payload FROM evidence WHERE environment=? AND registry=? "
                              "AND (? IS NULL OR service=?) AND measured>=? AND measured<=? "
                              "AND stored>=? ORDER BY measured DESC, id DESC LIMIT ?",
                              (environment, registry, service, service, since, until if until is not None else time.time(),
                               time.time() - self.retention_seconds, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def record_symptom(self, record: SymptomObservation, *, max_gap: float = 90) -> dict:
        """Debounce observed failures/recovery; unknown never opens or closes an episode."""
        if not 30 <= max_gap <= 635:
            raise ValueError("Unsupported debounce gap")
        age = age_seconds(record.measured_at.timestamp())
        if age is None or age > 60:
            raise ValueError("Symptom evidence is stale or future-dated")
        key = f"{record.environment_id}:{record.registry_digest}:{record.workflow}"
        stamp = record.measured_at.timestamp()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM symptom_state WHERE key=?", (key,)).fetchone()
            state = json.loads(row[0]) if row else {"last_measured": 0, "failures": 0, "successes": 0,
                                                 "active_id": None, "first_failure_id": None}
            if stamp <= state["last_measured"]:
                return {**state, "ignored_out_of_order": True}
            if not row and db.execute("SELECT count(*) FROM symptom_state").fetchone()[0] >= 32:
                raise ValueError("Monitor registry limit reached; archive reviewed old state")
            self._put(db, record, "symptom")
            if stamp - state["last_measured"] > max_gap:
                state.update(failures=0, successes=0, first_failure_id=None)
            state["last_measured"] = stamp
            if record.outcome == "failed":
                if state["failures"] == 0:
                    state["first_failure_id"] = record.evidence_id
                state.update(failures=min(2, state["failures"] + 1), successes=0)
            elif record.outcome == "healthy":
                state.update(failures=0, successes=min(2, state["successes"] + 1), first_failure_id=None)
            else:
                state.update(failures=0, successes=0, first_failure_id=None)
            episode = None
            if state["active_id"]:
                row = db.execute("SELECT payload FROM symptom_episodes WHERE id=?", (state["active_id"],)).fetchone()
                if row is None:
                    raise ValueError("Active symptom episode is missing")
                episode = json.loads(row[0])
            elif state["failures"] >= 2:
                state["active_id"] = f"SYM-{uuid4().hex}"
                episode = {"schema_version": 1, "incident_id": state["active_id"], "mode": "shadow",
                           "environment_id": record.environment_id, "registry_digest": record.registry_digest,
                           "registry_revision": record.registry_revision, "workflow": record.workflow,
                           "symptom_service": record.service_id, "symptom": "CHECKOUT_FAILED",
                           "state": "open", "detected_at": record.measured_at.isoformat(),
                           "candidate_roots": [], "root_cause_node": None,
                           "evidence_ids": [state["first_failure_id"]]}
            if episode is not None:
                episode["evidence_ids"] = (episode["evidence_ids"] + [record.evidence_id])[-32:]
                episode["last_outcome"] = record.outcome
                episode["updated_at"] = record.measured_at.isoformat()
                if state["successes"] >= 2:
                    episode["state"] = "recovered"
                    episode["recovered_at"] = record.measured_at.isoformat()
                    state["active_id"] = None
                db.execute("INSERT INTO symptom_episodes VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE "
                           "SET updated=excluded.updated, payload=excluded.payload",
                           (episode["incident_id"], key, stamp, json.dumps(episode)))
            db.execute("INSERT INTO symptom_state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                       (key, json.dumps(state)))
            self._prune(db)
        return {**state, "episode": episode}

    def episodes(self, environment: str, registry: str, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("Episode limit must be between 1 and 200")
        with closing(self._connect()) as db:
            rows = db.execute("SELECT payload FROM symptom_episodes WHERE key=? ORDER BY updated DESC LIMIT ?",
                              (f"{environment}:{registry}:boutique-checkout-v1", limit)).fetchall()
        return [json.loads(row[0]) for row in rows]
