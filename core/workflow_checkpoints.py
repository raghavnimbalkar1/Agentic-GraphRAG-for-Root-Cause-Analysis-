"""One-shot diagnostic checkpoints. Retained state is history, never a resumable capability."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

APPLICATION_ID = 0x52434157
MAX_PAYLOAD = 2 * 1024 * 1024
MAX_DATABASE = 64 * 1024 * 1024


class CheckpointDenied(PermissionError):
    """Fixed categories only; no database paths, exception text or provider payloads."""


def _plain(value, depth=0):
    if depth > 40:
        raise CheckpointDenied("checkpoint_value_rejected")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) in (list, tuple):
        for item in value:
            _plain(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _plain(item, depth + 1)
        return
    raise CheckpointDenied("checkpoint_value_rejected")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointDenied("checkpoint_duplicate_key")
        result[key] = value
    return result


class PlainJsonSerializer:
    """No pickle, import revival, constructor records or executable object loading."""

    def dumps_typed(self, value):
        _plain(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_PAYLOAD:
            raise CheckpointDenied("checkpoint_payload_limit")
        return "rca-json-v1", encoded

    def loads_typed(self, value):
        kind, encoded = value
        if kind != "rca-json-v1" or not isinstance(encoded, bytes) or len(encoded) > MAX_PAYLOAD:
            raise CheckpointDenied("checkpoint_encoding_rejected")
        try:
            decoded = json.loads(encoded, object_pairs_hook=_object)
            _plain(decoded)
            return decoded
        except (ValueError, TypeError, RecursionError):
            raise CheckpointDenied("checkpoint_value_rejected") from None


SERIALIZER = PlainJsonSerializer()


def diagnostic_thread_id(environment, symptom):
    return "WF-" + sha256(f"{environment}:{symptom}".encode()).hexdigest()


class DiagnosticIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    registry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    symptom_evidence_id: str = Field(pattern=r"^SYMOBS-[a-f0-9]{32}$")
    as_of: AwareDatetime
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    implementation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    driver_kind: Literal["model", "scripted", "deterministic"]

    @property
    def thread_id(self):
        # Changing prompts, cutoffs or model profiles cannot replay this same trigger.
        return diagnostic_thread_id(self.environment_id, self.symptom_evidence_id)


def _schema(db):
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != 1):
        raise CheckpointDenied("unsupported_workflow_store")


class DiagnosticCheckpoints:
    def __init__(self, path):
        self.path = Path(path).absolute()

    def _guard(self, db, file_identity, thread_id):
        stat = self.path.stat()
        if self.path.is_symlink() or (stat.st_dev, stat.st_ino) != file_identity:
            raise CheckpointDenied("workflow_file_replaced")
        size = sum(path.stat().st_size for path in (self.path, Path(str(self.path) + "-wal")) if path.exists())
        if size > MAX_DATABASE:
            raise CheckpointDenied("workflow_storage_limit")
        for table, maximum in (("checkpoints", 64), ("writes", 512)):
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone():
                count = db.execute(f"SELECT count(*) FROM {table} WHERE thread_id=?", (thread_id,)).fetchone()[0]
                if count >= maximum:
                    raise CheckpointDenied("workflow_event_limit")

    def invoke(self, builder, initial, identity):
        """Run a new shadow graph once; never accepts a saved checkpoint ID or resume command."""
        if type(identity) is not DiagnosticIdentity:
            raise CheckpointDenied("typed_diagnostic_identity_required")
        identity = DiagnosticIdentity.model_validate(identity.model_dump())
        SERIALIZER.dumps_typed(initial)
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            raise CheckpointDenied("workflow_persistence_extra_required") from None
        db, stream, admitted = None, None, False
        try:
            if self.path.is_symlink():
                raise CheckpointDenied("workflow_symlink_unsupported")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + ".lock")
            stream = os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a+b")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CheckpointDenied("workflow_writer_active") from None
            if not self.path.exists():
                os.close(os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
            stat = self.path.stat()
            file_identity = stat.st_dev, stat.st_ino
            db = sqlite3.connect(self.path, timeout=2, check_same_thread=False)
            db.execute("PRAGMA synchronous=FULL")
            if (db.execute("PRAGMA application_id").fetchone()[0] == 0
                    and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()):
                db.execute("PRAGMA journal_mode=WAL")
                with db:
                    db.execute("CREATE TABLE diagnostic_runs (thread_id TEXT PRIMARY KEY, identity TEXT NOT NULL, "
                        "state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT)")
                    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    db.execute("PRAGMA user_version=1")
            _schema(db)
            with db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM diagnostic_runs WHERE thread_id=?", (identity.thread_id,)).fetchone():
                    raise CheckpointDenied("workflow_already_admitted_review_required")
                if db.execute("SELECT count(*) FROM diagnostic_runs").fetchone()[0] >= 256:
                    raise CheckpointDenied("workflow_capacity_review_required")
                self._guard(db, file_identity, identity.thread_id)
                db.execute("INSERT INTO diagnostic_runs VALUES (?,?,?,?,NULL)", (identity.thread_id,
                    identity.model_dump_json(), "running", datetime.now(timezone.utc).isoformat()))
            admitted = True
            outer, writer = self, threading.RLock()

            class BoundedSaver(SqliteSaver):
                def put(self, *args, **kwargs):
                    with writer:
                        outer._guard(db, file_identity, identity.thread_id)
                        return super().put(*args, **kwargs)

                def put_writes(self, config, writes, task_id, task_path=""):
                    with writer:
                        outer._guard(db, file_identity, identity.thread_id)
                        safe = [(name, "diagnostic_node_interrupted" if name == "__error__" else value)
                                for name, value in writes]
                        return super().put_writes(config, safe, task_id, task_path)

            saver = BoundedSaver(db, serde=SERIALIZER)
            compiled = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": identity.thread_id}, "recursion_limit": 20}
            result = compiled.invoke(initial, config=config, durability="sync")
            head = compiled.get_state(config)
            if head.next or not result.get("stopped") or result.get("result") is None:
                raise CheckpointDenied("workflow_terminal_checkpoint_missing")
            self._guard(db, file_identity, identity.thread_id)
            with db:
                db.execute("UPDATE diagnostic_runs SET state='completed',finished_at=? WHERE thread_id=?",
                           (datetime.now(timezone.utc).isoformat(), identity.thread_id))
            return result
        except BaseException as error:
            if db is not None and admitted:
                try:
                    with db:
                        db.execute("UPDATE diagnostic_runs SET state='interrupted',finished_at=? WHERE thread_id=?",
                                   (datetime.now(timezone.utc).isoformat(), identity.thread_id))
                except (sqlite3.Error, OSError):
                    pass  # A running record is also review-only; never dispatch on uncertainty.
            if isinstance(error, (CheckpointDenied, KeyboardInterrupt, SystemExit)):
                raise
            raise CheckpointDenied("workflow_persistence_unconfirmed") from None
        finally:
            try:
                if db is not None:
                    db.close()
            except (sqlite3.Error, OSError):
                raise CheckpointDenied("workflow_close_unconfirmed") from None
            finally:
                if stream is not None:
                    stream.close()

    def inspect(self, *, limit=20, include_state=False):
        """One read-only WAL snapshot. No graph compilation, settings or live clients."""
        if type(limit) is not int or not 1 <= limit <= 100 or type(include_state) is not bool:
            raise CheckpointDenied("invalid_workflow_inspection_options")
        if self.path.is_symlink() or not self.path.is_file():
            raise CheckpointDenied("workflow_missing_or_symlink")
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.execute("BEGIN")
                _schema(db)
                rows = db.execute("SELECT thread_id,identity,state,started_at,finished_at FROM diagnostic_runs "
                                  "ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
                has_checkpoints = db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints' AND type='table'").fetchone()
                has_continuations = db.execute("SELECT 1 FROM sqlite_master WHERE name='diagnostic_continuations' AND type='table'").fetchone()
                result = []
                for identifier, encoded, status, started, finished in rows:
                    identity = DiagnosticIdentity.model_validate_json(encoded)
                    if identifier != identity.thread_id or status not in {"running", "completed", "interrupted"}:
                        raise CheckpointDenied("workflow_identity_inconsistent")
                    head = db.execute("SELECT checkpoint_id,type,checkpoint FROM checkpoints WHERE thread_id=? "
                        "AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1", (identifier,)).fetchone() if has_checkpoints else None
                    values = SERIALIZER.loads_typed((head[1], head[2]))["channel_values"] if head else {}
                    context = values.get("checkpoint_context") or values.get("__start__", {}).get("checkpoint_context")
                    if context is not None and sha256(json.dumps(context, sort_keys=True).encode()).hexdigest() != identity.context_digest:
                        raise CheckpointDenied("workflow_context_inconsistent")
                    if status == "completed" and (values.get("result") is None or not values.get("stopped")):
                        raise CheckpointDenied("workflow_terminal_checkpoint_missing")
                    complete = status == "completed"
                    summary = {"thread_id": identifier, "identity": identity.model_dump(mode="json"),
                        "state": "completed_history" if complete else "interrupted_or_active_review_required",
                        "started_at": started, "finished_at": finished, "checkpoint_id": head[0] if head else None,
                        "checkpointed_decisions": len(values.get("attempts", [])),
                        "checkpointed_reported_tokens": values.get("known_tokens", 0),
                        "usage_complete": complete and values.get("usage_complete") is True,
                        "pending_work_may_have_run": not complete, "candidate_is_historical": True,
                        "automatic_resume_allowed": False, "execution_authorized": False, "recovery_verified": False}
                    if include_state:
                        summary["saved_state"] = values
                    if has_continuations:
                        claim = db.execute("SELECT child_thread_id FROM diagnostic_continuations WHERE thread_id=?", (identifier,)).fetchone()
                        summary["continuation_record"] = {"present": claim is not None,
                            "claimed_child": claim[0] if claim else None, "requires_explicit_review": True}
                    result.append(summary)
                return result
        except CheckpointDenied:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise CheckpointDenied("workflow_inspection_unavailable_or_inconsistent") from None

    @contextmanager
    def _continuation_writer(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise CheckpointDenied("workflow_missing_or_symlink")
        db, stream = None, None
        try:
            stream = os.fdopen(os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a+b")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CheckpointDenied("workflow_writer_active") from None
            db = sqlite3.connect(self.path, timeout=2)
            db.execute("PRAGMA synchronous=FULL")
            _schema(db)
            stat = self.path.stat()
            identity = stat.st_dev, stat.st_ino
            self._guard(db, identity, "")
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS diagnostic_continuations (thread_id TEXT PRIMARY KEY "
                    "REFERENCES diagnostic_runs(thread_id), payload BLOB NOT NULL, digest TEXT NOT NULL, "
                    "child_thread_id TEXT UNIQUE, claimed_at TEXT)")
            yield db
            self._guard(db, identity, "")
        except CheckpointDenied:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError):
            raise CheckpointDenied("workflow_continuation_write_unconfirmed") from None
        finally:
            try:
                if db is not None:
                    db.close()
            finally:
                if stream is not None:
                    stream.close()

    @staticmethod
    def _completed_head(db, identifier):
        if not isinstance(identifier, str) or re.fullmatch(r"WF-[a-f0-9]{64}", identifier) is None:
            raise CheckpointDenied("invalid_continuation_thread")
        row = db.execute("SELECT identity,state FROM diagnostic_runs WHERE thread_id=?", (identifier,)).fetchone()
        if row is None or row[1] != "completed":
            raise CheckpointDenied("completed_diagnostic_checkpoint_required")
        identity = DiagnosticIdentity.model_validate_json(row[0])
        head = db.execute("SELECT checkpoint_id,type,checkpoint FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' "
                          "ORDER BY checkpoint_id DESC LIMIT 1", (identifier,)).fetchone()
        if identity.thread_id != identifier or head is None:
            raise CheckpointDenied("workflow_identity_inconsistent")
        values = SERIALIZER.loads_typed((head[1], head[2]))["channel_values"]
        if (values.get("stopped") is not True or values.get("result") is None or values.get("usage_complete") is not True
                or sha256(json.dumps(values["checkpoint_context"], sort_keys=True).encode()).hexdigest() != identity.context_digest):
            raise CheckpointDenied("completed_known_diagnostic_checkpoint_required")
        return identity, head[0], values

    def save_controller(self, identifier, payload):
        """Attach immutable data after native completion; a missing attachment is not resumable."""
        with self._continuation_writer() as db:
            identity, head, values = self._completed_head(db, identifier)
            self._bind_controller(payload, identity, values)
            body = {**payload, "native_identity": identity.model_dump(mode="json"), "checkpoint_id": head}
            _, encoded = SERIALIZER.dumps_typed(body)
            with db:
                db.execute("INSERT INTO diagnostic_continuations VALUES (?,?,?,NULL,NULL)",
                           (identifier, encoded, sha256(encoded).hexdigest()))

    @staticmethod
    def _bind_controller(payload, identity, values):
        snapshot = payload["state"]["snapshot"]
        saved_budget, native_budget = snapshot["incident_budget"], values["checkpoint_budget"]
        for key in ("calls_used", "response_bytes", "decisions_used", "admission_units", "known_reported_tokens",
                    "profile", "limits", "usage_complete", "decision_in_flight", "stop_reason", "evidence_elapsed_seconds"):
            if saved_budget[key] != native_budget[key]:
                raise CheckpointDenied("controller_native_budget_mismatch")
        if (type(saved_budget["remaining_seconds"]) not in (int, float)
                or not 0 <= saved_budget["remaining_seconds"] <= native_budget["remaining_seconds"]
                or sha256(json.dumps(payload["implementation"], sort_keys=True).encode()).hexdigest() != identity.implementation_digest):
            raise CheckpointDenied("controller_native_budget_or_source_mismatch")
        report = snapshot["epochs"][-1]["report"]
        for key, native_key in (("result", "result"), ("turns", "turns"), ("attempts", "attempts"),
                                ("known_reported_tokens", "known_tokens"), ("session", "checkpoint_session"),
                                ("initial_context", "checkpoint_context")):
            if report[key] != values[native_key]:
                raise CheckpointDenied("controller_native_report_mismatch")

    @staticmethod
    def _controller_row(db, identifier):
        identity, head, values = DiagnosticCheckpoints._completed_head(db, identifier)
        row = db.execute("SELECT payload,digest,child_thread_id FROM diagnostic_continuations WHERE thread_id=?", (identifier,)).fetchone()
        if row is None or sha256(row[0]).hexdigest() != row[1]:
            raise CheckpointDenied("controller_receipt_missing_or_inconsistent")
        payload = SERIALIZER.loads_typed(("rca-json-v1", row[0]))
        if payload["native_identity"] != identity.model_dump(mode="json") or payload["checkpoint_id"] != head:
            raise CheckpointDenied("controller_checkpoint_changed")
        DiagnosticCheckpoints._bind_controller(payload, identity, values)
        completed = db.execute("SELECT finished_at FROM diagnostic_runs WHERE thread_id=?", (identifier,)).fetchone()[0]
        return {"payload": payload, "digest": row[1], "claimed_child": row[2], "completed_at": completed}

    def load_controller(self, identifier):
        if self.path.is_symlink() or not self.path.is_file():
            raise CheckpointDenied("workflow_missing_or_symlink")
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.execute("BEGIN")
                _schema(db)
                return self._controller_row(db, identifier)
        except CheckpointDenied:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError):
            raise CheckpointDenied("controller_receipt_unavailable") from None

    def claim_continuation(self, parent, child, *, expected_digest):
        """Commit one child claim before its graph or provider; never refund an uncertain claim."""
        if not isinstance(child, str) or re.fullmatch(r"WF-[a-f0-9]{64}", child) is None or child == parent:
            raise CheckpointDenied("new_continuation_trigger_required")
        with self._continuation_writer() as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = self._controller_row(db, parent)
                if row["digest"] != expected_digest or row["claimed_child"] is not None:
                    raise CheckpointDenied("continuation_changed_or_already_claimed")
                if db.execute("SELECT 1 FROM diagnostic_runs WHERE thread_id=?", (child,)).fetchone():
                    raise CheckpointDenied("continuation_trigger_already_admitted")
                db.execute("UPDATE diagnostic_continuations SET child_thread_id=?,claimed_at=? WHERE thread_id=?",
                           (child, datetime.now(timezone.utc).isoformat(), parent))
