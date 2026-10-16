"""
core/schemas.py

Shared Pydantic models used across agent, graph, sandbox, and eval modules.
All inter-module data contracts live here to prevent circular imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class ServiceStatus(str, Enum):
    """Health states a microservice node can be in."""
    HEALTHY           = "HEALTHY"
    UNKNOWN           = "UNKNOWN"
    DEGRADED          = "DEGRADED"
    DEADLOCK_ERROR    = "DEADLOCK_ERROR"
    OOM_KILLED        = "OOM_KILLED"
    CONNECTION_REFUSED= "CONNECTION_REFUSED"
    CRASH_LOOPING     = "CRASH_LOOPING"
    STALE_DATA        = "STALE_DATA"
    HIGH_CPU          = "HIGH_CPU"
    TABLE_BLOAT       = "TABLE_BLOAT"
    DOWN              = "DOWN"
    # ── Section 1 closed-loop expansion fault states ──────────────────────
    DISK_PRESSURE     = "DISK_PRESSURE"        # writable layer filling up
    POOL_EXHAUSTION   = "POOL_EXHAUSTION"      # connection pool saturated
    CONFIG_DRIFT      = "CONFIG_DRIFT"         # runtime config drifted from baseline
    DEPENDENCY_TIMEOUT= "DEPENDENCY_TIMEOUT"   # service slow / latency over budget
    MEMORY_LEAK       = "MEMORY_LEAK"          # memory growing unbounded


class AlertSeverity(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class ResolutionStatus(str, Enum):
    RESOLVED  = "RESOLVED"
    ESCALATED = "ESCALATED"
    PARTIAL   = "PARTIAL"
    FAILED    = "FAILED"


class ObservationRecord(BaseModel):
    """Versioned collector evidence; old observation fields retain their meaning."""

    schema_version: Literal[1, 2] = 1
    evidence_id: str = Field(default_factory=lambda: f"OBS-{uuid4().hex}")
    service_id: str
    instance_id: str | None = None
    source: Literal["builtin-health-probe"] = "builtin-health-probe"
    interpretation_version: Literal["health-probes-v1"] = "health-probes-v1"
    measured_at: AwareDatetime
    collected_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ServiceStatus
    detail: str
    measurements: dict[str, Any] = Field(default_factory=dict)
    quality: Literal["observed", "unknown"]
    raw_artifact_ref: str | None = None
    environment_id: str | None = None
    registry_revision: str | None = None
    registry_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def evidence_contract(self):
        if self.schema_version == 2 and not all((self.environment_id, self.registry_revision, self.registry_digest)):
            raise ValueError("Version 2 evidence requires deployment provenance")
        if (self.status == "UNKNOWN") != (self.quality == "unknown"):
            raise ValueError("Observation quality must match its status")
        return self


class TransactionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]
    success: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    latency_s: float = Field(ge=0, allow_inf_nan=False)
    marker_found: bool | None = None
    error: Literal["HTTPError", "TimeoutException", "ProbeBudgetExceeded", "ResponseTooLarge",
                   "UnsafeRedirect", "ProbeError"] | None = None

    @model_validator(mode="after")
    def consistent_result(self):
        if self.success and (self.status_code != 200 or self.error is not None or self.marker_found is False):
            raise ValueError("A successful step requires HTTP 200 without a probe or marker failure")
        return self


class SymptomObservation(BaseModel):
    """A transaction symptom, never an attributed root or execution request."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    evidence_id: str = Field(default_factory=lambda: f"SYMOBS-{uuid4().hex}")
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    registry_revision: str = Field(max_length=96)
    registry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    service_id: Literal["frontend"] = "frontend"
    source: Literal["boutique-transaction-canary"] = "boutique-transaction-canary"
    workflow: Literal["boutique-checkout-v1"] = "boutique-checkout-v1"
    started_at: AwareDatetime
    measured_at: AwareDatetime
    collected_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome: Literal["healthy", "failed", "unknown"]
    quality: Literal["observed", "unknown"]
    steps: list[TransactionStep] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def symptom_contract(self):
        if self.measured_at < self.started_at:
            raise ValueError("Transaction completion precedes its start")
        if (self.outcome == "unknown") != (self.quality == "unknown"):
            raise ValueError("Symptom quality must match its outcome")
        names = [s.step for s in self.steps]
        application_steps = names[:-1] if names and names[-1] == "cleanup_cart" else names
        if application_steps != ["home", "add_cart", "read_cart", "checkout"][:len(application_steps)]:
            raise ValueError("Transaction steps must follow workflow order without duplicates")
        if self.outcome != "unknown" and any(
                s.error in {"ProbeBudgetExceeded", "ResponseTooLarge", "UnsafeRedirect", "ProbeError"}
                for s in self.steps):
            raise ValueError("Probe failures are unknown evidence, not application faults")
        if self.outcome == "healthy" and (
                [s.step for s in self.steps] != ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]
                or not all(s.success for s in self.steps)):
            raise ValueError("Healthy transactions need every successful step, including cleanup")
        if self.outcome == "failed" and not any(not s.success and s.step != "cleanup_cart" for s in self.steps):
            raise ValueError("A failed symptom needs a failed application step")
        return self


# ── Input: Alert ───────────────────────────────────────────────────────────

class AlertPayload(BaseModel):
    """
    Incoming alert from chaos injector / monitoring system.
    Posted to POST /alert on the agent webhook server.
    """
    alert_id:    str          = Field(default_factory=lambda: f"INC-{uuid4().hex.upper()}",
                                     pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    service:     str          = Field(..., pattern=r"^[a-z][a-z0-9-]{0,62}$")
    error_type:  str          = Field(..., pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    message:     str          = Field(max_length=8192)
    severity:    AlertSeverity = AlertSeverity.CRITICAL
    timestamp:   datetime      = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:    dict[str, Any]= Field(default_factory=dict)

    model_config = {"use_enum_values": True}

    @field_validator("error_type")
    @classmethod
    def incident_condition(cls, value: str) -> str:
        symptoms = {"HTTP_503", "HTTP_TIMEOUT", "HIGH_ERROR_RATE", "HIGH_LATENCY",
                    "CACHE_ERROR", "STALE_READS", "DEP_UNAVAILABLE", "DEP_ERROR", "DEP_SLOW"}
        allowed = {s.value for s in ServiceStatus} - {"HEALTHY", "UNKNOWN"}
        if value not in allowed | symptoms:
            raise ValueError("Unsupported incident symptom")
        return value


# ── Graph: Retrieval Results ───────────────────────────────────────────────

class DependencyChainResult(BaseModel):
    """Returned by the Neo4j root cause traversal query."""
    root_cause_node:  str
    dependency_chain: list[str]   # ordered: [root, ..., alerting_service]
    depth:            int
    candidate_roots: list[str] = Field(default_factory=list)


class SkillNode(BaseModel):
    """A single SOP node retrieved from the Semantic Skill Graph."""
    name:             str
    script_path:      str
    script_type:      str          = Field(pattern=r"^(python|bash)$")
    description:      str
    params:           list[str]    = Field(default_factory=list)
    timeout_seconds:  int          = Field(default=30, ge=1, le=120)
    risk_level:       str          = Field(default="LOW", pattern=r"^(LOW|MEDIUM|HIGH)$")
    trigger_condition: str         = ""   # the error condition this SOP remediates
    applies_to:       list[str]     = Field(default_factory=list)


# ── Sandbox: Execution ─────────────────────────────────────────────────────

class ExecutionResult(BaseModel):
    """What comes back from the Docker sandbox after running a SOP script."""
    skill_name:    str
    script_path:   str
    exit_code:     int
    stdout:        str  = ""
    stderr:        str  = ""
    duration_s:    float= 0.0
    success:       bool = False
    attempt:       int = 0
    sandbox_cleaned: bool = True
    timestamp:     datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def failed(self) -> bool:
        return not self.success


# ── Output: RCA Report ─────────────────────────────────────────────────────

class RCAReport(BaseModel):
    """
    Final structured report written to /audit after agent resolves or escalates.
    """
    alert_id:          str
    alert_service:     str
    alert_error_type:  str
    root_cause_node:   str
    dependency_chain:  list[str]
    skills_executed:   list[str]
    execution_history: list[ExecutionResult]
    total_hops:        int
    resolution_status: ResolutionStatus
    mttr_seconds:      float | None      = None
    tokens_used:       int | None        = None
    all_services_healthy: bool           = False
    root_cause_explanation: str          = ""   # graph-derived path + LLM rationale
    # ── Decision trail — makes the autonomous choice auditable, not just claimed ──
    candidates_considered: list[str]     = Field(default_factory=list)  # graph-vetted SOP options the LLM chose FROM
    llm_selection_reason:  str           = ""   # why the LLM picked the SOP it did
    timestamp:         datetime          = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes:             str               = ""
    schema_version:    int               = 2
    root_condition:    str               = ""
    potential_blast_radius: list[str]    = Field(default_factory=list)
    verification:      dict[str, Any]    = Field(default_factory=dict)
    attempts:          list[dict[str, Any]] = Field(default_factory=list)
    handling_seconds:  float | None      = None
    candidate_roots: list[str] = Field(default_factory=list)
