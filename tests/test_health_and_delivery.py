import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from agent.incident_manager import IncidentConflict, IncidentManager
from core.health import Observation, cpu_percent, observe, grpc_readiness
from core.schemas import AlertPayload
from simulation.incident_delivery import IncidentDelivery


def container():
    result = Mock()
    result.status = "running"
    result.attrs = {"NetworkSettings": {"Networks": {"boutique-sim": {}}}}
    return result


def client_with(service):
    client = Mock()
    client.containers.get.return_value = service
    return client


def test_zero_cpu_is_a_valid_sample():
    assert cpu_percent({"cpu_stats": {"cpu_usage": {"total_usage": 10}, "system_cpu_usage": 100,
                                     "online_cpus": 2},
                        "precpu_stats": {"cpu_usage": {"total_usage": 10}, "system_cpu_usage": 50}}) == 0
    assert cpu_percent({}) is None


def test_graph_batch_keeps_measurement_time_and_reports_only_matched_nodes():
    from graph.graph_client import GraphClient
    client = object.__new__(GraphClient)
    client._run = Mock(return_value=[{"name": "redis-cart"}])
    observation = Observation("OOM_KILLED", "cap", measured_at="2026-10-10T00:00:00+00:00")
    assert client.update_service_observations({"redis-cart": observation}) == {"redis-cart"}
    record = client._run.call_args.kwargs["observations"][0]
    assert record["measured_at"] == observation.measured_at
    assert record["error_code"] == "OOM_KILLED"


def test_missing_disk_sample_cannot_be_healthy():
    assert observe(client_with(container()), "emailservice", sizes={}).status == "UNKNOWN"


def test_missing_memory_sample_cannot_be_healthy():
    service = container()
    service.stats.return_value = {}
    assert observe(client_with(service), "recommendationservice").status == "UNKNOWN"


def test_fast_http_error_is_unhealthy():
    http = Mock()
    http.get.return_value = httpx.Response(500)
    result = observe(client_with(container()), "frontend", http_client=http)
    assert result.status == "DEGRADED"
    assert not result.healthy


def test_running_grpc_container_requires_readiness():
    result = observe(client_with(container()), "cartservice", readiness={})
    assert result.status == "UNKNOWN"


def test_docker_outage_is_unknown():
    client = Mock()
    client.containers.get.side_effect = RuntimeError("outage")
    assert observe(client, "cartservice").status == "UNKNOWN"


@pytest.mark.parametrize("expires,expected", [(1000, "STALE_DATA"), (0, "HEALTHY")])
def test_stale_keyspace_is_verified_independently_of_capacity(expires, expected):
    service = container()
    outputs = {
        ("PING",): "PONG", ("CONFIG", "GET", "maxmemory"): "maxmemory\n268435456",
        ("CONFIG", "GET", "maxmemory-policy"): "maxmemory-policy\nallkeys-lru",
        ("INFO", "clients"): "# Clients\nconnected_clients:5",
        ("INFO", "keyspace"): f"# Keyspace\ndb0:keys=1000,expires={expires},avg_ttl=1000",
    }
    service.exec_run.side_effect = lambda args, **kwargs: SimpleNamespace(
        exit_code=0, output=outputs[tuple(args[2:])].encode())
    assert observe(client_with(service), "redis-cart").status == expected


def test_failed_probe_container_is_removed():
    client = Mock()
    probe = client.containers.run.return_value
    probe.wait.side_effect = TimeoutError()
    result = grpc_readiness(client, ["cartservice"])
    assert result["cartservice"].status == "UNKNOWN"
    probe.remove.assert_called_once_with(force=True)
    assert "volumes" not in client.containers.run.call_args.kwargs


def test_missing_second_observation_cannot_reuse_healthy_evidence(monkeypatch):
    from agent.nodes import evaluator
    client = Mock()
    monkeypatch.setattr(evaluator.docker, "DockerClient", lambda **kwargs: client)
    monkeypatch.setattr("core.health.observe_many", Mock(side_effect=[
        {"frontend": Observation("HEALTHY", "ready")}, {},
    ]))
    monkeypatch.setattr(evaluator.time, "monotonic", Mock(side_effect=[0, 0, 30]))
    monkeypatch.setattr(evaluator.time, "sleep", lambda _: None)
    result = evaluator.verify_incident(["frontend"])
    assert not result["frontend"]["healthy"]
    assert result["frontend"]["status"] == "UNKNOWN"
    client.close.assert_called_once()


def episode(tmp_path):
    delivery = IncidentDelivery(tmp_path / "episodes.json", "http://agent/alert")
    observation = Observation("OOM_KILLED", "small cap")
    delivery.observe("redis-cart", observation)
    delivery.observe("redis-cart", observation)
    return delivery


def test_failed_delivery_is_pending_and_retries_same_id(tmp_path):
    delivery = episode(tmp_path)
    identifier = delivery.episodes["redis-cart"]["payload"]["alert_id"]
    failure = httpx.Response(500, request=httpx.Request("POST", "http://agent/alert"))
    delivery.send_one("redis-cart", post=lambda *args, **kwargs: failure)
    assert delivery.episodes["redis-cart"]["state"] == "pending"
    restored = IncidentDelivery(delivery.path, delivery.endpoint)
    assert restored.episodes["redis-cart"]["payload"]["alert_id"] == identifier
    success = httpx.Response(200, json={"alert_id": identifier, "status": "ESCALATED"})
    success.request = httpx.Request("POST", "http://agent/alert")
    restored.send_one("redis-cart", post=lambda *args, **kwargs: success)
    assert restored.episodes["redis-cart"]["state"] == "acknowledged"


def test_unrelated_report_is_not_an_acknowledgment(tmp_path):
    delivery = episode(tmp_path)
    response = httpx.Response(200, json={"alert_id": "INC-OTHER", "status": "RESOLVED"},
                              request=httpx.Request("POST", "http://agent/alert"))
    delivery.send_one("redis-cart", post=lambda *args, **kwargs: response)
    assert delivery.episodes["redis-cart"]["state"] == "pending"


def test_unknown_observation_does_not_rearm_a_fault(tmp_path):
    delivery = episode(tmp_path)
    identifier = delivery.episodes["redis-cart"]["payload"]["alert_id"]
    delivery.observe("redis-cart", Observation("UNKNOWN", "missing sample"))
    assert delivery.episodes["redis-cart"]["payload"]["alert_id"] == identifier
    delivery.observe("redis-cart", Observation("HEALTHY", "normal"))
    assert delivery.episodes == {}


def test_collector_identity_survives_healthy_rearm(tmp_path):
    from simulation.incident_tracking import collector_events
    delivery = episode(tmp_path)
    identifier = delivery.episodes["redis-cart"]["payload"]["alert_id"]
    delivery.observe("redis-cart", Observation("HEALTHY", "normal"))
    events = collector_events("redis-cart", 0, tmp_path)
    assert [event["alert_id"] for event in events] == [identifier]
    assert collector_events("frontend", 0, tmp_path) == []


def test_stale_or_unsynced_collector_is_not_ready(tmp_path):
    import json
    from simulation.incident_tracking import collector_ready
    path = tmp_path / "collector_status.json"
    path.write_text(json.dumps({"timestamp": 0, "graph_synced": True}))
    assert not collector_ready(tmp_path)


@pytest.mark.asyncio
async def test_duplicate_requests_execute_once(tmp_path, monkeypatch):
    import agent.incident_manager as module
    monkeypatch.setattr(module, "read_report", lambda identifier: None)
    manager = IncidentManager(tmp_path / "incidents.sqlite")
    alert = AlertPayload(service="frontend", error_type="DEGRADED", message="failure")
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def run(payload):
        calls.append(payload.alert_id)
        started.set()
        await release.wait()
        return "report"

    first = asyncio.create_task(manager.submit(alert, run))
    await started.wait()
    second = asyncio.create_task(manager.submit(alert, run))
    release.set()
    assert await first == await second == "report"
    assert calls == [alert.alert_id]


@pytest.mark.asyncio
async def test_incidents_are_serialized(tmp_path, monkeypatch):
    import agent.incident_manager as module
    monkeypatch.setattr(module, "read_report", lambda identifier: None)
    manager = IncidentManager(tmp_path / "incidents.sqlite")
    active = 0
    peak = 0

    async def run(payload):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    alerts = [AlertPayload(service="frontend", error_type="DEGRADED", message="failure") for _ in range(3)]
    await asyncio.gather(*(manager.submit(alert, run) for alert in alerts))
    assert peak == 1


@pytest.mark.asyncio
async def test_interrupted_incident_requires_review_and_id_collision_is_rejected(tmp_path, monkeypatch):
    import agent.incident_manager as module
    monkeypatch.setattr(module, "read_report", lambda identifier: None)
    path = tmp_path / "incidents.sqlite"
    first = IncidentManager(path)
    alert = AlertPayload(service="frontend", error_type="DEGRADED", message="failure")
    first._claim(alert)
    restarted = IncidentManager(path)
    with pytest.raises(IncidentConflict, match="Interrupted"):
        await restarted.submit(alert, Mock())
    changed = alert.model_copy(update={"message": "different failure"})
    with pytest.raises(IncidentConflict, match="different alert"):
        await restarted.submit(changed, Mock())


@pytest.mark.asyncio
async def test_unregistered_historical_report_never_becomes_a_cached_acknowledgment(tmp_path, monkeypatch):
    import agent.incident_manager as module
    monkeypatch.setattr(module, "read_report", lambda identifier: object())
    manager = IncidentManager(tmp_path / "incidents.sqlite")
    alert = AlertPayload(service="frontend", error_type="DEGRADED", message="failure")
    runner = Mock()
    for _ in range(2):
        with pytest.raises(IncidentConflict, match="no registered"):
            await manager.submit(alert, runner)
    runner.assert_not_called()
