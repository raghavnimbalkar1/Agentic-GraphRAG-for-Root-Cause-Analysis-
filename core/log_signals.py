"""Fixed log signals and read-only retrieval; raw application text is never retained."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from typing import Literal, get_args
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from core.diagnostics import DiagnosticRequest
from core.registry import DEFAULT_DEPLOYMENT

Operation = Literal["order_submission", "cart_read", "cart_write", "cart_clear",
                    "currency_lookup", "catalog_lookup", "shipping_quote", "other_request"]
GrpcCode = Literal["Canceled", "Unknown", "InvalidArgument", "DeadlineExceeded", "NotFound",
                   "AlreadyExists", "PermissionDenied", "ResourceExhausted", "FailedPrecondition",
                   "Aborted", "OutOfRange", "Unimplemented", "Internal", "Unavailable", "DataLoss",
                   "Unauthenticated"]
OPERATIONS = {
    "failed to complete the order": "order_submission",
    "could not retrieve cart": "cart_read", "failed to add to cart": "cart_write",
    "failed to empty cart": "cart_clear", "could not retrieve currencies": "currency_lookup",
    "could not retrieve products": "catalog_lookup", "could not retrieve product": "catalog_lookup",
    "failed to get shipping quote": "shipping_quote",
}
CODE = re.compile(r"rpc error: code = ([A-Za-z]+) desc =")
GRPC_CODES = set(get_args(GrpcCode))
EVENT_LIMIT = 24
BATCH_LIMIT = 10


class LogSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(pattern=r"^LOGEV-[a-f0-9]{64}$")
    measured_at: AwareDatetime
    operation: Operation
    grpc_codes: list[GrpcCode] = Field(max_length=4)


class LogObservation(BaseModel):
    """A bounded scan, not an assertion that all errors or healthy requests were seen."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    evidence_id: str = Field(default_factory=lambda: f"LOGOBS-{uuid4().hex}", pattern=r"^LOGOBS-[a-f0-9]{32}$")
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    registry_revision: str = Field(max_length=96)
    registry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    service_id: Literal["frontend"] = "frontend"
    instance_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source: Literal["docker-frontend-json"] = "docker-frontend-json"
    interpretation_version: Literal["boutique-frontend-v0.10.5-v1"] = "boutique-frontend-v0.10.5-v1"
    window_start: AwareDatetime
    measured_at: AwareDatetime
    collected_at: AwareDatetime
    outcome: Literal["sampled", "limited", "unavailable", "denied", "unsupported"]
    reason: Literal["none", "docker_unavailable", "ownership_mismatch", "unsupported_image",
                    "target_changed", "unsupported_format", "byte_limit", "line_limit",
                    "event_limit", "deadline", "invalid_frame"]
    bytes_read: int = Field(default=0, ge=0, le=262144, strict=True)
    lines_read: int = Field(default=0, ge=0, le=500, strict=True)
    discarded_lines: int = Field(default=0, ge=0, le=500, strict=True)
    signals: list[LogSignal] = Field(default_factory=list, max_length=EVENT_LIMIT)

    @model_validator(mode="after")
    def consistent_scan(self):
        if not 0 < (self.measured_at - self.window_start).total_seconds() <= 900:
            raise ValueError("Invalid log collection window")
        if self.collected_at < self.measured_at or self.discarded_lines > self.lines_read:
            raise ValueError("Inconsistent log collection metadata")
        if (self.outcome == "sampled") != (self.reason == "none"):
            raise ValueError("Scan outcome must disclose an incomplete collection")
        expected = {
            "sampled": {"none"}, "limited": {"byte_limit", "line_limit", "event_limit", "deadline"},
            "unavailable": {"docker_unavailable", "invalid_frame"},
            "denied": {"ownership_mismatch", "target_changed"},
            "unsupported": {"unsupported_image", "unsupported_format"},
        }
        if self.reason not in expected[self.outcome] or len(self.signals) > self.lines_read:
            raise ValueError("Inconsistent scan outcome or signal count")
        if self.signals and self.instance_id is None:
            raise ValueError("Log signals require a runtime identity")
        for signal in self.signals:
            if not self.window_start <= signal.measured_at <= self.measured_at:
                raise ValueError("Log signal is outside its collection window")
            if signal.evidence_id != signal_id(self.instance_id, signal.measured_at,
                                               signal.operation, signal.grpc_codes):
                raise ValueError("Log signal identity does not match its normalized fields")
        return self


def signal_id(instance: str, stamp: datetime, operation: str, codes: list[str]) -> str:
    # Never hash raw messages: their hashes could leak low-entropy sensitive values.
    fields = [instance, stamp.astimezone(timezone.utc).isoformat(), operation, codes]
    return "LOGEV-" + sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()


def frontend_signal(line: bytes, instance: str, start: datetime, end: datetime) -> tuple[bool, LogSignal | None]:
    """Project reviewed JSON fields to enums. Ignore paths, sessions, headers and message text."""
    try:
        prefix, body = line.decode("utf-8", errors="strict").split(" ", 1)
        stamp = datetime.fromisoformat(prefix.replace("Z", "+00:00"))
        data = json.loads(body)
        if (stamp.tzinfo is None or not start <= stamp <= end or not isinstance(data, dict)
                or not isinstance(data.get("message"), str)
                or data.get("severity") not in {"debug", "info", "warning", "error", "fatal", "panic"}):
            return False, None
        stamp = stamp.astimezone(timezone.utc)
        if data["message"] != "request error" or data["severity"] != "error":
            return True, None
        error = data.get("error")
        if not isinstance(error, str):
            return False, None
        operation = next((value for prefix, value in OPERATIONS.items()
                          if error == prefix or error.startswith(prefix + ": ")), "other_request")
        codes = [code for code in CODE.findall(error) if code in GRPC_CODES][:4]
        return True, LogSignal(evidence_id=signal_id(instance, stamp, operation, codes), measured_at=stamp,
                               operation=operation, grpc_codes=codes)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return False, None


class LogReader:
    """Read retained scan batches without Docker, settings, pruning or repair imports."""

    def __init__(self, path: Path):
        self.path = path

    def window(self, request: DiagnosticRequest, *, now: datetime | None = None) -> dict:
        request = DiagnosticRequest.model_validate(request)
        if request.service_id != "frontend" or request.metric is not None:
            raise ValueError("The log pilot supports only frontend error signals")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Log reader requires an aware clock")
        end = request.end_at or now
        if end > now:
            raise ValueError("Log windows cannot end in the future")
        start = end.timestamp() - request.window_seconds
        registry = DEFAULT_DEPLOYMENT
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported evidence schema")
            rows = db.execute(
                "SELECT id, measured, CASE WHEN length(CAST(payload AS BLOB))<=8192 THEN payload ELSE NULL END "
                "FROM evidence WHERE environment=? AND registry=? AND service='frontend' AND kind='log' "
                "AND measured BETWEEN ? AND ? AND stored BETWEEN ? AND ? "
                "ORDER BY measured DESC, id DESC LIMIT ?",
                (registry.environment_id, registry.digest, start, end.timestamp(),
                 now.timestamp() - 86400, now.timestamp(), BATCH_LIMIT + 1)).fetchall()
        batches = []
        for identifier, measured, payload in reversed(rows[:BATCH_LIMIT]):
            record = LogObservation.model_validate_json(payload or "{}")
            if (record.evidence_id != identifier or record.environment_id != registry.environment_id
                    or record.registry_revision != registry.revision or record.registry_digest != registry.digest
                    or record.measured_at.timestamp() != measured or record.collected_at > now):
                raise ValueError("Log evidence provenance or collection time is inconsistent")
            if record.window_start > end:
                continue
            projected = record.model_dump(mode="json")
            projected["signals"] = [signal.model_dump(mode="json") for signal in record.signals
                                    if start <= signal.measured_at.timestamp() <= end.timestamp()]
            batches.append(projected)
        result = {"schema_version": 1, "mode": "retained-evidence-only", "service_id": "frontend",
                  "environment_id": registry.environment_id, "registry_digest": registry.digest,
                  "registry_revision": registry.revision, "read_at": now.isoformat(),
                  "window_start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                  "window_end": end.isoformat(), "batch_limit": BATCH_LIMIT,
                  "truncated": len(rows) > BATCH_LIMIT, "sampling": "tail-500-per-scan",
                  "batches": batches}
        if len(json.dumps(result).encode()) > 131072:
            raise ValueError("Log response exceeds size limit")
        return result
