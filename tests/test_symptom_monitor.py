"""Offline HTTP and monitor checks; no Docker, provider or execution requests."""

import json
from unittest.mock import Mock

import httpx
import pytest

from core.audit import write_json_atomic
from core.registry import DEFAULT_DEPLOYMENT
from simulation import application_canary as canary
from simulation.symptom_monitor import SymptomMonitor, interpret_canary


def transport(*, checkout_status=200, redirect=None):
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path == "/":
            return httpx.Response(200, text="storefront", headers={"set-cookie": "session-id=private; Path=/"})
        if request.method == "POST" and path in {"/cart", "/cart/empty"}:
            location = redirect or ("/cart" if path == "/cart" else "/")
            return httpx.Response(302, headers={"location": location})
        if path == "/cart":
            return httpx.Response(200, text=f"{canary.PRODUCT} Cart (1)")
        if path == "/cart/checkout":
            return httpx.Response(checkout_status, text="Your order is complete!")
        pytest.fail(f"Unexpected canary request: {request.url}")

    return httpx.MockTransport(handle), requests


def healthy():
    adapter, _ = transport()
    return canary.check_application(transport=adapter)


def test_successful_workflow_follows_only_local_redirects_and_cleans_session():
    adapter, requests = transport()
    result = canary.check_application(transport=adapter)
    assert result["success"]
    assert interpret_canary(result)[0] == "healthy"
    assert len(requests) == 7
    assert [r.url.path for r in requests][-2:] == ["/cart/empty", "/"]
    assert all(r.url.host == "127.0.0.1" and r.url.port == 8080 for r in requests)
    assert "private" not in json.dumps(result)
    assert "credit_card" not in json.dumps(result)


@pytest.mark.parametrize("destination", [
    "https://example.com/", "http://127.0.0.1:8888/alert", "/unapproved", "/?secret=1",
    "http://user:password@127.0.0.1:8080/",
])
def test_unsafe_redirect_never_followed(destination):
    adapter, requests = transport(redirect=destination)
    result = canary.check_application(transport=adapter)
    assert not result["success"]
    assert interpret_canary(result)[0] == "unknown"
    assert all(r.url.host == "127.0.0.1" and r.url.port == 8080 for r in requests)
    assert all(r.url.path in canary.ALLOWED_PATHS and not r.url.query for r in requests)
    assert "UnsafeRedirect" in {step.get("error") for step in result["steps"]}


def test_real_http_failure_is_symptom_not_root():
    adapter, _ = transport(checkout_status=500)
    result = canary.check_application(transport=adapter)
    assert interpret_canary(result)[0] == "failed"
    assert result["steps"][3]["status_code"] == 500
    assert result["steps"][-1]["step"] == "cleanup_cart"


def test_timeout_observed_but_probe_payload_limit_is_unknown():
    def timeout(request):
        raise httpx.ReadTimeout("private transport detail")

    result = canary.check_application(transport=httpx.MockTransport(timeout))
    assert interpret_canary(result)[0] == "failed"
    assert "private" not in json.dumps(result)
    large = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (canary.MAX_RESPONSE_BYTES + 1)))
    result = canary.check_application(transport=large)
    assert interpret_canary(result)[0] == "unknown"
    assert result["steps"][0]["error"] == "ResponseTooLarge"


def test_probe_budget_exhaustion_is_unknown(monkeypatch):
    now = Mock(return_value=0.0)
    monkeypatch.setattr(canary.time, "perf_counter", now)

    def late(request):
        now.return_value = 31
        return httpx.Response(200, text="late")

    result = canary.check_application(transport=httpx.MockTransport(late), deadline_seconds=1)
    assert result["steps"][0]["error"] == "ProbeBudgetExceeded"
    assert interpret_canary(result)[0] == "unknown"


def test_cleanup_failure_or_missing_session_cannot_be_healthy():
    result = healthy()
    result["steps"][-1].update(success=False, status_code=500)
    assert interpret_canary(result)[0] == "unknown"
    result["steps"].pop()
    assert interpret_canary(result)[0] == "unknown"


def test_monitor_persists_provenance_without_execution_fields(tmp_path):
    monitor = SymptomMonitor(tmp_path, canary=healthy)
    record = monitor.poll_once()
    assert record.outcome == "healthy"
    rows = monitor.store.window(DEFAULT_DEPLOYMENT.environment_id, DEFAULT_DEPLOYMENT.digest)
    assert len(rows) == 1 and rows[0]["service_id"] == "frontend"
    assert rows[0]["registry_revision"] == DEFAULT_DEPLOYMENT.revision
    assert "root_cause_node" not in rows[0]
    state = json.loads((tmp_path / "symptom_monitor_status.json").read_text())
    assert state["mode"] == "shadow" and state["delivery_enabled"] is False
    assert state["state"] == "ready" and state["active_incident_id"] is None


def test_monitor_blocks_transaction_when_component_delivery_enabled(tmp_path):
    write_json_atomic(tmp_path / "collector_status.json", {"delivery_enabled": True})
    probe = Mock(side_effect=AssertionError("Must not probe"))
    monitor = SymptomMonitor(tmp_path, canary=probe)
    assert monitor.poll_once().outcome == "unknown"
    probe.assert_not_called()
    assert monitor.state["blocked_reason"] == "automatic_component_delivery_enabled"
    assert monitor.state["active_incident_id"] is None


def test_monotonic_schedule_has_no_catchup_burst(tmp_path):
    clock = Mock(return_value=0.0)
    probe = Mock(side_effect=healthy)
    monitor = SymptomMonitor(tmp_path, canary=probe, clock=clock)
    monitor.poll_once()
    assert monitor.next_due == 30
    assert monitor.poll_once() is None
    clock.return_value = 1000
    monitor.poll_once()
    assert monitor.next_due == 1030 and probe.call_count == 2
    assert monitor.poll_once() is None
    monitor.stop.set()
    clock.return_value = 2000
    assert monitor.poll_once() is None


@pytest.mark.parametrize("bad", [{}, {"steps": [{"step": "unapproved"}]},
                                 {"steps": [{"step": "checkout", "success": False, "latency_s": 1, "status_code": 500}]},
                                 {"steps": [{"step": "home", "success": True, "latency_s": 1, "status_code": 500}]}])
def test_bad_probe_output_is_unknown_not_false_incident(tmp_path, bad):
    monitor = SymptomMonitor(tmp_path, canary=lambda: bad)
    assert monitor.poll_once().outcome == "unknown"
    assert monitor.state["state"] == "degraded"
    assert monitor.state["active_incident_id"] is None


def test_write_failure_does_not_spin_and_recovery_clears_error(tmp_path):
    clock = Mock(return_value=0.0)
    store = Mock()
    store.record_symptom.side_effect = OSError("private error")
    monitor = SymptomMonitor(tmp_path, canary=healthy, store=store, clock=clock)
    with pytest.raises(OSError):
        monitor.poll_once()
    assert monitor.next_due == 30
    assert monitor.poll_once() is None
    assert monitor.state["evidence_store"] == "unavailable"
    assert "private" not in json.dumps(monitor.state)
    clock.return_value = 30
    store.record_symptom.side_effect = None
    store.record_symptom.return_value = {"active_id": None}
    monitor.poll_once()
    assert monitor.state["evidence_store"] == "ready" and "error_kind" not in monitor.state


def test_early_status_write_failure_is_also_rate_limited(tmp_path, monkeypatch):
    monitor = SymptomMonitor(tmp_path, clock=lambda: 10)
    monkeypatch.setattr(monitor, "publish", Mock(side_effect=OSError()))
    with pytest.raises(OSError):
        monitor.poll_once()
    assert monitor.next_due == 40
    assert monitor.poll_once() is None


def test_once_stops_and_records_exit_without_delivery(tmp_path):
    monitor = SymptomMonitor(tmp_path, canary=healthy)
    monitor.run(once=True)
    assert monitor.state["state"] == "stopped"
    assert monitor.state["delivery_enabled"] is False
