"""
simulation/telemetry_collector.py

Real health-observation loop — the closed-loop "sensing" layer.

This is a standalone background process that continuously observes the REAL
state of the running Online Boutique containers (via the Docker SDK and, for
redis-cart, live redis-cli probes) and writes ground-truth health into the
Neo4j Service nodes — completely independently of the fault injector.

Two responsibilities, both edge-/level-correct:

  1. SYNC (level-triggered): every poll, map each container's real state to a
     ServiceStatus and, if it differs from what Neo4j currently holds, update
     Neo4j. This is what makes the dashboard reflect reality and what makes the
     agent's graph queries operate on observed (not injected) truth.

  2. ALERT (edge-triggered): when a service transitions HEALTHY -> unhealthy and
     the agent webhook is reachable, POST an /alert describing what the health
     check actually found. The fault injector no longer fires alerts — it only
     breaks things; this collector detects the break and raises the incident.

Design notes
------------
* Alerts fire only on the HEALTHY -> unhealthy *edge*, so a service that stays
  broken across many polls raises exactly one incident (no alert storm while the
  agent is mid-remediation). It re-arms once the service returns to HEALTHY.
* The first poll establishes a baseline and does NOT alert, so starting the
  collector against an already-degraded stack won't spuriously page.
* Alerts are dispatched on a daemon thread so the 5s poll cadence never blocks
  on the agent's synchronous resolution (which can take 10s+).
* GraphClient is a singleton — instantiated directly, never as a context manager.

Run:
    python -m simulation.telemetry_collector
"""

from __future__ import annotations

import signal
import sys
import threading
import time

import docker
import httpx
from docker.errors import NotFound, APIError

from core import get_logger, settings
from core.schemas import ServiceStatus
from graph.graph_client import GraphClient

log = get_logger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

POLL_INTERVAL_S  = 5
BOUTIQUE_NETWORK = "boutique-sim"
REDIS_SERVICE    = "redis-cart"
ALERT_ENDPOINT   = f"http://localhost:{settings.alert_listen_port}/alert"
HEALTH_ENDPOINT  = f"http://localhost:{settings.alert_listen_port}/health"

# Redis maxmemory at/below this (and > 0) is treated as an injected OOM cap.
# Healthy redis-cart runs at 256mb (268435456) or 0 (unlimited).
OOM_MAXMEMORY_CEILING = 10 * 1024 * 1024   # 10 MB

# ── Section 1 detection thresholds ───────────────────────────────────────────
DISK_PRESSURE_CEILING   = 100 * 1024 * 1024   # writable layer > 100 MB
POOL_EXHAUSTION_CLIENTS = 50                  # redis connected_clients > 50
MEMORY_LEAK_CEILING     = 300 * 1024 * 1024   # container mem usage > 300 MB
LATENCY_THRESHOLD_S     = 2.0                 # HTTP response slower than 2s
REDIS_BASELINE_POLICY   = "allkeys-lru"       # known-good maxmemory-policy

# Services whose container memory is sampled each poll (docker stats is costly,
# so we scope it). adservice -> CPU watch; recommendationservice -> memory watch.
MEMORY_WATCH = {"recommendationservice"}
# Frontend is probed for HTTP latency (DEPENDENCY_TIMEOUT).
LATENCY_WATCH = {"frontend": "http://localhost:8080/"}

# The 12 Online Boutique services (container name == Neo4j Service.name).
SERVICES = [
    "redis-cart", "emailservice", "productcatalogservice", "currencyservice",
    "paymentservice", "shippingservice", "adservice", "cartservice",
    "recommendationservice", "checkoutservice", "frontend", "loadgenerator",
]

HEALTHY = ServiceStatus.HEALTHY.value


def _docker() -> docker.DockerClient:
    return docker.DockerClient(base_url=settings.docker_host)


# ── Per-service health probe ─────────────────────────────────────────────────

def check_service(client: docker.DockerClient, name: str) -> tuple[str, str]:
    """
    Observe one service's REAL health. Returns (status, human_message).

    Order of checks (first failure wins):
        container missing / not running  -> CRASH_LOOPING
        not attached to boutique-sim      -> CONNECTION_REFUSED
        redis-cart: ping fails / capped   -> OOM_KILLED
        redis-cart: stale-key anomaly     -> STALE_DATA      (Step 4)
        adservice: CPU over threshold     -> HIGH_CPU        (Step 4)
        otherwise                         -> HEALTHY
    """
    try:
        c = client.containers.get(name)
    except NotFound:
        return ServiceStatus.CRASH_LOOPING.value, "container not found"
    except APIError as e:
        return HEALTHY, f"docker api error (assuming healthy): {e}"

    try:
        c.reload()
    except APIError:
        pass

    # 1) Container running? (paused / exited / restarting all count as down)
    if c.status != "running":
        return ServiceStatus.CRASH_LOOPING.value, f"container status={c.status}"

    # 2) Attached to the simulation network?
    nets = (c.attrs.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
    if BOUTIQUE_NETWORK not in nets:
        return (ServiceStatus.CONNECTION_REFUSED.value,
                f"not attached to {BOUTIQUE_NETWORK} (nets={list(nets)})")

    # 3) Disk pressure — writable layer size (cheap inspect-with-size)
    disk = _check_disk(client, name)
    if disk is not None:
        return disk

    # 4) Redis-specific deep checks (OOM, pool exhaustion, config drift, stale)
    if name == REDIS_SERVICE:
        return _check_redis(c)

    # 5) AdService CPU check
    if name == "adservice":
        cpu_status = _check_adservice_cpu(c)
        if cpu_status is not None:
            return cpu_status

    # 6) Memory-leak watch (docker stats — scoped to MEMORY_WATCH services)
    if name in MEMORY_WATCH:
        mem_status = _check_memory(c)
        if mem_status is not None:
            return mem_status

    # 7) Latency watch (HTTP probe — scoped to LATENCY_WATCH services)
    if name in LATENCY_WATCH:
        lat_status = _check_latency(name, LATENCY_WATCH[name])
        if lat_status is not None:
            return lat_status

    return HEALTHY, "ok"


def _size_rw(client: docker.DockerClient, name: str) -> int:
    """Writable-layer size (SizeRw) for a container, via the size-enabled list API."""
    try:
        for c in client.api.containers(all=True, size=True, filters={"name": name}):
            if any(n.lstrip("/") == name for n in c.get("Names", [])):
                return c.get("SizeRw", 0) or 0
    except APIError:
        return 0
    return 0


def _check_disk(client: docker.DockerClient, name: str) -> tuple[str, str] | None:
    """DISK_PRESSURE: container writable layer (SizeRw) over the ceiling."""
    size_rw = _size_rw(client, name)
    if size_rw > DISK_PRESSURE_CEILING:
        return (ServiceStatus.DISK_PRESSURE.value,
                f"writable layer {size_rw // (1024*1024)}MB > "
                f"{DISK_PRESSURE_CEILING // (1024*1024)}MB")
    return None


def _check_memory(c) -> tuple[str, str] | None:
    """MEMORY_LEAK: container resident memory over the ceiling."""
    try:
        stats = c.stats(stream=False)
        usage = (stats.get("memory_stats", {}) or {}).get("usage", 0) or 0
        if usage > MEMORY_LEAK_CEILING:
            return (ServiceStatus.MEMORY_LEAK.value,
                    f"memory usage {usage // (1024*1024)}MB > "
                    f"{MEMORY_LEAK_CEILING // (1024*1024)}MB")
    except Exception:  # noqa: BLE001
        return None
    return None


def _check_latency(name: str, url: str) -> tuple[str, str] | None:
    """DEPENDENCY_TIMEOUT: HTTP response slower than the latency budget."""
    try:
        t0 = time.perf_counter()
        httpx.get(url, timeout=LATENCY_THRESHOLD_S + 4.0)
        elapsed = time.perf_counter() - t0
        if elapsed > LATENCY_THRESHOLD_S:
            return (ServiceStatus.DEPENDENCY_TIMEOUT.value,
                    f"{name} responded in {elapsed:.2f}s (> {LATENCY_THRESHOLD_S}s budget)")
    except Exception:  # noqa: BLE001 — timeout / connection error = slow/unavailable
        return (ServiceStatus.DEPENDENCY_TIMEOUT.value,
                f"{name} did not respond within latency budget")
    return None


def _check_redis(c) -> tuple[str, str]:
    """redis-cart deep health: ping, maxmemory cap, and stale-data anomaly."""
    try:
        ping = c.exec_run(["redis-cli", "ping"], demux=False)
        if b"PONG" not in (ping.output or b""):
            return ServiceStatus.OOM_KILLED.value, "redis ping did not return PONG"

        mm = c.exec_run(["redis-cli", "CONFIG", "GET", "maxmemory"], demux=False)
        maxmemory = _parse_redis_value(mm.output)
        if maxmemory is not None and 1 <= maxmemory <= OOM_MAXMEMORY_CEILING:
            return (ServiceStatus.OOM_KILLED.value,
                    f"maxmemory capped at {maxmemory} bytes (<= {OOM_MAXMEMORY_CEILING})")

        # POOL_EXHAUSTION: too many open client connections
        info = c.exec_run(["redis-cli", "INFO", "clients"], demux=False)
        clients = _parse_info_field(info.output, "connected_clients")
        if clients is not None and clients > POOL_EXHAUSTION_CLIENTS:
            return (ServiceStatus.POOL_EXHAUSTION.value,
                    f"connected_clients={clients} (> {POOL_EXHAUSTION_CLIENTS})")

        # CONFIG_DRIFT: maxmemory-policy drifted from the known-good baseline
        pol = c.exec_run(["redis-cli", "CONFIG", "GET", "maxmemory-policy"], demux=False)
        policy = _parse_redis_str(pol.output)
        if policy is not None and policy != REDIS_BASELINE_POLICY:
            return (ServiceStatus.CONFIG_DRIFT.value,
                    f"maxmemory-policy='{policy}' != baseline '{REDIS_BASELINE_POLICY}'")

        # STALE_DATA: a large pool of volatile/expiring keys
        stale = _check_redis_stale(c)
        if stale is not None:
            return stale
    except APIError as e:
        return ServiceStatus.OOM_KILLED.value, f"redis exec failed: {e}"

    return HEALTHY, "ok"


def _parse_info_field(output: bytes | None, field: str) -> int | None:
    """Parse an integer field from `redis-cli INFO` output (field:value)."""
    if not output:
        return None
    for line in output.decode(errors="replace").splitlines():
        if line.startswith(field + ":"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _parse_redis_str(output: bytes | None) -> str | None:
    """CONFIG GET returns 'key\\nvalue' — return the value as a stripped string."""
    if not output:
        return None
    toks = [t for t in output.decode(errors="replace").split() if t.strip()]
    return toks[-1] if toks else None


def _check_redis_stale(c) -> tuple[str, str] | None:
    """
    STALE_DATA heuristic (Step 4): inject_stale_data() floods redis with a large
    number of keys carrying short TTLs. We flag STALE_DATA when the keyspace has
    an anomalously large number of volatile (TTL-bearing) keys relative to a
    healthy steady state (the boutique normally holds only a handful of cart keys).
    """
    try:
        info = c.exec_run(["redis-cli", "INFO", "keyspace"], demux=False)
        text = (info.output or b"").decode(errors="replace")
        # db0:keys=N,expires=M,avg_ttl=...
        expires = 0
        keys = 0
        for line in text.splitlines():
            if line.startswith("db0:"):
                for part in line.split(":", 1)[1].split(","):
                    if part.startswith("keys="):
                        keys = int(part.split("=")[1])
                    elif part.startswith("expires="):
                        expires = int(part.split("=")[1])
        # Healthy steady state: a few cart keys, ~0 volatile keys. The stale-data
        # fault writes ~1000 expiring keys, so a large volatile pool is the signal.
        if expires >= 200:
            return (ServiceStatus.STALE_DATA.value,
                    f"stale-data anomaly: {expires} volatile keys (keys={keys})")
    except APIError:
        return None
    return None


def _check_adservice_cpu(c) -> tuple[str, str] | None:
    """
    HIGH_CPU heuristic (Step 4): read a single docker stats sample and flag
    HIGH_CPU if the container's CPU usage exceeds a threshold. Returns None if
    CPU is normal (so the caller falls through to HEALTHY).
    """
    try:
        stats = c.stats(stream=False)
        cpu_pct = _cpu_percent(stats)
        if cpu_pct is not None and cpu_pct >= 80.0:
            return ServiceStatus.HIGH_CPU.value, f"cpu at {cpu_pct:.0f}% (>= 80%)"
    except Exception:
        return None
    return None


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _parse_redis_value(output: bytes | None) -> int | None:
    """CONFIG GET returns 'maxmemory\\n<value>\\n' — return <value> as int."""
    if not output:
        return None
    lines = [l for l in output.decode(errors="replace").split() if l.strip()]
    for tok in reversed(lines):
        if tok.lstrip("-").isdigit():
            return int(tok)
    return None


def _cpu_percent(stats: dict) -> float | None:
    """Compute CPU % from a docker stats sample (Linux cgroup deltas)."""
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu["system_cpu_usage"] - pre.get("system_cpu_usage", 0)
        ncpus = cpu.get("online_cpus") or len(
            cpu["cpu_usage"].get("percpu_usage") or [1])
        if sys_delta > 0 and cpu_delta > 0:
            return (cpu_delta / sys_delta) * ncpus * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    return None


# ── Alert dispatch (edge-triggered, non-blocking) ────────────────────────────

def _agent_reachable() -> bool:
    try:
        return httpx.get(HEALTH_ENDPOINT, timeout=2.0).status_code == 200
    except Exception:
        return False


def _fire_alert(service: str, error_type: str, message: str) -> None:
    """POST an alert to the agent on a daemon thread so the poll loop never blocks."""
    def _send():
        payload = {"service": service, "error_type": error_type, "message": message}
        try:
            r = httpx.post(ALERT_ENDPOINT, json=payload, timeout=180.0)
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            log.info("telemetry_alert_resolved",
                     service=service, error_type=error_type,
                     resolution=body.get("status"), root_cause=body.get("root_cause"))
        except Exception as e:  # noqa: BLE001
            log.error("telemetry_alert_failed", service=service, error=str(e))

    log.warning("telemetry_alert_fired",
                service=service, error_type=error_type, message=message)
    threading.Thread(target=_send, daemon=True).start()


# ── Main poll loop ───────────────────────────────────────────────────────────

_running = True


def _handle_sigint(_signum, _frame):
    global _running
    _running = False
    log.info("telemetry_collector_stopping")


def run() -> None:
    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    client = _docker()
    gc = GraphClient()

    log.info("telemetry_collector_starting",
             poll_interval_s=POLL_INTERVAL_S, services=len(SERVICES),
             alert_endpoint=ALERT_ENDPOINT)

    # In-memory record of the last status this collector OBSERVED per service.
    # Initialised on the first poll (baseline) without firing alerts.
    prev_observed: dict[str, str] = {}
    # Whether an alert has already been raised for the current unhealthy episode
    # of each service (re-armed when it returns to HEALTHY).
    alerted: dict[str, bool] = {}
    first_pass = True

    while _running:
        loop_start = time.time()
        try:
            neo4j_status = gc.get_all_service_statuses()
        except Exception as e:  # noqa: BLE001
            log.error("telemetry_neo4j_read_failed", error=str(e))
            neo4j_status = {}

        for svc in SERVICES:
            try:
                status, message = check_service(client, svc)
            except Exception as e:  # noqa: BLE001
                log.error("telemetry_check_failed", service=svc, error=str(e))
                continue

            # 1) Sync Neo4j to observed reality if it drifted.
            if neo4j_status.get(svc) != status:
                try:
                    gc.update_service_status(
                        service_name=svc,
                        status=status,
                        error_code=None if status == HEALTHY else status,
                    )
                    log.info("telemetry_status_synced",
                             service=svc, observed=status,
                             was=neo4j_status.get(svc), detail=message)
                except Exception as e:  # noqa: BLE001
                    log.error("telemetry_sync_failed", service=svc, error=str(e))

            # 2) Debounced, edge-triggered alert. We fire ONCE per unhealthy
            #    episode, and only after the SAME unhealthy status is seen on two
            #    consecutive polls. The debounce smooths transient container
            #    lifecycle blips (e.g. a few seconds of "removing" while a
            #    container is recreated) so they don't raise spurious incidents,
            #    and ensures the stable real fault (e.g. OOM_KILLED) is the one
            #    that pages. `alerted[svc]` re-arms when the service goes HEALTHY.
            was = prev_observed.get(svc, HEALTHY)
            if status == HEALTHY:
                alerted[svc] = False
            elif (not first_pass) and status == was and not alerted.get(svc, False):
                if _agent_reachable():
                    _fire_alert(svc, status,
                                f"telemetry detected {svc} unhealthy: {message}")
                    alerted[svc] = True
                else:
                    log.warning("telemetry_alert_suppressed_agent_down",
                                service=svc, status=status)

            prev_observed[svc] = status

        first_pass = False

        elapsed = time.time() - loop_start
        time.sleep(max(0.0, POLL_INTERVAL_S - elapsed))

    log.info("telemetry_collector_stopped")


if __name__ == "__main__":
    run()
