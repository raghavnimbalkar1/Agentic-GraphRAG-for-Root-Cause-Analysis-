"""
core/schemas.py

Shared Pydantic models used across agent, graph, sandbox, and eval modules.
All inter-module data contracts live here to prevent circular imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────

class ServiceStatus(str, Enum):
    """Health states a microservice node can be in."""
    HEALTHY           = "HEALTHY"
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


# ── Input: Alert ───────────────────────────────────────────────────────────

class AlertPayload(BaseModel):
    """
    Incoming alert from chaos injector / monitoring system.
    Posted to POST /alert on the agent webhook server.
    """
    alert_id:    str          = Field(default_factory=lambda: f"INC-{uuid4().hex[:8].upper()}")
    service:     str          = Field(..., description="Container name of the alerting service")
    error_type:  ServiceStatus
    message:     str
    severity:    AlertSeverity = AlertSeverity.CRITICAL
    timestamp:   datetime      = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:    dict[str, Any]= Field(default_factory=dict)

    model_config = {"use_enum_values": True}


# ── Graph: Retrieval Results ───────────────────────────────────────────────

class DependencyChainResult(BaseModel):
    """Returned by the Neo4j root cause traversal query."""
    root_cause_node:  str
    dependency_chain: list[str]   # ordered: [root, ..., alerting_service]
    depth:            int


class SkillNode(BaseModel):
    """A single SOP node retrieved from the Semantic Skill Graph."""
    name:             str
    script_path:      str
    script_type:      str          # python | bash
    description:      str
    params:           list[str]    = Field(default_factory=list)
    timeout_seconds:  int          = 30
    risk_level:       str          = "LOW"
    trigger_condition: str         = ""   # the error condition this SOP remediates


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
    tokens_used:       int               = 0
    all_services_healthy: bool           = False
    root_cause_explanation: str          = ""   # graph-derived path + LLM rationale
    timestamp:         datetime          = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes:             str               = ""