"""
core/logging_config.py

Configures structlog for the entire project.
Call setup_logging() once at application startup (in agent/main.py).

- Development (LOG_LEVEL=DEBUG): coloured, human-readable console output
- Production  (LOG_LEVEL=INFO+): structured JSON, one line per event

Usage:
    from core.logging_config import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
    log.info("agent_started", port=8888)
"""

import logging
import sys

import structlog

from core.config import settings, LogLevel


def setup_logging() -> None:
    """
    Call once at startup. Idempotent — safe to call multiple times.
    """
    log_level_str = settings.log_level.value          # "DEBUG" | "INFO" etc.
    log_level     = getattr(logging, log_level_str, logging.INFO)
    is_dev        = settings.log_level == LogLevel.DEBUG

    # ── Shared processors ─────────────────────────────────────
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,       # thread-local context
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_dev:
        # Pretty coloured output for local development
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # JSON for anything that might be scraped by a log aggregator
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "docker", "neo4j"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """
    Returns a bound structlog logger.
    Preferred over logging.getLogger() throughout the project.

    Usage:
        log = get_logger(__name__)
        log.info("event_name", key="value")
    """
    return structlog.get_logger(name)