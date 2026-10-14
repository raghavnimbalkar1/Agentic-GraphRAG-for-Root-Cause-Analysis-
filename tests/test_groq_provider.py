"""Groq configuration, wire compatibility, and a non-executing smoke check."""

import json
import warnings
from types import SimpleNamespace

import httpx
import langchain_openai
import pytest

import agent.nodes.reasoner as reasoner
from core.config import LLMProvider, Settings
from scripts import check_llm
from tests.helpers import FakeLLM, FakeResponse, make_state


def groq_settings(model="openai/gpt-oss-120b", key="test-groq-key"):
    return SimpleNamespace(
        llm_provider=LLMProvider.GROQ, llm_model=model, groq_api_key=key,
        llm_timeout=30, llm_max_tokens=2048,
    )


def test_groq_key_is_loaded_without_openai_warning_and_hidden_in_repr(tmp_path):
    with warnings.catch_warnings(record=True) as recorded:
        config = Settings(
            _env_file=None, llm_provider="groq", groq_api_key="test-groq-secret",
            openai_api_key="", neo4j_password="test-only", sops_dir=tmp_path / "sops",
            audit_dir=tmp_path / "audit",
        )
    assert config.groq_api_key == "test-groq-secret"
    assert config.llm_provider == LLMProvider.GROQ
    assert "test-groq-secret" not in repr(config)
    assert not any("OPENAI_API_KEY" in str(w.message) for w in recorded)


@pytest.mark.parametrize("model", ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "other-model"])
def test_groq_factory_uses_explicit_endpoint_key_and_model_options(monkeypatch, model):
    monkeypatch.setattr(reasoner, "settings", groq_settings(model))
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **kwargs: kwargs)
    args = reasoner._get_llm()
    assert args["base_url"] == "https://api.groq.com/openai/v1"
    assert args["api_key"] == "test-groq-key"
    assert args["model"] == model
    assert args["timeout"] == 30 and args["max_tokens"] == 2048
    assert args["max_retries"] == 0
    assert args["model_kwargs"]["response_format"] == {"type": "json_object"}
    assert args["extra_body"] == (
        {} if model == "other-model" else {"reasoning_effort": "low", "include_reasoning": False}
    )


@pytest.mark.parametrize("key", ["", "   "])
def test_missing_groq_key_fails_closed(monkeypatch, key):
    monkeypatch.setattr(reasoner, "settings", groq_settings(key=key))
    result = reasoner.llm_decide(make_state())
    assert result["llm_decision"] == "escalate"
    assert "GROQ_API_KEY is required" in result["llm_reason"]


def test_groq_http_response_flows_through_real_reasoner_validation(monkeypatch):
    monkeypatch.setattr(reasoner, "settings", groq_settings())
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-groq-key"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
        assert body.get("max_completion_tokens", body.get("max_tokens")) == 2048
        return httpx.Response(200, json={
            "id": "synthetic-response", "object": "chat.completion", "created": 0,
            "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({
                    "action": "execute", "chosen_skill": "Redis_Restart_SOP",
                    "reason": "Matches the observed condition.",
                    "root_cause_explanation": "Redis is on the supplied dependency path.",
                }),
            }}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
        })

    chat_class = langchain_openai.ChatOpenAI
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(langchain_openai, "ChatOpenAI",
                            lambda **kwargs: chat_class(http_client=client, **kwargs))
        result = reasoner.llm_decide(make_state())
    assert len(requests) == 1
    assert result["llm_decision"] == "execute"
    assert result["current_script"] == "/sops/redis/restart.sh"
    assert result["tokens_used"] == 27 and result["token_usage_complete"] is True


@pytest.mark.parametrize("chosen,passed", [("Redis_Config_Reset_SOP", True), ("invented", False)])
def test_connection_check_is_one_inference_with_no_execution(monkeypatch, chosen, passed):
    fake = FakeLLM([FakeResponse(json.dumps({
        "action": "execute", "chosen_skill": chosen,
        "reason": "Matches condition.", "root_cause_explanation": "Synthetic path.",
    }))])
    monkeypatch.setattr(check_llm, "_get_llm", lambda: fake)
    report = check_llm.check()
    assert report["passed"] is passed
    assert report["remediation_executed"] is False
    assert report["usage"]["total_tokens"] == 42
    assert fake.calls == 1


def test_connection_check_does_not_print_provider_error_details(monkeypatch):
    fake = FakeLLM([RuntimeError("private-request-details")])
    monkeypatch.setattr(check_llm, "_get_llm", lambda: fake)
    report = check_llm.check()
    assert report["passed"] is False
    assert report["error_type"] == "RuntimeError"
    assert "private-request-details" not in json.dumps(report)
