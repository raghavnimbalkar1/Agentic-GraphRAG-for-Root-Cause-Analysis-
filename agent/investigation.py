"""Retained-evidence investigation boundary, not an enabled LLM or repair workflow."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.diagnostics import ComponentReader, DiagnosticRequest, TransactionReader
from core.log_signals import LogReader
from core.registry import DEFAULT_DEPLOYMENT

TOOL_NAMES = ("component_window", "resource_window", "transaction_evidence", "error_signals")


class ToolRequest(DiagnosticRequest):
    tool: Literal["component_window", "resource_window", "transaction_evidence", "error_signals"]
    # The trusted session fixes time. A caller cannot move the evidence cutoff.
    end_at: None = None

    @model_validator(mode="after")
    def fixed_fields(self):
        if (self.tool == "resource_window") != (self.metric is not None):
            raise ValueError("Only resource windows require a metric")
        if self.tool in {"transaction_evidence", "error_signals"} and self.service_id != "frontend":
            raise ValueError("This adapter supports only frontend evidence")
        return self


class InvestigationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_tool_calls: int = Field(default=8, ge=1, le=8, strict=True)
    max_seconds: int = Field(default=180, ge=1, le=180, strict=True)
    max_response_bytes: int = Field(default=65536, ge=512, le=65536, strict=True)
    max_total_bytes: int = Field(default=131072, ge=512, le=131072, strict=True)


class HypothesisProposal(BaseModel):
    """An observation-backed possibility, never a causal or execution authorization."""

    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    condition: Literal["readiness_failure", "resource_pressure", "request_failure"]
    supporting_evidence: list[str] = Field(min_length=1, max_length=8)
    contradicting_evidence: list[str] = Field(default_factory=list, max_length=8)
    unresolved_alternatives: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def distinct_references(self):
        refs = self.supporting_evidence + self.contradicting_evidence
        if len(set(refs)) != len(refs) or any(len(value) > 128 for value in refs):
            raise ValueError("Evidence references must be distinct bounded identifiers")
        alternatives = self.unresolved_alternatives
        if len(set(alternatives)) != len(alternatives) or self.service_id in alternatives:
            raise ValueError("Alternatives must be distinct from the proposed service")
        return self


class InvestigationSession:
    """One trusted caller-owned session; no network, settings, model or executor dependency."""

    def __init__(self, path: Path, *, symptom_evidence_id: str, as_of: datetime,
                 limits: InvestigationLimits | None = None,
                 clock=time.monotonic):
        if as_of.tzinfo is None or as_of > datetime.now(timezone.utc):
            raise ValueError("Investigation cutoff must be aware and not in the future")
        self.path, self.as_of, self.clock = path, as_of.astimezone(timezone.utc), clock
        self.limits = InvestigationLimits.model_validate((limits or InvestigationLimits()).model_dump())
        self.started = clock()
        self._calls, self._bytes, self._closed = 0, 0, False
        self._requested, self._evidence, self._history = set(), {}, []
        self._terminal = None
        self._active_request = None
        self._lock = threading.RLock()
        rows = TransactionReader(path).window(DiagnosticRequest(service_id="frontend", window_seconds=900),
                                              now=self.as_of.timestamp())["observations"]
        symptom = next((row for row in rows if row["evidence_id"] == symptom_evidence_id), None)
        if (symptom is None or symptom["outcome"] != "failed"
                or (self.as_of - datetime.fromisoformat(symptom["measured_at"])).total_seconds() > 90):
            raise ValueError("A recent retained failed transaction is required")
        self._symptom = symptom
        self._bytes = len(json.dumps(symptom, allow_nan=False).encode())
        if self._bytes > self.limits.max_total_bytes:
            raise ValueError("Initial symptom exceeds the context budget")
        self._evidence.update(self._citations(ToolRequest(tool="transaction_evidence", service_id="frontend"),
                                              {"observations": [symptom]}))

    def initial_context(self) -> dict:
        return {"mode": "retained-shadow", "symptom": json.loads(json.dumps(self._symptom)),
                "availability_cutoff": self.as_of.isoformat(), "tools": list(TOOL_NAMES),
                "root_cause_node": None, "execution_authorized": False}

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict:
        return {"mode": "retained-shadow", "availability_cutoff": self.as_of.isoformat(),
                "environment_id": DEFAULT_DEPLOYMENT.environment_id, "registry_digest": DEFAULT_DEPLOYMENT.digest,
                "calls_used": self._calls, "response_bytes": self._bytes, "closed": self._closed,
                "symptom_evidence_id": self._symptom["evidence_id"], "termination_reason": self._terminal,
                "issued_evidence_ids": sorted(self._evidence), "history": json.loads(json.dumps(self._history)),
                "root_cause_node": None, "execution_authorized": False}

    def _expired(self):
        return self.clock() - self.started >= self.limits.max_seconds

    def _result(self, outcome, *, tool=None, data=None):
        row = {"call": self._calls, "tool": tool, "outcome": outcome, "request": self._active_request,
               "recorded_at": datetime.now(timezone.utc).isoformat()}
        self._history.append(json.loads(json.dumps(row)))
        return {**row, "mode": "retained-shadow", "availability_cutoff": self.as_of.isoformat(), "data": data}

    def call(self, raw_request: dict) -> dict:
        with self._lock:
            return self._call(raw_request)

    def _call(self, raw_request: dict) -> dict:
        self._active_request = None
        if self._closed:
            return {"outcome": "closed", "data": None}
        if self._expired():
            self._closed, self._terminal = True, "time_exhausted"
            return self._result("time_exhausted")
        if self._calls >= self.limits.max_tool_calls:
            self._closed, self._terminal = True, "budget_exhausted"
            return self._result("budget_exhausted")
        self._calls += 1  # Invalid, repeated and failed calls consume the same incident budget.
        try:
            if not isinstance(raw_request, dict) or len(json.dumps(raw_request).encode()) > 4096:
                raise ValueError("Invalid request size or representation")
            request = ToolRequest.model_validate(raw_request)
            if request.service_id not in DEFAULT_DEPLOYMENT.by_name:
                raise ValueError("Unregistered target")
        except (ValueError, TypeError, RecursionError):
            return self._result("denied")
        self._active_request = {"service_id": request.service_id, "window_seconds": request.window_seconds,
                                "metric": request.metric}
        key = sha256(request.model_dump_json().encode()).hexdigest()
        if key in self._requested:
            return self._result("duplicate", tool=request.tool)
        self._requested.add(key)
        arguments = DiagnosticRequest(service_id=request.service_id, metric=request.metric,
                                      window_seconds=request.window_seconds, end_at=self.as_of)
        try:
            if request.tool == "error_signals":
                data = LogReader(self.path).window(arguments, now=self.as_of)
            elif request.tool == "transaction_evidence":
                data = TransactionReader(self.path).window(arguments, now=self.as_of.timestamp())
            else:
                data = ComponentReader(self.path).window(arguments, now=self.as_of.timestamp())
            if self._expired():
                self._closed, self._terminal = True, "time_exhausted"
                return self._result("time_exhausted", tool=request.tool)
            data["evaluated_as_of"] = data.pop("read_at")
            if "summary" in data:
                data["summary"]["fresh_at_cutoff"] = data["summary"].pop("fresh_now")
            size = len(json.dumps(data, allow_nan=False).encode())
            if size > self.limits.max_response_bytes or self._bytes + size > self.limits.max_total_bytes:
                return self._result("output_limit", tool=request.tool)
            citations = self._citations(request, data)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return self._result("unavailable", tool=request.tool)
        self._bytes += size
        self._evidence.update(citations)
        return self._result("ok", tool=request.tool, data=data)

    def _citations(self, request, data):
        citations = {}
        if request.tool == "error_signals":
            for batch in data["batches"]:
                for signal in batch["signals"]:
                    citations[signal["evidence_id"]] = {"service": "frontend", "stamp": signal["measured_at"],
                        "observed": batch["outcome"] == "sampled", "condition": "request_failure", "max_age": 60,
                        "supports": batch["outcome"] == "sampled"}
        elif request.tool == "transaction_evidence":
            for row in data["observations"]:
                citations[row["evidence_id"]] = {"service": "frontend", "stamp": row["measured_at"],
                    "observed": row["quality"] == "observed",
                    "condition": "request_failure" if row["outcome"] == "failed" else None, "max_age": 90,
                    "supports": row["outcome"] == "failed"}
        else:
            for row in data["samples"]:
                if (re.fullmatch(r"OBS-[a-f0-9]{32}", row["evidence_id"]) is None
                        or (row["instance_id"] is not None and re.fullmatch(r"[a-f0-9]{64}", row["instance_id"]) is None)):
                    raise ValueError("Investigation requires canonical observation and Docker instance IDs")
                condition = None
                if row["status"] in {"DOWN", "CONNECTION_REFUSED", "CRASH_LOOPING", "DEGRADED", "DEPENDENCY_TIMEOUT"}:
                    condition = "readiness_failure"
                elif row["status"] in {"HIGH_CPU", "OOM_KILLED", "MEMORY_LEAK", "DISK_PRESSURE", "POOL_EXHAUSTION"}:
                    condition = "resource_pressure"
                citations[row["evidence_id"]] = {"service": request.service_id, "stamp": row["measured_at"],
                    "observed": row["quality"] == "observed", "condition": condition, "max_age": 30,
                    "numeric_resource": (request.tool == "resource_window" and row["value"] is not None
                                         and request.metric in {"cpu_percent", "memory_usage_bytes", "writable_layer_bytes"}),
                    "supports": False}
            if citations and not data["summary"]["latest_conflict"]:
                latest = max(datetime.fromisoformat(row["stamp"]) for row in citations.values())
                for row in citations.values():
                    row["supports"] = datetime.fromisoformat(row["stamp"]) == latest
        return citations

    def propose(self, raw_proposal: dict) -> dict:
        """One terminal proposal attempt. Validation checks clues, not causal truth."""
        with self._lock:
            return self._propose(raw_proposal)

    def _propose(self, raw_proposal: dict) -> dict:
        if self._closed:
            return {"outcome": "closed", "execution_authorized": False}
        self._closed = True
        if self._expired():
            self._terminal = "time_exhausted"
            return {"outcome": "time_exhausted", "execution_authorized": False}
        try:
            if not isinstance(raw_proposal, dict) or len(json.dumps(raw_proposal).encode()) > 4096:
                raise ValueError("Invalid proposal size or representation")
            proposal = HypothesisProposal.model_validate(raw_proposal)
            if any(name not in DEFAULT_DEPLOYMENT.by_name for name in
                   [proposal.service_id, *proposal.unresolved_alternatives]):
                raise ValueError("Unregistered hypothesis target")
            supporting = []
            for identifier in proposal.supporting_evidence + proposal.contradicting_evidence:
                evidence = self._evidence[identifier]
                age = (self.as_of - datetime.fromisoformat(evidence["stamp"])).total_seconds()
                if not evidence["observed"] or not 0 <= age <= evidence["max_age"]:
                    raise ValueError("Evidence is unknown or stale at the decision cutoff")
                if identifier in proposal.supporting_evidence:
                    supporting.append(evidence)
            if not any(row["supports"] and row["service"] == proposal.service_id
                       and (row["condition"] == proposal.condition
                            or (proposal.condition == "resource_pressure" and row.get("numeric_resource", False)))
                       for row in supporting):
                raise ValueError("A proposal requires a matching target-specific clue")
        except (ValueError, TypeError, KeyError, RecursionError):
            self._terminal = "proposal_denied"
            return {"outcome": "denied", "execution_authorized": False}
        self._terminal = "proposal"
        return {"outcome": "proposal", "mode": "shadow-proposal-only", "proposal": proposal.model_dump(),
                "availability_cutoff": self.as_of.isoformat(), "causal_attribution": False,
                "execution_authorized": False}

    def abstain(self, reason: str) -> dict:
        with self._lock:
            if self._closed:
                return {"outcome": "closed", "execution_authorized": False}
            self._closed = True
            if self._expired():
                self._terminal = "time_exhausted"
                return {"outcome": "time_exhausted", "execution_authorized": False}
            if reason not in ("insufficient_evidence", "unknown_condition", "unresolved_alternatives", "provider_unavailable"):
                self._terminal = "abstention_denied"
                return {"outcome": "denied", "execution_authorized": False}
            self._terminal = f"abstained:{reason}"
            return {"outcome": "abstained", "reason": reason, "mode": "retained-shadow",
                    "causal_attribution": False, "execution_authorized": False}
