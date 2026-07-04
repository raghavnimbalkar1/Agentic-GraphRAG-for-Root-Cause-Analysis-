"""
The graph-as-allowlist security invariant, as unit tests.

The LLM may only execute a SOP present in the graph-derived candidate set.
Anything else — a hallucinated name, another service's SOP, an injection
string, null, or unparseable output — must fail SAFE to "escalate" and must
never populate execution fields from LLM text.
"""

from __future__ import annotations

import json

import pytest

import agent.nodes.reasoner as reasoner
from tests.helpers import FakeLLM, FakeResponse, make_state


def _decision(monkeypatch, llm_script, **state_overrides):
    fake = FakeLLM(llm_script)
    monkeypatch.setattr(reasoner, "_get_llm", lambda: fake)
    result = reasoner.llm_decide(make_state(**state_overrides))
    return result, fake


def _llm_json(action, chosen, reason="r", explanation="e") -> FakeResponse:
    return FakeResponse(json.dumps({
        "action": action, "chosen_skill": chosen,
        "reason": reason, "root_cause_explanation": explanation,
    }))


# ── Executions outside the allowlist are refused ──────────────────────────

def test_non_candidate_skill_escalates(monkeypatch):
    result, _ = _decision(monkeypatch, [_llm_json("execute", "EXFILTRATE_SECRETS_SOP")])
    assert result["llm_decision"] == "escalate"
    assert "allowlist" in result["llm_reason"].lower()


def test_null_chosen_skill_on_execute_escalates(monkeypatch):
    result, _ = _decision(monkeypatch, [_llm_json("execute", None)])
    assert result["llm_decision"] == "escalate"


def test_injection_style_skill_name_escalates(monkeypatch):
    result, _ = _decision(monkeypatch, [_llm_json("execute", "; rm -rf / #")])
    assert result["llm_decision"] == "escalate"


def test_unparseable_output_fails_safe_to_escalate(monkeypatch):
    garbage = FakeResponse("I think you should restart redis. Good luck!")
    result, fake = _decision(monkeypatch, [garbage, garbage])
    assert result["llm_decision"] == "escalate"
    assert fake.calls == 2  # one retry, then fail-safe
    assert "unparseable" in result["llm_reason"].lower()


def test_llm_hard_failure_escalates(monkeypatch):
    result, _ = _decision(monkeypatch, [RuntimeError("invalid api key")])
    assert result["llm_decision"] == "escalate"
    assert "failed" in result["llm_reason"].lower()


# ── Valid executions come from the graph record, never LLM text ───────────

def test_valid_candidate_executes_with_graph_sourced_script(monkeypatch):
    result, _ = _decision(monkeypatch, [_llm_json("execute", "Redis_Flush_SOP")])
    assert result["llm_decision"] == "execute"
    assert result["current_skill"] == "Redis_Flush_SOP"
    # script path must be the GRAPH candidate's path, not anything LLM-authored
    assert result["current_script"] == "/sops/redis/cache_flush.sh"
    assert result["current_risk_level"] == "LOW"
    assert result["current_trigger"] == "STALE_DATA"


def test_markdown_fenced_json_is_parsed(monkeypatch):
    fenced = FakeResponse(
        "```json\n" + json.dumps({
            "action": "execute", "chosen_skill": "Redis_Restart_SOP",
            "reason": "r", "root_cause_explanation": "e"}) + "\n```"
    )
    result, _ = _decision(monkeypatch, [fenced])
    assert result["llm_decision"] == "execute"
    assert result["current_skill"] == "Redis_Restart_SOP"


def test_skip_makes_no_execution_selection(monkeypatch):
    result, _ = _decision(monkeypatch, [_llm_json("skip", None)])
    assert result["llm_decision"] == "skip"


# ── Empty candidate set never calls the LLM ────────────────────────────────

def test_no_candidates_escalates_without_llm_call(monkeypatch):
    def _boom():
        raise AssertionError("LLM must not be constructed with no candidates")
    monkeypatch.setattr(reasoner, "_get_llm", _boom)
    result = reasoner.llm_decide(make_state(candidate_skills=[]))
    assert result["llm_decision"] == "escalate"


# ── Structured explanation is graph-derived ────────────────────────────────

def test_explanation_backbone_is_deterministic_from_chain():
    state = make_state()
    text = reasoner._build_root_cause_explanation(state, "model narrative")
    assert "redis-cart" in text
    assert "frontend → cartservice → redis-cart" in text
    assert "2-hop" in text
    assert text.endswith("Agent rationale: model narrative")


def test_explanation_without_llm_text_has_no_rationale_suffix():
    text = reasoner._build_root_cause_explanation(make_state(), "")
    assert "Agent rationale" not in text
