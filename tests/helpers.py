"""
tests/helpers.py — shared fakes and state builders for the unit suite.
"""

from __future__ import annotations

from typing import Any


class FakeResponse:
    """Mimics a LangChain chat response (content + usage_metadata)."""

    def __init__(self, content: str, total_tokens: int = 42):
        self.content = content
        self.usage_metadata = {"total_tokens": total_tokens}


class FakeLLM:
    """
    Scripted LLM double. Each item in `script` is either a FakeResponse
    (returned) or an Exception (raised). Records how many calls were made.
    """

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


CANDIDATES = [
    {"name": "Redis_Restart_SOP", "description": "Restarts redis-cart",
     "risk_level": "MEDIUM", "script_path": "/sops/redis/restart.sh",
     "script_type": "bash", "trigger_condition": "OOM_KILLED"},
    {"name": "Redis_Flush_SOP", "description": "Flushes stale cache keys",
     "risk_level": "LOW", "script_path": "/sops/redis/cache_flush.sh",
     "script_type": "bash", "trigger_condition": "STALE_DATA"},
]


def make_state(**overrides) -> dict:
    """A fully-initialised AgentState mid-incident, override what you need."""
    state = {
        "alert_id": "INC-TEST0001",
        "alert_service": "frontend",
        "alert_error_type": "HTTP_503",
        "alert_message": "elevated 5xx on storefront",
        "alert_raw": {},
        "root_cause_node": "redis-cart",
        "dependency_chain": ["redis-cart", "cartservice", "frontend"],
        "traversal_depth": 2,
        "current_skill": "Redis_Restart_SOP",
        "current_script": "/sops/redis/restart.sh",
        "current_script_type": "bash",
        "current_description": "Restarts redis-cart",
        "current_risk_level": "MEDIUM",
        "current_trigger": "OOM_KILLED",
        "candidate_skills": [dict(c) for c in CANDIDATES],
        "root_cause_explanation": None,
        "t_alert": 0.0,
        "tokens_used": 0,
        "visited_skills": [],
        "execution_history": [],
        "attempt_count": 0,
        "max_attempts": 5,
        "llm_decision": None,
        "llm_reason": None,
        "fallback_pending": False,
        "all_healthy": False,
        "services_still_unhealthy": 0,
        "rca_report": None,
        "error_message": None,
    }
    state.update(overrides)
    return state
