"""
Pydantic schema definitions for type-safe data validation.

These schemas are used throughout the system to validate:
- Agent state in LangGraph nodes
- Tool inputs via OpenClaw
- Graph entities and relationships
- Sandbox execution requests and results
- Telemetry events

Phase 1: Basic schemas for simulation telemetry and graph entities.
Phase 2+: Extended schemas for remediation SOPs, agent decisions, etc.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, ConfigDict


class ServiceEntity(BaseModel):
    """Represents a microservice in the cluster topology."""

    model_config = ConfigDict(populate_by_name=True)

    service_id: str = Field(description="Unique service identifier")
    name: str = Field(description="Human-readable service name")
    namespace: Optional[str] = Field(default=None, description="Kubernetes namespace or compose service")
    container_image: str = Field(description="Container image URI")
    replicas: int = Field(default=1, description="Number of running instances")
    status: Literal["running", "pending", "failed", "unknown"] = Field(default="unknown")
    ports: Dict[str, int] = Field(default_factory=dict, description="Exposed ports mapping")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PodEntity(BaseModel):
    """Represents a container pod/instance in the cluster."""

    pod_id: str
    service_id: str
    status: Literal["running", "pending", "failed", "terminating"]
    ip_address: Optional[str] = None
    node: Optional[str] = None  # For Kubernetes
    cpu_usage_percent: Optional[float] = None
    memory_usage_mb: Optional[float] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RemediationSOP(BaseModel):
    """Represents a remediation Standard Operating Procedure in the graph."""

    sop_id: str
    name: str
    description: str
    category: str  # e.g., "database", "network", "compute"
    applicable_services: List[str] = Field(description="List of service_ids this SOP applies to")
    preconditions: Dict[str, str] = Field(default_factory=dict)
    remediation_script: str = Field(description="Python code to execute in sandbox")
    validation_queries: List[str] = Field(description="Cypher queries to validate fix")
    rollback_script: Optional[str] = None
    estimated_duration_seconds: int = Field(default=60)
    risk_level: Literal["low", "medium", "high"] = Field(default="medium")
    owner_team: Optional[str] = None


class TelemetryEvent(BaseModel):
    """Represents a telemetry event (log, metric, trace) from the cluster."""

    event_id: str
    timestamp: datetime
    source_service: str
    source_pod: Optional[str] = None
    event_type: Literal["log", "metric", "trace", "error"]
    severity: Literal["debug", "info", "warning", "error", "critical"]
    message: str
    structured_data: Dict[str, Any] = Field(default_factory=dict)
    raw_payload: Optional[Dict[str, Any]] = None


class GraphBlastRadiusResult(BaseModel):
    """Result of querying the graph for blast radius of a given failure."""

    root_cause_service: str
    affected_services: List[str]
    affected_pods: List[str]
    recommended_sops: List[RemediationSOP]
    graph_path_explanation: str


class SandboxExecutionRequest(BaseModel):
    """Request to execute code in a sandboxed Docker container."""

    request_id: str
    script_code: str
    timeout_seconds: int = Field(default=30)
    memory_limit_mb: int = Field(default=512)
    cpu_limit: float = Field(default=0.5)
    environment_vars: Dict[str, str] = Field(default_factory=dict)
    read_only_mounts: Dict[str, str] = Field(
        default_factory=dict,
        description="Host path -> container path (read-only)"
    )


class SandboxExecutionResult(BaseModel):
    """Result of sandbox code execution."""

    request_id: str
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    execution_time_seconds: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentSystemState(BaseModel):
    """
    Typed state dictionary for LangGraph nodes.
    
    CRITICAL: All state must flow through this schema to maintain
    type safety and auditability across the ReAct loop.
    """

    # Current state in the reasoning loop
    phase: Literal["ingestion", "analysis", "planning", "execution", "verification", "escalation"]

    # Telemetry and context
    current_telemetry: List[TelemetryEvent] = Field(default_factory=list)
    blast_radius: Optional[GraphBlastRadiusResult] = None

    # Reasoning artifacts
    llm_reasoning_steps: List[str] = Field(default_factory=list)
    identified_root_causes: List[str] = Field(default_factory=list)
    candidate_sops: List[RemediationSOP] = Field(default_factory=list)

    # Execution history
    executed_actions: List[Dict[str, Any]] = Field(default_factory=list)
    sandbox_results: List[SandboxExecutionResult] = Field(default_factory=list)

    # Decision points
    should_escalate: bool = Field(default=False)
    escalation_reason: Optional[str] = None
    confidence_score: float = Field(default=0.0)

    # Audit trail
    messages: List[Dict[str, str]] = Field(default_factory=list, description="ReAct loop messages")
    timestamp_started: datetime = Field(default_factory=datetime.utcnow)
    timestamp_last_updated: datetime = Field(default_factory=datetime.utcnow)


# Tool schema stubs (Phase 3+)
class ToolInput(BaseModel):
    """Base class for all tool inputs - validated by OpenClaw before execution."""
    pass


class GraphQueryInput(ToolInput):
    """Input for Neo4j graph query tool (Phase 2+)."""
    cypher_query: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class LLMInferenceInput(ToolInput):
    """Input for LLM inference tool (Phase 3+)."""
    prompt: str
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024)


class SandboxExecutionInput(ToolInput):
    """Input for sandbox code execution tool (Phase 4+)."""
    request: SandboxExecutionRequest
