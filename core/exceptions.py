"""
core/exceptions.py

Custom exception hierarchy.
All modules raise these — never bare Exception or ValueError.

Hierarchy:
    AgentError
    ├── GraphError
    │   ├── RootCauseNotFoundError
    │   └── SkillNotFoundError
    ├── SandboxError
    │   ├── SOPExecutionError
    │   └── ContainerTimeoutError
    ├── LLMError
    │   └── LLMParseError
    └── HealthCheckError
"""


class AgentError(Exception):
    """Base exception for all project errors."""

    def __init__(self, message: str, context: dict | None = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:
        if self.context:
            ctx = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} [{ctx}]"
        return self.message


# ── Graph exceptions ───────────────────────────────────────────────────────

class GraphError(AgentError):
    """Neo4j query or traversal failure."""


class RootCauseNotFoundError(GraphError):
    """
    Raised when no unhealthy upstream node is found for the alerting service.
    The alerting service itself may be the root cause.
    """
    def __init__(self, alert_service: str):
        super().__init__(
            f"No upstream root cause found for '{alert_service}'.",
            context={"alert_service": alert_service},
        )


class SkillNotFoundError(GraphError):
    """
    Raised when no SOP skill matches the root cause node + error type.
    """
    def __init__(self, node: str, error_type: str):
        super().__init__(
            f"No skill found for node='{node}' with error_type='{error_type}'.",
            context={"node": node, "error_type": error_type},
        )


# ── Sandbox exceptions ─────────────────────────────────────────────────────

class SandboxError(AgentError):
    """Docker sandbox setup or configuration failure."""


class SOPExecutionError(SandboxError):
    """
    Raised when a SOP script exits with non-zero code.
    Carries exit_code and stderr for logging.
    """
    def __init__(self, skill_name: str, exit_code: int, stderr: str):
        super().__init__(
            f"SOP '{skill_name}' failed with exit code {exit_code}.",
            context={"skill_name": skill_name, "exit_code": exit_code,
                     "stderr": stderr[:500]},  # truncate long stderr
        )
        self.exit_code = exit_code
        self.stderr    = stderr


class ContainerTimeoutError(SandboxError):
    """Raised when the sandbox container exceeds its timeout."""
    def __init__(self, skill_name: str, timeout_s: int):
        super().__init__(
            f"SOP '{skill_name}' container killed after {timeout_s}s timeout.",
            context={"skill_name": skill_name, "timeout_s": timeout_s},
        )


# ── LLM exceptions ────────────────────────────────────────────────────────

class LLMError(AgentError):
    """LLM API call failure."""


class LLMParseError(LLMError):
    """
    Raised when the LLM returns malformed output (not valid JSON, missing fields).
    """
    def __init__(self, raw_output: str):
        super().__init__(
            "LLM returned unparseable output.",
            context={"raw_output": raw_output[:300]},
        )
        self.raw_output = raw_output


# ── Health check exceptions ───────────────────────────────────────────────

class HealthCheckError(AgentError):
    """Raised when a service health check itself fails (network/timeout)."""
    def __init__(self, service: str, reason: str):
        super().__init__(
            f"Health check failed for '{service}': {reason}",
            context={"service": service, "reason": reason},
        )