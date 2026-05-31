"""
Centralized logging configuration for all modules.

Uses loguru for structured logging with JSON output for Phase 2+ analysis.

Usage:
    from core.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Event occurred", extra={"service_id": "svc-123"})
"""

import sys
from pathlib import Path
from typing import Optional

from loguru import logger as _logger

from core.config import get_config


def setup_logging(log_level: Optional[str] = None) -> None:
    """
    Initialize structured logging for the application.
    
    Args:
        log_level: Override log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    config = get_config()
    level = log_level or config.log_level

    # Remove default handler
    _logger.remove()

    # Console output with color
    _logger.add(
        sys.stderr,
        format="<level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=level,
        colorize=True,
    )

    # File output with structured JSON (Phase 2+)
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    _logger.add(
        str(log_dir / "agentic_graphrag.log"),
        format="{message}",
        level=level,
        serialize=config.environment == "production",  # JSON in production
        retention="7 days",
    )

    _logger.add(
        str(log_dir / "errors.log"),
        format="{message}",
        level="ERROR",
        serialize=False,
    )


def get_logger(name: str) -> "_logger.__class__":
    """Get a logger instance with the given name."""
    return _logger.bind(module=name)
