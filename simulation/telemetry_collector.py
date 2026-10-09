"""Observe real service health, synchronize Neo4j, and deliver stable incidents."""

import signal
import time

import docker
import httpx

from core import get_logger, settings, setup_logging
from core.audit import write_json_atomic
from core.health import SERVICES, observe, observe_many
from graph.graph_client import GraphClient
from simulation.incident_delivery import IncidentDelivery

log = get_logger(__name__)
POLL_INTERVAL_S = 5
ALERT_ENDPOINT = f"http://localhost:{settings.alert_listen_port}/alert"
_running = True


def check_service(client, name: str) -> tuple[str, str]:
    observation = observe(client, name)
    return observation.status, observation.detail


def _stop(_signal, _frame):
    global _running
    _running = False


def run() -> None:
    setup_logging()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    delivery = IncidentDelivery(settings.audit_dir / "collector_episodes.json", ALERT_ENDPOINT)
    client = docker.DockerClient(base_url=settings.docker_host, timeout=10)
    http_client = httpx.Client()
    try:
        while _running:
            start = time.monotonic()
            observations = observe_many(client, SERVICES, http_client=http_client)
            synced = set()
            roots = set()
            try:
                gc = GraphClient()
                for name, observation in observations.items():
                    gc.update_service_status(name, observation.status,
                                             None if observation.healthy else observation.status)
                    synced.add(name)
                roots = {r["name"] for r in gc.get_independent_roots()}
            except Exception as exc:
                log.error("telemetry_graph_sync_failed", error=str(exc))
            for name, observation in observations.items():
                delivery.observe(name, observation, eligible=name in synced and name in roots)
            if roots:
                delivery.dispatch()
            with delivery.lock:
                episodes = {name: {"alert_id": episode["payload"]["alert_id"], "state": episode["state"]}
                            for name, episode in delivery.episodes.items()}
            write_json_atomic(settings.audit_dir / "collector_status.json", {
                "timestamp": time.time(), "poll_seconds": time.monotonic() - start,
                "graph_synced": len(synced) == len(SERVICES),
                "observations": {name: value.to_dict() for name, value in observations.items()},
                "episodes": episodes,
            })
            time.sleep(max(0, POLL_INTERVAL_S - (time.monotonic() - start)))
    finally:
        http_client.close()
        client.close()


if __name__ == "__main__":
    run()
