"""
core/__init__.py

Public API for the core module.
Any other module should import from here, not from submodules directly.

    from core import settings, get_logger
    from core.schemas import AlertPayload, RCAReport
    from core.exceptions import AgentError, SkillNotFoundError
"""

from core.config import settings, get_settings
from core.logging_config import get_logger, setup_logging
from core.schemas import (
    AlertPayload,
    AlertSeverity,
    DependencyChainResult,
    ExecutionResult,
    RCAReport,
    ResolutionStatus,
    ServiceStatus,
    SkillNode,
)
from core.exceptions import (
    AgentError,
    ContainerTimeoutError,
    GraphError,
    HealthCheckError,
    LLMError,
    LLMParseError,
    RootCauseNotFoundError,
    SandboxError,
    SkillNotFoundError,
    SOPExecutionError,
)

__all__ = [
    # Config
    "settings",
    "get_settings",
    # Logging
    "get_logger",
    "setup_logging",
    # Schemas
    "AlertPayload",
    "AlertSeverity",
    "DependencyChainResult",
    "ExecutionResult",
    "RCAReport",
    "ResolutionStatus",
    "ServiceStatus",
    "SkillNode",
    # Exceptions
    "AgentError",
    "ContainerTimeoutError",
    "GraphError",
    "HealthCheckError",
    "LLMError",
    "LLMParseError",
    "RootCauseNotFoundError",
    "SandboxError",
    "SkillNotFoundError",
    "SOPExecutionError",
]