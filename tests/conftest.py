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

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Real env vars take precedence over .env in pydantic-settings, so tests are
# deterministic even on machines with a populated .env.
if os.getenv("RCA_LIVE_GRAPH_TESTS") != "1":
    os.environ.setdefault("NEO4J_PASSWORD", "test-only-password")
    os.environ.setdefault("LLM_PROVIDER", "gemini")
    os.environ.setdefault("LLM_MODEL", "test-model")


@pytest.fixture
def mock_harmony_tokenizer(monkeypatch):
    """Schema/wire tests use arithmetic tokens, never a downloaded vocabulary."""
    import tiktoken

    class Tokenizer:
        def encode(self, text, **_):
            return [0] * max(1, len(text.encode()) // 4)

    def encoding(name):
        assert name == "o200k_harmony"
        return Tokenizer()

    monkeypatch.setattr(tiktoken, "get_encoding", encoding)
