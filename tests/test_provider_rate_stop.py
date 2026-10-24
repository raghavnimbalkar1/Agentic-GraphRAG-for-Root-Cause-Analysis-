"""Mocked HTTP and synthetic state only; no real provider calls or sleeping."""

import json
from types import SimpleNamespace

import httpx
import pytest

from agent.shadow import ShadowRunner
from agent.shadow_protocol import ProviderFailure
from agent.shadow_provider import GroqShadowDriver, safe_provider_failure
from eval.shadow_controls import assess_control
from tests.test_structured_shadow import session, source as source


def error(headers, status=429):
    return SimpleNamespace(status_code=status, response=SimpleNamespace(headers=headers),
        body={"error": {"code": "rate_limit_exceeded", "message": "PRIVATE-MESSAGE token limit"}})


def test_rate_metadata_uses_only_allowlisted_numeric_and_duration_headers():
    failure = safe_provider_failure(error(httpx.Headers({
        "retry-after": "12.5", "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "998", "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-requests": "2m59.56s",
        "x-ratelimit-reset-tokens": "7.66s", "authorization": "PRIVATE-KEY",
        "x-request-id": "PRIVATE-REQUEST-ID", "set-cookie": "PRIVATE-COOKIE"})))
    assert failure.diagnostics["rate_limits"] == {"retry_after_seconds": 12.5,
        "request_limit_per_day": 1000, "requests_remaining_per_day": 998,
        "token_limit_per_minute": 8000, "tokens_remaining_per_minute": 0,
        "request_reset_duration": "2m59.56s", "token_reset_duration": "7.66s"}
    assert "PRIVATE" not in json.dumps(failure.diagnostics)


@pytest.mark.parametrize("value", ["PRIVATE-KEY", "-1", "NaN", "Infinity", "1e9", "999999", "1" * 1000, True, None])
def test_invalid_retry_header_is_omitted_not_echoed(value):
    failure = safe_provider_failure(error({"retry-after": value}))
    assert "rate_limits" not in failure.diagnostics


@pytest.mark.parametrize("value", ["PRIVATE-KEY", "-1", "1.2", "1e3", "1" * 1000, True, None])
def test_invalid_count_header_is_omitted(value):
    failure = safe_provider_failure(error({"x-ratelimit-remaining-tokens": value}))
    assert "rate_limits" not in failure.diagnostics


@pytest.mark.parametrize("value", ["PRIVATE-KEY", "tomorrow", "1h;run", "-3s", "1e3s", "1" * 1000, True, None])
def test_invalid_reset_header_is_omitted(value):
    failure = safe_provider_failure(error({"x-ratelimit-reset-tokens": value}))
    assert "rate_limits" not in failure.diagnostics


def test_absent_or_non_mapping_headers_are_backward_compatible():
    assert "rate_limits" not in safe_provider_failure(error(None)).diagnostics
    assert "rate_limits" not in safe_provider_failure(error("PRIVATE-KEY")).diagnostics
    assert "rate_limits" not in safe_provider_failure(error({"retry-after": "10"}, status=503)).diagnostics


def test_one_mocked_429_stops_later_shared_driver_sessions_without_another_sdk_attempt(source):
    from langchain_openai import ChatOpenAI

    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(429, headers={"retry-after": "60", "x-ratelimit-remaining-tokens": "0"},
            json={"error": {"message": "PRIVATE-MESSAGE token quota", "code": "rate_limit_exceeded"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ChatOpenAI(model="openai/gpt-oss-120b", api_key="test", max_retries=0,
            base_url="https://api.groq.com/openai/v1", http_client=client)
        driver = GroqShadowDriver(model, structured=True)
        class Wrapper:
            kind = "model"
            structured_decisions = True
            calls = 0

            def estimate_input_tokens(self, messages):
                return driver.estimate_input_tokens(messages)

            def wait_for_admission(self, **kwargs):
                return driver.wait_for_admission(**kwargs)

            def complete(self, messages, **kwargs):
                self.calls += 1
                return driver.complete(messages, **kwargs)

        wrapped = Wrapper()
        first = ShadowRunner(session(source), source[2], wrapped, screening="dependency", context_profile="structured").run()
        assert first["attempts"][0]["provider_error"]["rate_limits"]["retry_after_seconds"] == 60
        first["attempts"][0]["provider_error"]["rate_limits"]["retry_after_seconds"] = 0
        second = ShadowRunner(session(source), source[2], wrapped, screening="dependency", context_profile="structured").run()
        assert len(captured) == 1
        assert wrapped.calls == 1
        assert first["provider_attempts"] == 1 and not first["usage_complete"]
        assert second["provider_attempts"] == 0 and second["session"]["calls_used"] == 0
        assert second["termination_reason"] == "provider_rate_limited"
        assert second["blocked_request"]["provider_error"]["shared_driver_latched"]
        assert second["blocked_request"]["provider_error"]["rate_limits"]["retry_after_seconds"] == 60
        assert not first["candidate_screened"] and not second["candidate_screened"]
        assert "PRIVATE" not in json.dumps(first) + json.dumps(second)
        with pytest.raises(ProviderFailure) as direct:
            driver.complete([], max_output_tokens=100, timeout_seconds=1)
        assert direct.value.diagnostics["shared_driver_latched"] and len(captured) == 1
    score = assess_control(second, expected={"outcome": "abstained", "reason": "insufficient_evidence"},
                           clue_service="paymentservice")
    assert not score["control_passed"] and not score["explicit_policy_terminal"]


def test_latched_metadata_is_copied_and_admission_never_reserves_or_sleeps():
    model = SimpleNamespace(max_retries=0)
    driver = GroqShadowDriver(model)
    original = safe_provider_failure(error({"retry-after": "10"})).diagnostics
    driver._rate_stop = json.loads(json.dumps(original))
    for _ in range(2):
        with pytest.raises(ProviderFailure) as caught:
            driver.wait_for_admission(estimated_input_tokens=100, output_tokens=10, remaining_seconds=180)
        assert caught.value.diagnostics["rate_limits"]["retry_after_seconds"] == 10
        caught.value.diagnostics["rate_limits"]["retry_after_seconds"] = 0
    assert driver._ticket is None and original["rate_limits"]["retry_after_seconds"] == 10


@pytest.mark.parametrize("status", [400, 401, 403, 503])
def test_other_failure_categories_do_not_get_a_rate_limit_latch(status):
    class Failure(RuntimeError):
        status_code = status
        body = {"error": {"message": "PRIVATE-ERROR", "code": "unclassified"}}

    class Model:
        max_retries = 0

        def bind(self, **kwargs):
            return self

        def invoke(self, messages):
            raise Failure("PRIVATE-ERROR")

    driver = GroqShadowDriver(Model())
    with pytest.raises(ProviderFailure):
        driver.complete([], max_output_tokens=100, timeout_seconds=1)
    assert driver._rate_stop is None
