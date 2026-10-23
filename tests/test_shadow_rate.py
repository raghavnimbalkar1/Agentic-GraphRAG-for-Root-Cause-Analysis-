"""Deterministic fake-clock rate/deadline tests; no actual sleeping or API calls."""

from types import SimpleNamespace
import json

import pytest

from agent.shadow import ShadowRunner
from agent.shadow_protocol import ProviderFailure
from agent.shadow_provider import safe_provider_failure
from agent.shadow_rate import RollingTokenAdmission
from tests.test_structured_shadow import ModelPolicy, session
from tests.test_structured_shadow import source as source


def limiter():
    ticks, waits = [0.0], []
    def sleep(delay):
        ticks[0] += delay
        waits.append(delay)
    return RollingTokenAdmission(minimum_interval=0, clock=lambda: ticks[0], sleep=sleep), ticks, waits


def test_known_usage_settles_rate_reservation_without_refunding_incident_budget():
    rate, ticks, waits = limiter()
    first = rate.reserve(4000, remaining_seconds=180)
    rate.record_usage(first, 2000)
    assert rate.reserve(5500, remaining_seconds=180) is not None
    assert ticks[0] == 0 and waits == []
    assert rate.reserve(3000, remaining_seconds=180) is not None
    assert ticks[0] == 61


def test_unknown_usage_remains_reserved_and_cannot_force_an_overdue_call():
    rate, ticks, waits = limiter()
    first = rate.reserve(7000, remaining_seconds=180)
    rate.record_usage(first, None)
    assert rate.reserve(2000, remaining_seconds=20) is None
    assert ticks[0] == 0 and waits == []


@pytest.mark.parametrize("amount,remaining", [(8001, 180), (0, 180), (True, 180), (1000, 0)])
def test_invalid_or_oversized_rate_reservations_do_not_wait(amount, remaining):
    rate, _, waits = limiter()
    assert rate.reserve(amount, remaining_seconds=remaining) is None
    assert waits == []


def test_rate_wait_rechecks_incident_deadline_before_calling_provider(source):
    ticks = [0]
    broker = session(source)
    broker.clock = lambda: ticks[0]
    broker.started = 0
    class Delayed(ModelPolicy):
        calls = 0
        def wait_for_admission(self, **_):
            ticks[0] = 181
            return True
        def complete(self, *args, **kwargs):
            self.calls += 1
            return super().complete(*args, **kwargs)
    driver = Delayed()
    report = ShadowRunner(broker, source[2], driver, screening="dependency", context_profile="structured").run()
    assert driver.calls == 0 and report["provider_attempts"] == 0
    assert report["termination_reason"] == "time_exhausted"


def test_failed_admission_is_not_an_external_attempt_or_successful_model_abstention(source):
    class Refused(ModelPolicy):
        def wait_for_admission(self, **_):
            return False
    report = ShadowRunner(session(source), source[2], Refused(), screening="dependency", context_profile="structured").run()
    assert report["provider_attempts"] == 0 and not report["turns"]
    assert report["termination_reason"] == "provider_admission_exhausted"


@pytest.mark.parametrize("status,expected", [(400, "provider_bad_request"), (401, "provider_authentication_failed"),
    (403, "provider_access_denied"), (429, "provider_rate_limited"), (503, "provider_unavailable")])
def test_provider_failures_have_fixed_categories_and_never_retain_vendor_text(status, expected):
    failure = safe_provider_failure(SimpleNamespace(status_code=status, body={"error": {
        "code": "json_validate_failed", "param": "response_format", "message": "PRIVATE-KEY schema json",
        "failed_generation": '{"decision":{"action":"execute","command":"PRIVATE-KEY"}}'}}))
    assert isinstance(failure, ProviderFailure) and failure.category == expected
    assert "PRIVATE-KEY" not in str(failure) + json.dumps(failure.diagnostics)
    assert failure.diagnostics["code"] == "json_validate_failed"


def test_native_failure_report_retains_status_category_without_attempt_retry(source):
    class Failed(ModelPolicy):
        def complete(self, *_args, **_kwargs):
            raise ProviderFailure("provider_bad_request", {"http_status": 400, "code": "json_validate_failed"})
    report = ShadowRunner(session(source), source[2], Failed(), screening="dependency", context_profile="structured").run()
    assert report["termination_reason"] == "provider_bad_request" and not report["usage_complete"]
    assert report["attempts"][0]["provider_error"]["http_status"] == 400
    assert report["provider_attempts"] == 1 and report["known_reported_tokens"] == 0


@pytest.mark.parametrize("deadline,admitted", [(180, 3), (300, 4)])
def test_four_full_rate_windows_need_an_explicit_slow_provider_deadline(deadline, admitted):
    rate, ticks, _ = limiter()
    count = 0
    for _ in range(4):
        ticket = rate.reserve(8000, remaining_seconds=deadline - ticks[0])
        if ticket is None:
            break
        count += 1
        rate.record_usage(ticket, 8000)
    assert count == admitted
    assert ticks[0] <= deadline


def test_explicit_300_second_profile_does_not_change_legacy_defaults():
    from agent.investigation import InvestigationLimits
    from agent.shadow import ShadowLimits

    assert InvestigationLimits().max_seconds == ShadowLimits().max_seconds == 180
    assert InvestigationLimits(max_seconds=300).max_tool_calls == 8
    assert ShadowLimits(max_seconds=300).max_decisions == 4
    with pytest.raises(ValueError):
        ShadowLimits(max_seconds=361)
    with pytest.raises(ValueError):
        InvestigationLimits(max_seconds=361)
