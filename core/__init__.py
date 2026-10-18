"""
core/__init__.py

Public API for the core module.
Any other module should import from here, not from submodules directly.

    from core import settings, get_logger
    from core.schemas import AlertPayload, RCAReport
    from core.exceptions import AgentError, SkillNotFoundError
"""

from importlib import import_module

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


def __getattr__(name):
    # Data-only readers must not load .env just by importing a core submodule.
    modules = {"settings": "core.config", "get_settings": "core.config",
               "get_logger": "core.logging_config", "setup_logging": "core.logging_config"}
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(modules[name]), name)
    globals()[name] = value
    return value


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
