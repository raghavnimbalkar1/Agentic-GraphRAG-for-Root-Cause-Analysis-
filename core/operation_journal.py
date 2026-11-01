"""Local durable admission records, not restartable capabilities or a transaction with Docker."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

APPLICATION_ID = 0x5243414A
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
TRANSITIONS = {
    "preparing": {"preparing", "effect_attempted", "not_dispatched"},
    "effect_attempted": {"operation_confirmed", "effect_uncertain"},
    "operation_confirmed": {"operation_confirmed", "verifying"},
    "effect_uncertain": {"effect_uncertain"},
    "not_dispatched": {"not_dispatched", "operator_closed"},
    "verifying": {"verified_recovery_window", "verified_mitigation", "not_recovered", "recurrence_observed",
                  "verification_inconclusive"},
    "verified_recovery_window": {"operator_closed"},
    "verified_mitigation": set(), "not_recovered": set(), "recurrence_observed": set(),
    "verification_inconclusive": set(), "operator_closed": set(),
}
RECONCILABLE = frozenset({"preparing", "not_dispatched", "effect_attempted", "effect_uncertain", "operation_confirmed", "verifying",
    "verification_inconclusive", "not_recovered", "recurrence_observed", "verified_mitigation",
    "reconciling", "reconciliation_inconclusive", "reconciled_observed_healthy",
    "cleanup_inspecting", "cleanup_removal_admitted", "cleanup_finished"})
TRANSITIONS.update({"reconciling": {"reconciliation_inconclusive", "reconciled_observed_healthy", "operator_closed"},
                    "reconciliation_inconclusive": set(), "reconciled_observed_healthy": set(),
                    "cleanup_inspecting": {"cleanup_removal_admitted", "cleanup_finished"},
                    "cleanup_removal_admitted": {"cleanup_finished"}, "cleanup_finished": set()})
for _stage in RECONCILABLE:
    TRANSITIONS[_stage].add("reconciling")
for _stage in RECONCILABLE - {"cleanup_inspecting", "cleanup_removal_admitted", "cleanup_finished"}:
    TRANSITIONS[_stage].add("cleanup_inspecting")


def _now():
    return datetime.now(timezone.utc)


class JournalDenied(PermissionError):
    """Fixed local categories; never propagate database paths, tokens or exceptions."""


class AttemptIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    registry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    incident_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    trigger_evidence_id: str = Field(pattern=r"^SYMOBS-[a-f0-9]{32}$")
    plan_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    offer_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    instance_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    skill_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,95}$")
    condition: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    attempt: Literal[1] = 1


def _encoded(value):
    if not isinstance(value, dict):
        raise JournalDenied("invalid_or_oversized_receipt")
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(data.encode()) > MAX_RECEIPT_BYTES:
            raise ValueError()
        return data
    except (ValueError, TypeError, OverflowError):
        raise JournalDenied("invalid_or_oversized_receipt") from None


def _digest(value):
    return sha256(value.encode()).hexdigest()


def sandbox_reservation(value):
    """The exact write-ahead v2 request identity; never infer it from a label search."""
    if (type(value) is not dict or set(value) != {"name", "nonce", "image_id", "role"}
            or any(type(item) is not str for item in value.values())
            or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None
            or value["name"] != f"sop-intent-{value['nonce'][:12]}"
            or re.fullmatch(r"sha256:[a-f0-9]{64}", value["image_id"]) is None
            or value["role"] != "bound-sop-intent"):
        raise JournalDenied("exact_request_reservation_required")
    return dict(value)


def _version(db):
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != 1):
        raise JournalDenied("unsupported_journal_schema")


def _row(db, identifier):
    row = db.execute("SELECT * FROM attempts WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise JournalDenied("attempt_missing")
    try:
        identity = AttemptIdentity.model_validate_json(row["identity"])
        if (identity.environment_id, identity.incident_id, identity.trigger_evidence_id) != (row["environment"], row["incident"], row["trigger"]):
            raise ValueError()
    except ValueError:
        raise JournalDenied("journal_identity_inconsistent") from None
    event = db.execute("SELECT * FROM events WHERE attempt_id=? ORDER BY version DESC LIMIT 1", (identifier,)).fetchone()
    if (type(row["version"]) is not int or not 1 <= row["version"] <= 32 or row["closed"] not in (0, 1)
            or not isinstance(row["owner_hash"], str) or re.fullmatch(r"[a-f0-9]{64}", row["owner_hash"]) is None
            or row["stage"] not in TRANSITIONS or _digest(row["receipt"]) != row["receipt_sha256"] or event is None
            or (event["version"], event["stage"], event["receipt_sha256"]) !=
               (row["version"], row["stage"], row["receipt_sha256"])):
        raise JournalDenied("journal_record_inconsistent")
    barrier = db.execute("SELECT attempt_id FROM barriers WHERE environment=?", (row["environment"],)).fetchone()
    if not row["closed"] and (barrier is None or barrier[0] != row["id"]):
        raise JournalDenied("deployment_barrier_changed")
    return row


def _public(row, *, receipt=False):
    value = {"attempt_id": row["id"], "identity": json.loads(row["identity"]), "stage": row["stage"],
             "version": row["version"], "closed": bool(row["closed"]), "receipt_sha256": row["receipt_sha256"],
             "automatic_retry_allowed": False, "execution_authorized": False}
    if receipt:
        value["receipt"] = json.loads(row["receipt"])
    return value


class OperationJournal:
    def __init__(self, path: Path):
        self.path = Path(path).absolute()

    def _open(self, *, create=False):
        stream, db = None, None
        try:
            if self.path.is_symlink():
                raise JournalDenied("journal_symlink_unsupported")
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            elif not self.path.is_file():
                raise JournalDenied("journal_missing")
            lock = self.path.with_name(self.path.name + ".lock")
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            stream = os.fdopen(fd, "a+b")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalDenied("writer_active") from None
            if not self.path.exists():
                if not create:
                    raise JournalDenied("journal_missing")
                os.close(os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
            db = sqlite3.connect(self.path, timeout=2)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            app = db.execute("PRAGMA application_id").fetchone()[0]
            if create and version == app == 0 and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                db.execute("PRAGMA journal_mode=WAL")
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("CREATE TABLE attempts (id TEXT PRIMARY KEY, environment TEXT NOT NULL, incident TEXT NOT NULL, "
                        "trigger TEXT NOT NULL, owner_hash TEXT NOT NULL, identity TEXT NOT NULL, stage TEXT NOT NULL, "
                        "version INTEGER NOT NULL, receipt TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, closed INTEGER NOT NULL, "
                        "UNIQUE(environment, incident), UNIQUE(environment, trigger))")
                    db.execute("CREATE TABLE barriers (environment TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id))")
                    db.execute("CREATE TABLE events (attempt_id TEXT NOT NULL REFERENCES attempts(id), version INTEGER NOT NULL, "
                        "stage TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, receipt TEXT NOT NULL, recorded_at TEXT NOT NULL, "
                        "PRIMARY KEY(attempt_id, version))")
                    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    db.execute("PRAGMA user_version=1")
            _version(db)
            return db, stream
        except BaseException as error:
            if db is not None:
                db.close()
            if stream is not None:
                stream.close()
            if isinstance(error, (JournalDenied, KeyboardInterrupt, SystemExit)):
                raise
            raise JournalDenied("journal_unavailable") from None

    def reserve(self, identity, receipt):
        if type(identity) is not AttemptIdentity:
            raise JournalDenied("typed_attempt_identity_required")
        try:
            identity = AttemptIdentity.model_validate(identity.model_dump())
        except (ValueError, TypeError):
            raise JournalDenied("invalid_attempt_identity") from None
        encoded, identifier, token = _encoded(receipt), "ATT-" + uuid4().hex, secrets.token_hex(32)
        db, stream = self._open(create=True)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM barriers WHERE environment=?", (identity.environment_id,)).fetchone():
                    raise JournalDenied("deployment_review_required")
                if db.execute("SELECT 1 FROM attempts WHERE environment=? AND (incident=? OR trigger=?)",
                              (identity.environment_id, identity.incident_id, identity.trigger_evidence_id)).fetchone():
                    raise JournalDenied("incident_or_trigger_already_attempted")
                if db.execute("SELECT count(*) FROM attempts").fetchone()[0] >= 1000:
                    raise JournalDenied("journal_capacity_review_required")
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,0)", (identifier, identity.environment_id,
                    identity.incident_id, identity.trigger_evidence_id, _digest(token), _encoded(identity.model_dump()),
                    "preparing", 1, encoded, _digest(encoded)))
                db.execute("INSERT INTO barriers VALUES (?,?)", (identity.environment_id, identifier))
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (identifier, 1, "preparing", _digest(encoded), encoded,
                                                                     _now().isoformat()))
            return AttemptLease(self, db, stream, identifier, token)
        except BaseException as error:
            db.close()
            stream.close()
            if isinstance(error, (JournalDenied, KeyboardInterrupt, SystemExit)):
                raise
            raise JournalDenied("reservation_unconfirmed") from None

    @contextmanager
    def diagnostic_guard(self, environment, incident, *, original_trigger, initialize=False, expected_identity=None):
        """Exclude cooperating mutations during one explicitly requested diagnostic epoch."""
        if (type(initialize) is not bool or re.fullmatch(r"[a-z][a-z0-9-]{0,62}", environment) is None
                or re.fullmatch(r"RPL-[a-f0-9]{64}", incident) is None
                or re.fullmatch(r"SYMOBS-[a-f0-9]{32}", original_trigger) is None):
            raise JournalDenied("invalid_diagnostic_guard")
        db, stream = self._open(create=initialize)
        try:
            guard = DiagnosticOperationGuard(self, db, environment, incident, original_trigger)
            if expected_identity is not None and guard.identity != expected_identity:
                raise JournalDenied("diagnostic_journal_identity_changed")
            guard.check()
            yield guard
            guard.check()
        finally:
            try:
                db.close()
            finally:
                stream.close()

    def resume_verification(self, identifier, token):
        db, stream = self._open()
        try:
            row = _row(db, identifier)
            if (not secrets.compare_digest(row["owner_hash"], _digest(token)) or row["closed"]
                    or row["stage"] != "operation_confirmed"):
                raise JournalDenied("owned_confirmed_attempt_required")
            return AttemptLease(self, db, stream, identifier, token)
        except BaseException as error:
            db.close()
            stream.close()
            if isinstance(error, (JournalDenied, KeyboardInterrupt, SystemExit)):
                raise
            raise JournalDenied("verification_resume_unavailable") from None

    def inspect(self, *, limit=20, receipt=False):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise JournalDenied("invalid_inspection_limit")
        if self.path.is_symlink() or not self.path.is_file():
            raise JournalDenied("journal_missing_or_symlink")
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")  # Read the row, event head and barrier from one WAL snapshot.
                _version(db)
                rows = db.execute("SELECT id FROM attempts ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
                return [_public(_row(db, row[0]), receipt=receipt) for row in rows]
        except (sqlite3.Error, ValueError, TypeError):
            raise JournalDenied("inspection_unavailable_or_inconsistent") from None

    def acknowledge_terminal(self, identifier, *, expected_version, approve_review=False):
        """Release only a terminal no-dispatch or verified-recovery barrier, never uncertainty."""
        if approve_review is not True or type(expected_version) is not int or expected_version < 1:
            raise JournalDenied("explicit_versioned_operator_review_required")
        db, stream = self._open()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = _row(db, identifier)
                if row["version"] != expected_version or row["stage"] not in {"not_dispatched", "verified_recovery_window"}:
                    raise JournalDenied("terminal_review_not_eligible")
                receipt = json.loads(row["receipt"])
                if row["stage"] == "not_dispatched" and (receipt.get("sandbox_cleaned") is False
                        or (receipt.get("sandbox_intent") is not None and receipt.get("sandbox_cleaned") is not True)):
                    raise JournalDenied("request_sandbox_reconciliation_required")
                receipt["operator_review"] = {"previous_stage": row["stage"], "acknowledged": True,
                                               "execution_authorized": False}
                _record(db, row, "operator_closed", receipt)
                db.execute("UPDATE attempts SET closed=1 WHERE id=?", (identifier,))
                db.execute("DELETE FROM barriers WHERE environment=? AND attempt_id=?", (row["environment"], identifier))
            return _public(_row(db, identifier))
        except (sqlite3.Error, ValueError, TypeError):
            raise JournalDenied("terminal_review_unconfirmed") from None
        finally:
            db.close()
            stream.close()

    def begin_reconciliation(self, identifier, *, expected_version, approve_inspection=False):
        """Own a bounded read-only runtime review, never an execution capability."""
        if approve_inspection is not True or type(expected_version) is not int or expected_version < 1:
            raise JournalDenied("explicit_versioned_runtime_inspection_required")
        db, stream = self._open()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = _row(db, identifier)
                if row["closed"] or row["version"] != expected_version or row["stage"] not in RECONCILABLE:
                    raise JournalDenied("reconciliation_not_eligible")
                receipt = json.loads(row["receipt"])
                history = receipt.setdefault("reconciliations", [])
                if not isinstance(history, list) or len(history) >= 3:
                    raise JournalDenied("reconciliation_budget_exhausted")
                for earlier in history:
                    if earlier.get("outcome") == "running":
                        earlier.update(outcome="inconclusive", reason="previous_reconciliation_interrupted")
                source = db.execute("SELECT recorded_at FROM events WHERE attempt_id=? AND stage NOT IN "
                    "('reconciling','reconciliation_inconclusive','reconciled_observed_healthy') ORDER BY version DESC LIMIT 1",
                    (identifier,)).fetchone()
                receipt.setdefault("reconciliation_origin_stage", row["stage"])
                history.append({"outcome": "running", "started_at": _now().isoformat(), "execution_authorized": False})
                _record(db, row, "reconciling", receipt)
            snapshot = _public(_row(db, identifier), receipt=True)
            snapshot["last_runtime_recorded_at"] = source[0]
            return ReconciliationLease(self, db, stream, snapshot)
        except BaseException as error:
            db.close()
            stream.close()
            if isinstance(error, (JournalDenied, KeyboardInterrupt, SystemExit)):
                raise
            raise JournalDenied("reconciliation_start_unconfirmed") from None

    def begin_sandbox_cleanup(self, identifier, *, expected_version, approve_cleanup=False):
        """One explicit cleanup review; retain the barrier and all original effect history."""
        if approve_cleanup is not True or type(expected_version) is not int or expected_version < 1:
            raise JournalDenied("explicit_versioned_sandbox_cleanup_required")
        db, stream = self._open()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = _row(db, identifier)
                if row["closed"] or row["version"] != expected_version or "cleanup_inspecting" not in TRANSITIONS[row["stage"]]:
                    raise JournalDenied("sandbox_cleanup_not_eligible")
                receipt = json.loads(row["receipt"])
                if receipt.get("execution_policy") != "operator-controlled-v2":
                    raise JournalDenied("exact_request_reservation_required")
                sandbox_reservation(receipt.get("sandbox_intent"))
                if "sandbox_cleanup" in receipt:
                    raise JournalDenied("sandbox_cleanup_already_admitted")
                # Reserve three cleanup events and a later two-event health review.
                if row["version"] > 27:
                    raise JournalDenied("sandbox_cleanup_event_budget_exhausted")
                receipt["sandbox_cleanup"] = {"origin_stage": row["stage"], "started_at": _now().isoformat(),
                    "removal_admission": None, "report": None}
                _record(db, row, "cleanup_inspecting", receipt)
            return SandboxCleanupLease(self, db, stream, _public(_row(db, identifier), receipt=True))
        except BaseException as error:
            db.close()
            stream.close()
            if isinstance(error, (JournalDenied, KeyboardInterrupt, SystemExit)):
                raise
            raise JournalDenied("sandbox_cleanup_start_unconfirmed") from None


def _record(db, row, stage, receipt):
    if stage not in TRANSITIONS[row["stage"]] or row["version"] >= 32:
        raise JournalDenied("transition_or_event_budget_rejected")
    encoded, version = _encoded(receipt), row["version"] + 1
    db.execute("UPDATE attempts SET stage=?,version=?,receipt=?,receipt_sha256=? WHERE id=?",
               (stage, version, encoded, _digest(encoded), row["id"]))
    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (row["id"], version, stage, _digest(encoded), encoded,
                                                         _now().isoformat()))


class DiagnosticOperationGuard:
    """Read-only local journal ownership. Not a procedure grant or continuation token."""

    def __init__(self, journal, db, environment, incident, original_trigger):
        self.journal, self._db, self.environment, self.incident = journal, db, environment, incident
        self.original_trigger = original_trigger
        stat = journal.path.stat()
        self.identity = {"device": stat.st_dev, "inode": stat.st_ino}

    def check(self):
        try:
            stat = self.journal.path.stat()
            if self.journal.path.is_symlink() or self.identity != {"device": stat.st_dev, "inode": stat.st_ino}:
                raise JournalDenied("diagnostic_journal_identity_changed")
            _version(self._db)
            if self._db.execute("SELECT 1 FROM barriers WHERE environment=?", (self.environment,)).fetchone():
                raise JournalDenied("deployment_review_required")
            if self._db.execute("SELECT 1 FROM attempts WHERE environment=? AND (incident=? OR trigger=?)",
                                (self.environment, self.incident, self.original_trigger)).fetchone():
                raise JournalDenied("diagnostic_incident_has_operation_history")
        except JournalDenied:
            raise
        except (sqlite3.Error, OSError):
            raise JournalDenied("diagnostic_journal_check_unavailable") from None

    def __reduce__(self):
        raise TypeError("Diagnostic journal owners cannot be serialized")


class AttemptLease:
    """An in-memory owner plus an OS writer lock; never reconstructed from an audit receipt."""

    def __init__(self, journal, db, stream, identifier, token):
        self.journal, self._db, self._stream, self.identifier, self._token = journal, db, stream, identifier, token
        stat = journal.path.stat()
        self._file_identity = (stat.st_dev, stat.st_ino)

    def __repr__(self):
        return "<AttemptLease: private local owner>"

    def __reduce__(self):
        raise TypeError("Journal owners cannot be serialized or copied")

    def __copy__(self):
        raise TypeError("Journal owners cannot be serialized or copied")

    def __deepcopy__(self, memo):
        raise TypeError("Journal owners cannot be serialized or copied")

    def record(self, stage, receipt):
        if self._db is None:
            raise JournalDenied("lease_closed")
        try:
            stat = self.journal.path.stat()
            if self.journal.path.is_symlink() or (stat.st_dev, stat.st_ino) != self._file_identity:
                raise JournalDenied("journal_file_replaced")
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                row = _row(self._db, self.identifier)
                if row["closed"] or not secrets.compare_digest(row["owner_hash"], _digest(self._token)):
                    raise JournalDenied("attempt_not_owned")
                barrier = self._db.execute("SELECT attempt_id FROM barriers WHERE environment=?", (row["environment"],)).fetchone()
                if barrier is None or barrier[0] != self.identifier:
                    raise JournalDenied("deployment_barrier_changed")
                _record(self._db, row, stage, receipt)
        except JournalDenied:
            raise
        except (sqlite3.Error, OSError):
            raise JournalDenied("journal_write_unconfirmed") from None

    def close(self):
        db, stream, self._db, self._stream = self._db, self._stream, None, None
        try:
            if db is not None:
                db.close()
        finally:
            if stream is not None:
                stream.close()


class ReconciliationLease:
    """Exclusive review only. Public receipts cannot reconstruct its live writer lock."""

    def __init__(self, journal, db, stream, snapshot):
        self.journal, self._db, self._stream, self.snapshot = journal, db, stream, snapshot
        stat = journal.path.stat()
        self._file_identity = (stat.st_dev, stat.st_ino)

    def __reduce__(self):
        raise TypeError("Reconciliation owners cannot be serialized or copied")

    def __copy__(self):
        raise TypeError("Reconciliation owners cannot be serialized or copied")

    def __deepcopy__(self, memo):
        raise TypeError("Reconciliation owners cannot be serialized or copied")

    def finish(self, report, *, approve_current_state_closure=False):
        if self._db is None or type(approve_current_state_closure) is not bool:
            raise JournalDenied("owned_reconciliation_required")
        try:
            stat = self.journal.path.stat()
            if self.journal.path.is_symlink() or (stat.st_dev, stat.st_ino) != self._file_identity:
                raise JournalDenied("journal_file_replaced")
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                row = _row(self._db, self.snapshot["attempt_id"])
                if row["stage"] != "reconciling" or row["version"] != self.snapshot["version"]:
                    raise JournalDenied("reconciliation_superseded")
                if (report["execution_authorized"] is not False or report["automatic_retry_allowed"] is not False
                        or report["recovery_verified"] is not False or report["causal_attribution"] is not False
                        or report["outcome"] not in {"observed_healthy", "inconclusive"}):
                    raise JournalDenied("invalid_reconciliation_report")
                healthy = report["outcome"] == "observed_healthy"
                now = _now()
                if healthy and (not 0 <= (now - datetime.fromisoformat(report["checked_at"])).total_seconds() < 5
                                or now >= datetime.fromisoformat(report["valid_until"])):
                    raise JournalDenied("reconciliation_evidence_expired")
                receipt = json.loads(row["receipt"])
                receipt["reconciliations"][-1] = report
                closed = healthy and approve_current_state_closure
                stage = "operator_closed" if closed else "reconciled_observed_healthy" if healthy else "reconciliation_inconclusive"
                if closed:
                    receipt["operator_review"] = {"acknowledged": True, "kind": "observed-current-state-not-effect-proof",
                                                  "execution_authorized": False}
                _record(self._db, row, stage, receipt)
                if closed:
                    self._db.execute("UPDATE attempts SET closed=1 WHERE id=?", (row["id"],))
                    self._db.execute("DELETE FROM barriers WHERE environment=? AND attempt_id=?", (row["environment"], row["id"]))
            return {**_public(_row(self._db, row["id"])), "current_state_closed": bool(closed)}
        except JournalDenied:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            raise JournalDenied("reconciliation_finish_unconfirmed") from None

    def close(self):
        db, stream, self._db, self._stream = self._db, self._stream, None, None
        try:
            if db is not None:
                db.close()
        finally:
            if stream is not None:
                stream.close()


class SandboxCleanupLease:
    """Exclusive one-shot request removal, not a grant to mutate a managed service."""

    def __init__(self, journal, db, stream, snapshot):
        self.journal, self._db, self._stream, self.snapshot = journal, db, stream, snapshot
        stat = journal.path.stat()
        self._file_identity = (stat.st_dev, stat.st_ino)

    def __reduce__(self):
        raise TypeError("Cleanup owners cannot be serialized or copied")

    def __copy__(self):
        raise TypeError("Cleanup owners cannot be serialized or copied")

    def __deepcopy__(self, memo):
        raise TypeError("Cleanup owners cannot be serialized or copied")

    def check(self):
        if self._db is None:
            raise JournalDenied("cleanup_owner_closed")
        stat = self.journal.path.stat()
        if self.journal.path.is_symlink() or (stat.st_dev, stat.st_ino) != self._file_identity:
            raise JournalDenied("journal_file_replaced")
        row = _row(self._db, self.snapshot["attempt_id"])
        if row["closed"] or row["version"] != self.snapshot["version"] or row["stage"] != self.snapshot["stage"]:
            raise JournalDenied("sandbox_cleanup_superseded")
        return row

    def admit_removal(self, container_id):
        if not isinstance(container_id, str) or re.fullmatch(r"[a-f0-9]{64}", container_id) is None:
            raise JournalDenied("immutable_sandbox_id_required")
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                row = self.check()
                if row["stage"] != "cleanup_inspecting":
                    raise JournalDenied("sandbox_removal_already_admitted")
                receipt = json.loads(row["receipt"])
                receipt["sandbox_cleanup"]["removal_admission"] = {
                    "container_id": container_id, "admitted_at": _now().isoformat(), "outcome": "unknown"}
                _record(self._db, row, "cleanup_removal_admitted", receipt)
            self.snapshot = _public(_row(self._db, row["id"]), receipt=True)
        except JournalDenied:
            raise
        except (sqlite3.Error, OSError):
            raise JournalDenied("sandbox_removal_admission_unconfirmed") from None

    def finish(self, report):
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                row = self.check()
                if (row["stage"] not in {"cleanup_inspecting", "cleanup_removal_admitted"}
                        or report.get("outcome") not in {"absent", "inconclusive"}
                        or any(report.get(key) is not False for key in
                               ("execution_authorized", "automatic_retry_allowed", "recovery_verified", "current_state_closed"))):
                    raise JournalDenied("invalid_sandbox_cleanup_report")
                if report["outcome"] == "absent" and not 0 <= (
                        _now() - datetime.fromisoformat(report["checked_at"])).total_seconds() < 5:
                    raise JournalDenied("sandbox_absence_observation_expired")
                receipt = json.loads(row["receipt"])
                receipt["sandbox_cleanup"]["report"] = report
                _record(self._db, row, "cleanup_finished", receipt)
            return _public(_row(self._db, row["id"]))
        except JournalDenied:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            raise JournalDenied("sandbox_cleanup_finish_unconfirmed") from None

    def close(self):
        db, stream, self._db, self._stream = self._db, self._stream, None, None
        try:
            if db is not None:
                db.close()
        finally:
            if stream is not None:
                stream.close()
