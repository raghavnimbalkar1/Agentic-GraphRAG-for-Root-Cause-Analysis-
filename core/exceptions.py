"""
Custom exception definitions for Agentic GraphRAG.

All exceptions inherit from AgenticGraphRAGError for easy catching
and reporting across the entire system.
"""


class AgenticGraphRAGError(Exception):
    """Base exception for all Agentic GraphRAG errors."""

    pass


class Neo4jError(AgenticGraphRAGError):
    """Raised when Neo4j graph operations fail."""

    pass


class GraphQueryError(Neo4jError):
    """Raised when a Cypher query fails or returns unexpected results."""

    pass


class TelemetryCollectionError(AgenticGraphRAGError):
    """Raised when telemetry collection from cluster fails."""

    pass


class ClusterConnectionError(AgenticGraphRAGError):
    """Raised when unable to connect to Docker/Kubernetes cluster."""

    pass


class SandboxExecutionError(AgenticGraphRAGError):
    """Raised when code execution in sandbox fails."""

    pass


class SandboxTimeoutError(SandboxExecutionError):
    """Raised when sandbox execution exceeds timeout."""

    pass


class SandboxSecurityError(SandboxExecutionError):
    """Raised when sandbox detects security violation (escape attempt, etc)."""

    pass


class LLMInferenceError(AgenticGraphRAGError):
    """Raised when LLM inference fails."""

    pass


class ConfigurationError(AgenticGraphRAGError):
    """Raised when configuration is invalid or missing."""

    pass


class SchemaValidationError(AgenticGraphRAGError):
    """Raised when Pydantic schema validation fails."""

    pass
