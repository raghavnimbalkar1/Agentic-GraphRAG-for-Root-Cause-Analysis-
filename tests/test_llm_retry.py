"""
Transient-error retry on the LLM provider call.

A 429/503 blip must be retried with backoff; a non-transient error (bad key)
must raise immediately so the caller's fail-safe-escalate handles it.
"""

from __future__ import annotations

import pytest

import agent.nodes.reasoner as reasoner
from tests.helpers import FakeLLM, FakeResponse


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(reasoner.time, "sleep", lambda _s: None)


def test_transient_error_is_retried_then_succeeds():
    ok = FakeResponse("{}")
    llm = FakeLLM([RuntimeError("429 rate limit exceeded"), ok])
    assert reasoner._invoke_with_retry(llm, []) is ok
    assert llm.calls == 2


def test_non_transient_error_raises_immediately():
    llm = FakeLLM([RuntimeError("invalid API key provided")])
    with pytest.raises(RuntimeError):
        reasoner._invoke_with_retry(llm, [])
    assert llm.calls == 1


def test_exhausted_retries_reraise_last_transient_error():
    llm = FakeLLM([RuntimeError("503 service unavailable")] * 3)
    with pytest.raises(RuntimeError):
        reasoner._invoke_with_retry(llm, [], attempts=3)
    assert llm.calls == 3
