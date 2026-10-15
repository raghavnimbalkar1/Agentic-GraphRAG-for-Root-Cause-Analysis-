"""Observe service health with reconnects, fresh evidence and stable incident IDs."""

import argparse
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import docker
import httpx

from core import get_logger, settings, setup_logging
from core.audit import write_json_atomic
from core.health import SERVICES, Observation, observe, observe_many
from core.schemas import ObservationRecord
from core.telemetry import age_seconds
from graph.graph_client import GraphClient
from simulation.incident_delivery import IncidentDelivery

log = get_logger(__name__)
POLL_INTERVAL_S = 5
MAX_RETRY_S = 60
ALERT_ENDPOINT = f"http://localhost:{settings.alert_listen_port}/alert"


def check_service(client, name: str) -> tuple[str, str]:
    observation = observe(client, name)
    return observation.status, observation.detail


@contextmanager
def collector_lock(directory: Path):
    """One collector per audit directory on the supported macOS/Linux hosts."""
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "collector.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A collector already owns this audit directory") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class TelemetryCollector:
    """A restartable polling lifecycle; dependency failures never become service faults."""

    def __init__(self, directory: Path, deliver_alerts: bool = True, *,
                 docker_factory=None, graph_factory=None, observer=None, clock=None):
        self.directory = directory
        self.deliver_alerts = deliver_alerts
        self.delivery = IncidentDelivery(directory / "collector_episodes.json", ALERT_ENDPOINT)
        self.docker_factory = docker_factory or (
            lambda: docker.DockerClient(base_url=settings.docker_host, timeout=10))
        self.graph_factory = graph_factory or GraphClient
        self.observer = observer or observe_many
        self.clock = clock or time.monotonic
        self.http = httpx.Client()
        self.stop = threading.Event()
        self.clients = {"docker": None, "neo4j": None}
        self.retry_at = {name: 0.0 for name in self.clients}
        self.failures = {name: 0 for name in self.clients}
        self.dependencies = {name: {"state": "unknown"} for name in self.clients}
        self.state = {
            "schema_version": 2, "run_id": uuid4().hex, "pid": os.getpid(),
            "delivery_enabled": deliver_alerts, "graph_synced": False,
            "cycle_completed_at": None, "poll_seconds": None, "observations": {},
        }

    def _connected(self, name):
        self.failures[name] = 0
        self.retry_at[name] = 0.0
        self.dependencies[name] = {"state": "connected", "last_success_at": time.time()}

    def _failed(self, name, exc):
        client, self.clients[name] = self.clients[name], None
        if client is not None:
            try:
                client.close()
            except Exception:
                log.warning("telemetry_client_close_failed", dependency=name)
        self.failures[name] += 1
        delay = min(MAX_RETRY_S, POLL_INTERVAL_S * 2 ** min(self.failures[name] - 1, 4))
        self.retry_at[name] = self.clock() + delay
        self.dependencies[name] = {"state": "retrying", "error_kind": type(exc).__name__,
                                   "consecutive_failures": self.failures[name]}
        log.warning("telemetry_dependency_unavailable", dependency=name,
                    error_kind=type(exc).__name__, retry_seconds=delay)

    def _publish(self, state):
        with self.delivery.lock:
            episodes = {name: {"alert_id": episode["payload"]["alert_id"], "state": episode["state"]}
                        for name, episode in self.delivery.episodes.items()}
        self.state.update(state=state, timestamp=time.time(), episodes=episodes,
                          dependencies={name: {**value, "retry_in_seconds": max(0, self.retry_at[name] - self.clock())}
                                        for name, value in self.dependencies.items()})
        write_json_atomic(self.directory / "collector_status.json", self.state)

    def poll_once(self):
        start = self.clock()
        self._publish("polling")
        observations = {name: Observation("UNKNOWN", "Docker observation unavailable") for name in SERVICES}
        if self.clock() >= self.retry_at["docker"]:
            try:
                if self.clients["docker"] is None:
                    self.clients["docker"] = self.docker_factory()
                if not self.clients["docker"].ping():
                    raise ConnectionError("Docker ping failed")
                sampled = self.observer(self.clients["docker"], SERVICES, http_client=self.http)
                observations = {name: sampled.get(name, Observation("UNKNOWN", "Probe returned no observation"))
                                for name in SERVICES}
                self._connected("docker")
            except Exception as exc:
                self._failed("docker", exc)

        records = {}
        for name, item in observations.items():
            try:
                age = age_seconds(item.measured_at)
                if age is None or age > settings.observation_max_age:
                    raise ValueError("Invalid measurement time")
                record = ObservationRecord(
                    **item.to_dict(), service_id=name,
                    quality="unknown" if item.status == "UNKNOWN" else "observed")
            except (TypeError, ValueError, AttributeError):
                item = observations[name] = Observation("UNKNOWN", "Invalid or expired probe evidence")
                record = ObservationRecord(**item.to_dict(), service_id=name, quality="unknown")
            records[name] = record.model_dump(mode="json")

        synced, roots = set(), set()
        if self.clock() >= self.retry_at["neo4j"]:
            try:
                if self.clients["neo4j"] is None:
                    self.clients["neo4j"] = self.graph_factory()
                synced = self.clients["neo4j"].update_service_observations(observations)
                if synced == set(SERVICES):
                    roots = {row["name"] for row in self.clients["neo4j"].get_independent_roots()}
                self._connected("neo4j")
            except Exception as exc:
                synced, roots = set(), set()
                self._failed("neo4j", exc)

        complete = synced == set(SERVICES) and all(
            row["quality"] == "observed"
            and (age := age_seconds(row["measured_at"])) is not None
            and age <= settings.observation_max_age
            for row in records.values())
        eligible = roots if complete else set()
        for name, item in observations.items():
            self.delivery.observe(name, item, eligible=name in eligible, evidence=records[name])
        # Persist the completed evidence before any new alert can authorize work.
        self.state.update(observations=records, graph_synced=synced == set(SERVICES),
                          cycle_completed_at=time.time(), poll_seconds=self.clock() - start)
        self._publish("ready" if complete else "degraded")
        if self.deliver_alerts and eligible and not self.stop.is_set():
            with self.delivery.lock:
                matching = {name for name in eligible if name in self.delivery.episodes
                            and self.delivery.episodes[name]["payload"]["error_type"] == observations[name].status}
            if matching:
                self.delivery.dispatch(eligible_services=matching)

    def run(self, once: bool = False):
        try:
            self._publish("starting")
            while not self.stop.is_set():
                start = self.clock()
                try:
                    self.poll_once()
                except Exception as exc:
                    # Audit failures must not dispatch an unrecorded incident.
                    log.error("telemetry_cycle_failed", error_kind=type(exc).__name__)
                    self.state["graph_synced"] = False
                    self._publish("degraded")
                if once:
                    break
                self.stop.wait(max(0, POLL_INTERVAL_S - (self.clock() - start)))
        finally:
            self.close()

    def close(self):
        self.stop.set()
        try:
            self._publish("stopped")
        finally:
            self.http.close()
            for name, client in self.clients.items():
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        log.warning("telemetry_client_close_failed", dependency=name)
                    self.clients[name] = None


def run(deliver_alerts: bool = True, once: bool = False) -> None:
    setup_logging()
    with collector_lock(settings.audit_dir):
        collector = TelemetryCollector(settings.audit_dir, deliver_alerts=deliver_alerts)
        previous = {sig: signal.signal(sig, lambda *_: collector.stop.set())
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            collector.run(once=once)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-only", action="store_true",
                        help="Synchronize health and record incidents without submitting alerts")
    parser.add_argument("--once", action="store_true", help="One observe-only poll, then stop")
    args = parser.parse_args()
    if args.once and not args.observe_only:
        parser.error("--once requires --observe-only")
    run(deliver_alerts=not args.observe_only, once=args.once)
