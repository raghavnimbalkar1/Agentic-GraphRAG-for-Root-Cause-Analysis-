"""
tests/conftest.py

Unit tests run with NO external dependencies: no Neo4j, no Docker daemon,
no LLM API. Required env vars are seeded before any project import so
core.config.Settings never fails on a machine without a .env file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Real env vars take precedence over .env in pydantic-settings, so tests are
# deterministic even on machines with a populated .env.
os.environ.setdefault("NEO4J_PASSWORD", "test-only-password")
os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("LLM_MODEL", "test-model")
