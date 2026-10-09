from datetime import datetime, timezone
from unittest.mock import Mock

from core.health import SERVICES
from simulation import chaos_daemon as chaos


def healthy():
    return {name: {"healthy": True, "status": "HEALTHY"} for name in SERVICES}


def test_no_trials_has_no_zero_time_or_accuracy_claim():
    now = datetime.now(timezone.utc)
    data = chaos.campaign_record([], now, now)
    assert data["detection_rate_pct"] is None
    assert data["mean_mttr_s"] is None
    assert "unavailable" in chaos._summary([], 0, now, now)


def test_invalid_injection_remains_in_attempts_not_completed_commands():
    now = datetime.now(timezone.utc)
    invalid = chaos.Incident("config_drift", "redis-cart", now, reason="command failed")
    data = chaos.campaign_record([invalid], now, now)
    assert data["total_attempted"] == 1
    assert data["total_injected"] == 0
    assert data["incidents"][0]["status"] == "INVALID"


def test_baseline_failure_never_injects_or_resets(monkeypatch):
    from agent.nodes import evaluator
    monkeypatch.setattr(evaluator, "verify_incident", Mock(side_effect=RuntimeError("offline")))
    inject, reset = Mock(), Mock()
    monkeypatch.setitem(chaos.FAULTS, "config_drift", (inject, reset, None))
    incident = chaos.run_incident("config_drift", None, Mock())
    assert not incident.injection_succeeded
    inject.assert_not_called()
    reset.assert_not_called()


def test_partial_failed_injection_still_resets_and_verifies_cleanup(monkeypatch):
    from agent.nodes import evaluator
    from simulation import incident_tracking
    monkeypatch.setattr(evaluator, "verify_incident", lambda _: healthy())
    monkeypatch.setattr(incident_tracking, "collector_ready", lambda: True)
    inject, reset = Mock(side_effect=RuntimeError("partly applied")), Mock()
    monkeypatch.setitem(chaos.FAULTS, "config_drift", (inject, reset, None))
    incident = chaos.run_incident("config_drift", None, Mock())
    assert not incident.injection_succeeded
    assert incident.cleanup_verified
    reset.assert_called_once()


def test_legacy_resolved_report_cannot_count_as_independent_recovery(monkeypatch):
    from agent.nodes import evaluator
    from simulation import incident_tracking
    monkeypatch.setattr(evaluator, "verify_incident", lambda _: healthy())
    monkeypatch.setattr(incident_tracking, "collector_ready", lambda: True)
    event = {"alert_id": "INC-A", "condition": "CONFIG_DRIFT", "detected_at": datetime.now(timezone.utc).timestamp()}
    report = {"schema_version": 1, "root_cause_node": "redis-cart", "dependency_chain": ["redis-cart"],
              "skills_executed": [], "resolution_status": "RESOLVED", "all_services_healthy": True}
    monkeypatch.setattr(incident_tracking, "matching_incident", lambda *_: (event, report))
    monkeypatch.setitem(chaos.FAULTS, "config_drift", (Mock(), Mock(), None))
    monkeypatch.setattr(chaos, "_running", True)
    incident = chaos.run_incident("config_drift", None, Mock())
    assert incident.detected and incident.alert_id == "INC-A"
    assert not incident.resolved
    assert incident.cleanup_verified
