"""No Docker, Neo4j or model requests: exercise collector lifecycle with adapters."""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from core.health import SERVICES, Observation
from core.telemetry import age_seconds, collector_status, fresh_service_statuses
from simulation.incident_delivery import IncidentDelivery
from simulation.incident_tracking import collector_ready
from simulation.telemetry_collector import TelemetryCollector, collector_lock


def samples(condition="HEALTHY"):
    return {name: Observation(condition if name == "redis-cart" else "HEALTHY", "probe",
                              instance_id=f"instance-{name}") for name in SERVICES}


@pytest.fixture
def runtime(tmp_path):
    client = Mock()
    client.ping.return_value = True
    graph = Mock()
    graph.update_service_observations.return_value = set(SERVICES)
    graph.get_independent_roots.return_value = []
    clock = Mock(return_value=0.0)
    collector = TelemetryCollector(tmp_path, docker_factory=Mock(return_value=client),
                                   graph_factory=Mock(return_value=graph),
                                   observer=Mock(side_effect=lambda *a, **k: samples()), clock=clock)
    collector.delivery.dispatch = Mock()
    yield collector, client, graph, clock
    collector.close()


def status(collector):
    return json.loads((collector.directory / "collector_status.json").read_text())


def test_docker_startup_outage_publishes_unknown_then_recovers(runtime):
    collector, client, _, clock = runtime
    collector.docker_factory.side_effect = [RuntimeError("private connection detail"), client]
    collector.poll_once()
    failed = status(collector)
    assert failed["state"] == "degraded"
    assert failed["dependencies"]["docker"]["retry_in_seconds"] == 5
    assert all(row["status"] == "UNKNOWN" for row in failed["observations"].values())
    assert "private connection detail" not in json.dumps(failed)
    assert collector_status(collector.directory)["heartbeat_fresh"]
    assert not collector_ready(collector.directory)
    collector.poll_once()
    assert collector.docker_factory.call_count == 1
    clock.return_value = 5
    collector.poll_once()
    assert collector_ready(collector.directory)
    assert status(collector)["dependencies"]["docker"]["state"] == "connected"
    collector.delivery.dispatch.assert_not_called()


def test_docker_disconnect_closes_client_and_reconnects(runtime):
    collector, client, _, clock = runtime
    replacement = Mock()
    replacement.ping.return_value = True
    collector.docker_factory.side_effect = [client, replacement]
    client.ping.side_effect = [True, RuntimeError("paused engine")]
    collector.poll_once()
    collector.poll_once()
    client.close.assert_called_once()
    assert not collector_ready(collector.directory)
    clock.return_value = 5
    collector.poll_once()
    assert collector_ready(collector.directory)
    assert collector.clients["docker"] is replacement


def test_retry_delay_is_bounded_and_uses_monotonic_time(runtime):
    collector, _, _, clock = runtime
    collector.docker_factory.side_effect = ConnectionError()
    now = 0
    for expected in [5, 10, 20, 40, 60, 60]:
        clock.return_value = now
        collector.poll_once()
        assert status(collector)["dependencies"]["docker"]["retry_in_seconds"] == expected
        now += expected
    assert collector.docker_factory.call_count == 6


def test_graph_outage_suppresses_delivery_and_preserves_episode_id(runtime):
    collector, _, graph, clock = runtime
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.poll_once()
    identifier = collector.delivery.episodes["redis-cart"]["payload"]["alert_id"]
    collector.delivery.dispatch.reset_mock()
    graph.update_service_observations.side_effect = RuntimeError("graph outage")
    collector.poll_once()
    assert not collector_ready(collector.directory)
    graph.close.assert_called_once()
    collector.delivery.dispatch.assert_not_called()
    graph.update_service_observations.side_effect = None
    clock.return_value = 5
    collector.poll_once()
    assert collector_ready(collector.directory)
    assert collector.delivery.episodes["redis-cart"]["payload"]["alert_id"] == identifier
    collector.delivery.dispatch.assert_called_once_with(eligible_services={"redis-cart"})


@pytest.mark.parametrize("failure", ["missing_service", "unknown", "stale", "future", "invalid_status", "partial_graph"])
def test_incomplete_or_invalid_evidence_cannot_dispatch(runtime, failure):
    collector, _, graph, _ = runtime
    data = samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    if failure == "missing_service":
        data.pop("frontend")
    elif failure == "unknown":
        data["frontend"].status = "UNKNOWN"
    elif failure == "invalid_status":
        data["frontend"].status = "made-up"
    elif failure == "partial_graph":
        graph.update_service_observations.return_value = {"redis-cart"}
    else:
        delta = timedelta(seconds=-600 if failure == "stale" else 600)
        data["frontend"].measured_at = (datetime.now(timezone.utc) + delta).isoformat()
    collector.observer.side_effect = lambda *a, **k: data
    collector.poll_once()
    collector.poll_once()
    assert not collector_ready(collector.directory)
    assert collector.delivery.episodes == {}
    collector.delivery.dispatch.assert_not_called()


def test_observe_only_records_evidence_but_never_dispatches(runtime):
    collector, _, graph, _ = runtime
    collector.deliver_alerts = False
    collector.state["delivery_enabled"] = False
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.poll_once()
    payload = collector.delivery.episodes["redis-cart"]["payload"]
    evidence = payload["metadata"]["observation"]
    event = json.loads((collector.directory / "collector_events.json").read_text())[0]
    assert evidence["service_id"] == "redis-cart"
    assert evidence["instance_id"] == "instance-redis-cart"
    assert evidence["source"] == "builtin-health-probe"
    assert evidence["schema_version"] == 1
    assert event["evidence_id"] == evidence["evidence_id"]
    assert event["observed_at"] == evidence["measured_at"]
    assert collector_ready(collector.directory, require_delivery=False)
    assert not collector_ready(collector.directory)
    collector.delivery.dispatch.assert_not_called()


def test_restart_keeps_pending_id_and_recovery_rearms(runtime):
    collector, _, graph, _ = runtime
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.poll_once()
    original = collector.delivery.episodes["redis-cart"]["payload"]["alert_id"]
    collector.delivery = IncidentDelivery(collector.delivery.path, collector.delivery.endpoint)
    collector.delivery.dispatch = Mock()
    collector.poll_once()
    assert collector.delivery.episodes["redis-cart"]["payload"]["alert_id"] == original
    collector.observer.side_effect = lambda *a, **k: samples()
    graph.get_independent_roots.return_value = []
    collector.poll_once()
    assert not collector.delivery.episodes
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.poll_once()
    assert collector.delivery.episodes["redis-cart"]["payload"]["alert_id"] != original


def test_changed_condition_does_not_resend_previous_fault(runtime):
    collector, _, graph, _ = runtime
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.poll_once()
    collector.observer.side_effect = lambda *a, **k: samples("CONFIG_DRIFT")
    collector.delivery.dispatch.reset_mock()
    collector.poll_once()
    assert collector.delivery.episodes["redis-cart"]["state"] == "attention_required"
    assert collector.delivery.episodes["redis-cart"]["error"] == "condition_changed"
    collector.delivery.dispatch.assert_not_called()


def test_evidence_expiring_during_graph_sync_cannot_dispatch(runtime, monkeypatch):
    collector, _, graph, _ = runtime
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.poll_once()
    collector.delivery.dispatch.reset_mock()
    age = Mock(return_value=0)
    monkeypatch.setattr("simulation.telemetry_collector.age_seconds", age)

    def slow_sync(_):
        age.return_value = 600
        return set(SERVICES)

    graph.update_service_observations.side_effect = slow_sync
    collector.poll_once()
    assert status(collector)["state"] == "degraded"
    assert not collector_ready(collector.directory)
    collector.delivery.dispatch.assert_not_called()


def test_delivery_dispatch_filters_pending_episodes(tmp_path, monkeypatch):
    delivery = IncidentDelivery(tmp_path / "episodes.json", "http://unused")
    for _ in range(2):
        delivery.observe("redis-cart", Observation("OOM_KILLED", "cap"))
    thread = Mock()
    monkeypatch.setattr("simulation.incident_delivery.threading.Thread", thread)
    delivery.dispatch(eligible_services={"adservice"})
    thread.assert_not_called()


def test_stop_during_poll_does_not_start_an_alert(runtime):
    collector, _, graph, _ = runtime
    collector.observer.side_effect = lambda *a, **k: samples("OOM_KILLED")
    graph.get_independent_roots.return_value = [{"name": "redis-cart"}]
    collector.poll_once()
    collector.stop.set()
    collector.poll_once()
    collector.delivery.dispatch.assert_not_called()


def test_single_poll_closes_clients_and_records_stopped(runtime):
    collector, client, graph, _ = runtime
    collector.run(once=True)
    assert status(collector)["state"] == "stopped"
    assert collector_status(collector.directory)["state"] == "stopped"
    assert not collector_ready(collector.directory)
    client.close.assert_called_once()
    graph.close.assert_called_once()


def test_second_collector_cannot_overwrite_owner_status(tmp_path):
    with pytest.raises(ValueError, match="owner exit"):
        with collector_lock(tmp_path):
            with pytest.raises(RuntimeError, match="already owns"):
                with collector_lock(tmp_path):
                    pytest.fail("Second writer acquired the lock")
            raise ValueError("owner exit")
    with collector_lock(tmp_path):
        pass


def test_heartbeat_does_not_refresh_old_measurements(runtime):
    collector, _, _, _ = runtime
    collector.poll_once()
    before = status(collector)["observations"]
    collector.state["cycle_completed_at"] = time.time() - 600
    collector._publish("polling")
    assert status(collector)["observations"] == before
    result = collector_status(collector.directory)
    assert result["heartbeat_fresh"]
    assert not result["sensing_ready"]


@pytest.mark.parametrize("timestamp", [None, True, float("nan"), float("inf"),
                                      "not-a-time", "2026-10-15T00:00:00", 201])
def test_invalid_or_future_timestamp_is_not_fresh(timestamp):
    assert age_seconds(timestamp, now=200) is None


def test_stale_health_is_not_a_current_fault_or_healthy_baseline():
    result = fresh_service_statuses({
        "fresh": {"status": "HEALTHY", "age_seconds": 0},
        "old": {"status": "HEALTHY", "age_seconds": 61},
        "expired_fault": {"status": "HIGH_CPU", "age_seconds": 61},
        "untimed": {"status": "HEALTHY", "age_seconds": None},
        "future": {"status": "HEALTHY", "age_seconds": -1},
        "not_finite": {"status": "HEALTHY", "age_seconds": float("nan")},
        "invalid_row": None,
    }, max_age=60)
    assert result == {"fresh": "HEALTHY", "old": "STALE", "expired_fault": "STALE",
                      "untimed": "UNKNOWN", "future": "UNKNOWN", "not_finite": "UNKNOWN",
                      "invalid_row": "UNKNOWN"}


@pytest.mark.parametrize("payload", [[], None, {"schema_version": 3},
                                     {"timestamp": "bad"}, {"timestamp": True}])
def test_malformed_status_is_not_ready(tmp_path, payload):
    (tmp_path / "collector_status.json").write_text(json.dumps(payload))
    assert not collector_ready(tmp_path)
