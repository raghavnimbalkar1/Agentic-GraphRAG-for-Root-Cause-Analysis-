"""
simulation/fault_injector.py

Chaos engineering fault injection against the running Online Boutique stack.

Separation of concerns (closed-loop architecture):
    The injector ONLY breaks things and records the intended ground-truth
    status in Neo4j. It does NOT raise alerts. Detection and alerting are the
    job of simulation/telemetry_collector.py, which independently observes the
    real container/redis state and fires the /alert when it sees degradation.
    This removes the old "faked detection" path where the injector hand-wrote
    an alert with an error_type guaranteed to match a SOP.

Each fault function:
    1. Breaks something real via Docker (container/network/exec)
    2. Updates the Neo4j Service node to reflect ground-truth status
       (the telemetry collector will independently converge to the same truth)

Each fault has a matching reset_* function for cleanup between scenarios.

CLI usage:
    python -m simulation.fault_injector inject redis_oom
    python -m simulation.fault_injector inject service_crash --target paymentservice
    python -m simulation.fault_injector reset redis_oom
    python -m simulation.fault_injector list
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

import docker
from docker.errors import NotFound, APIError

from core import get_logger, settings
from core.schemas import ServiceStatus

COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose.yml"
from graph.graph_client import GraphClient

log = get_logger(__name__)

BOUTIQUE_NETWORK = "boutique-sim"


# ── Docker client ────────────────────────────────────────────────────────

def _docker() -> docker.DockerClient:
    """Returns a Docker client pointed at settings.docker_host."""
    return docker.DockerClient(base_url=settings.docker_host)


def _get_container(client: docker.DockerClient, name: str):
    try:
        return client.containers.get(name)
    except NotFound as e:
        raise RuntimeError(
            f"Container '{name}' not found. Is the simulation stack running? "
            f"(docker compose -f simulation/docker-compose.yml up -d)"
        ) from e


# ── Graph update helper ──────────────────────────────────────────────────

def _update_graph_status(service: str, status: ServiceStatus) -> None:
    gc = GraphClient()
    gc.update_service_status(
        service_name=service,
        status=status.value,
        error_code=status.value if status != ServiceStatus.HEALTHY else None,
    )


# ===========================================================================
# FAULT 1 — Redis OOM (redis-cart)
# Cascades: redis-cart -> cartservice -> checkoutservice / frontend
# ===========================================================================

def inject_redis_oom() -> None:
    """
    Forces redis-cart into memory pressure by setting a tiny maxmemory cap
    and filling it with junk. cartservice operations start failing or
    returning evicted/stale data.
    """
    log.info("fault_injecting", fault="redis_oom", target="redis-cart")
    client = _docker()
    container = _get_container(client, "redis-cart")

    # Log original maxmemory for reference
    original = container.exec_run(["redis-cli", "CONFIG", "GET", "maxmemory"])
    log.info("redis_oom_original_maxmemory",
             output=original.output.decode(errors="replace").strip())

    # Set a tiny memory cap and aggressive eviction policy
    container.exec_run(["redis-cli", "CONFIG", "SET", "maxmemory", "1mb"])
    container.exec_run(["redis-cli", "CONFIG", "SET", "maxmemory-policy", "allkeys-lru"])

    # Fill with enough keys to trigger eviction pressure
    for i in range(500):
        container.exec_run(
            ["redis-cli", "SET", f"bloat:{i}", "x" * 512]
        )

    dbsize = container.exec_run(["redis-cli", "DBSIZE"])
    log.info("redis_oom_injected",
             dbsize=dbsize.output.decode(errors="replace").strip())

    _update_graph_status("redis-cart", ServiceStatus.OOM_KILLED)
    # No alert here — the telemetry collector will detect the capped maxmemory
    # on its next poll and raise the incident itself.


def reset_redis_oom() -> None:
    """Restores redis-cart to normal operation."""
    log.info("fault_resetting", fault="redis_oom", target="redis-cart")
    client = _docker()
    container = _get_container(client, "redis-cart")

    container.exec_run(["redis-cli", "FLUSHALL"])
    container.exec_run(["redis-cli", "CONFIG", "SET", "maxmemory", "256mb"])
    container.exec_run(["redis-cli", "CONFIG", "SET", "maxmemory-policy", "allkeys-lru"])

    _update_graph_status("redis-cart", ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="redis_oom")


# ===========================================================================
# FAULT 1b — Persistent Redis OOM (restart does NOT clear it)
# Exercises the NEXT_IF_FAIL fallback chain: Redis_Restart_SOP runs a restart,
# real verification still sees the cap, agent falls back to Redis_Flush_SOP.
#
# Stock redis:alpine starts as bare `redis-server` with no config file, so a
# runtime `CONFIG SET maxmemory` does NOT survive `docker restart` (it resets to
# the default 0/unlimited) and CONFIG REWRITE has no file to write. To make the
# cap genuinely persist across the SOP's restart, we recreate redis-cart with the
# cap baked into its command: `redis-server --maxmemory 1mb`. `docker restart`
# then preserves it. The fallback flush (cache_flush.sh) raises the live maxmemory
# back to 256mb, which the restart alone could never do.
# ===========================================================================

def _recreate_redis(maxmemory: str | None) -> None:
    """Remove and recreate redis-cart, optionally with a baked-in maxmemory cap."""
    client = _docker()
    try:
        client.containers.get("redis-cart").remove(force=True)
    except NotFound:
        pass

    command = ["redis-server"]
    if maxmemory is not None:
        command += ["--maxmemory", maxmemory, "--maxmemory-policy", "noeviction"]

    client.containers.run(
        "redis:alpine",
        command=command,
        name="redis-cart",
        network=BOUTIQUE_NETWORK,
        ports={"6379/tcp": 6379},
        restart_policy={"Name": "unless-stopped"},
        detach=True,
    )

    # Wait for redis to accept connections so the collector's next probe is valid.
    container = _get_container(client, "redis-cart")
    for _ in range(15):
        res = container.exec_run(["redis-cli", "ping"])
        if b"PONG" in (res.output or b""):
            return
        time.sleep(1)


def inject_persistent_redis_oom() -> None:
    """
    Recreate redis-cart with maxmemory baked into its launch command so the cap
    survives `docker restart`. Redis_Restart_SOP will not fix it; the agent must
    follow NEXT_IF_FAIL to Redis_Flush_SOP.
    """
    log.info("fault_injecting", fault="redis_oom_persistent", target="redis-cart")
    _recreate_redis(maxmemory="1mb")
    log.info("redis_oom_persistent_injected",
             note="redis-cart recreated with --maxmemory 1mb (survives restart)")
    _update_graph_status("redis-cart", ServiceStatus.OOM_KILLED)
    # No alert — the telemetry collector detects the persistent cap and alerts.


def reset_persistent_redis_oom() -> None:
    """Recreate redis-cart with no cap (back to the stock unlimited config)."""
    log.info("fault_resetting", fault="redis_oom_persistent", target="redis-cart")
    _recreate_redis(maxmemory=None)
    _update_graph_status("redis-cart", ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="redis_oom_persistent")


# ===========================================================================
# FAULT 2 — Service Crash (any service)
# SIGKILL -> restart: unless-stopped brings it back -> brief CRASH_LOOPING
# ===========================================================================

def inject_service_crash(target: str) -> None:
    """
    SIGKILLs the target container. Compose restart: unless-stopped will
    bring it back, but upstream dependents see connection refused during
    the restart window.
    """
    log.info("fault_injecting", fault="service_crash", target=target)
    client = _docker()
    container = _get_container(client, target)

    container.kill(signal="SIGKILL")
    log.info("service_killed", target=target)

    _update_graph_status(target, ServiceStatus.CRASH_LOOPING)
    # No alert here — the telemetry collector detects the down container and
    # raises the incident. NOTE: `restart: unless-stopped` can bring the
    # container back within ~1-2s, faster than the 5s poll, so this fault may
    # self-heal before detection; redis_oom / network_partition are the
    # reliably-detectable demo faults.


def reset_service_crash(target: str) -> None:
    """
    Waits for Docker to restart the container automatically
    (via restart: unless-stopped) then marks it healthy in the graph.
    """
    log.info("fault_resetting", fault="service_crash", target=target)
    client = _docker()

    for attempt in range(15):
        container = _get_container(client, target)
        container.reload()
        if container.status == "running":
            log.info("service_restarted", target=target, attempts=attempt + 1)
            break
        time.sleep(2)
    else:
        log.warning("service_crash_reset_timeout",
                    target=target, status=container.status)

    _update_graph_status(target, ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="service_crash", target=target)


# ===========================================================================
# FAULT 3 — Network Partition (any service)
# Disconnects container from boutique-sim network entirely
# ===========================================================================

def inject_network_partition(target: str) -> None:
    """
    Disconnects the target container from boutique-sim. All gRPC calls
    to/from it will fail with connection errors until reset.
    """
    log.info("fault_injecting", fault="network_partition", target=target)
    client = _docker()
    container = _get_container(client, target)

    client.networks.get(BOUTIQUE_NETWORK).disconnect(container, force=True)
    log.info("network_disconnected", target=target, network=BOUTIQUE_NETWORK)

    _update_graph_status(target, ServiceStatus.CONNECTION_REFUSED)
    # No alert here — the telemetry collector detects the missing boutique-sim
    # network attachment on its next poll and raises the incident.


def reset_network_partition(target: str) -> None:
    """Reconnects the target container to boutique-sim."""
    log.info("fault_resetting", fault="network_partition", target=target)
    client = _docker()
    container = _get_container(client, target)

    try:
        client.networks.get(BOUTIQUE_NETWORK).connect(container)
    except APIError as e:
        if "already exists" in str(e).lower():
            log.info("network_already_connected", target=target)
        else:
            raise

    _update_graph_status(target, ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="network_partition", target=target)


# ===========================================================================
# FAULT 4 — High Latency (any service)
# Uses tc (traffic control) to add artificial network delay
# ===========================================================================

def inject_high_latency(target: str, delay_ms: int = 2000) -> None:
    """
    Adds artificial network latency inside the target container using tc.
    Falls back to a warning for distroless images that lack iproute2.
    """
    log.info("fault_injecting", fault="high_latency",
             target=target, delay_ms=delay_ms)
    client = _docker()
    container = _get_container(client, target)

    result = container.exec_run(
        f"tc qdisc add dev eth0 root netem delay {delay_ms}ms",
        privileged=True,
    )
    if result.exit_code != 0:
        log.warning("high_latency_tc_unavailable",
                    target=target,
                    output=result.output.decode(errors="replace"),
                    note="Image likely lacks iproute2 — use service_crash or "
                         "network_partition for this service instead")
        return

    _update_graph_status(target, ServiceStatus.DEGRADED)
    # No alert here — detection/alerting is the telemetry collector's job.


def reset_high_latency(target: str) -> None:
    """Removes the tc qdisc netem rule from the target container."""
    log.info("fault_resetting", fault="high_latency", target=target)
    client = _docker()
    container = _get_container(client, target)

    container.exec_run("tc qdisc del dev eth0 root netem", privileged=True)

    _update_graph_status(target, ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="high_latency", target=target)


# ===========================================================================
# FAULT 5 — Stale Data (redis-cart)  → activates Redis_Flush_SOP (STALE_DATA)
# Floods the cache with a large volatile keyspace representing stale entries.
# ===========================================================================

def inject_stale_data() -> None:
    """
    Write ~1000 TTL-bearing keys to redis-cart to simulate a cache full of stale
    entries. A moderate TTL (600s) is used so the anomaly persists long enough to
    be detected (the collector flags an abnormally large volatile keyspace) and
    remediated — very short TTLs would self-expire before the 2-poll debounce.
    Uses a single EVAL so all keys are written in one round-trip (fast).
    """
    log.info("fault_injecting", fault="stale_data", target="redis-cart")
    client = _docker()
    container = _get_container(client, "redis-cart")

    lua = ("for i=1,1000 do redis.call('SET','stale:'..i,'stale-value-'..i,'EX',600) end "
           "redis.call('SET','stale:permanent','never-expires') return 1000")
    container.exec_run(["redis-cli", "EVAL", lua, "0"])

    info = container.exec_run(["redis-cli", "INFO", "keyspace"])
    log.info("stale_data_injected",
             keyspace=info.output.decode(errors="replace").strip())

    _update_graph_status("redis-cart", ServiceStatus.STALE_DATA)
    # No alert — the telemetry collector detects the stale-key anomaly and alerts.


def reset_stale_data() -> None:
    """Clear the stale keys and restore healthy state."""
    log.info("fault_resetting", fault="stale_data", target="redis-cart")
    client = _docker()
    container = _get_container(client, "redis-cart")
    container.exec_run(["redis-cli", "FLUSHALL"])
    _update_graph_status("redis-cart", ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="stale_data")


# ===========================================================================
# FAULT 6 — High CPU (adservice)  → activates AdService_CPU_Throttle_SOP
# Starts a busy-loop inside adservice so it monopolises a CPU core. The throttle
# SOP caps it via `docker update --cpus` (non-restart remediation).
# ===========================================================================

def inject_high_cpu(target: str = "adservice") -> None:
    """
    Launch a detached CPU burner inside the target so docker stats CPU% spikes.
    adservice ships a /bin/sh, so a shell busy-loop pins one core (~100%).
    """
    log.info("fault_injecting", fault="high_cpu", target=target)
    client = _docker()
    container = _get_container(client, target)

    # Detached busy loop — runs until the container is restarted (reset).
    container.exec_run(["sh", "-c", "while true; do :; done"], detach=True)
    log.info("high_cpu_burner_started", target=target)

    _update_graph_status(target, ServiceStatus.HIGH_CPU)
    # No alert — the telemetry collector detects the CPU spike via docker stats.


def reset_high_cpu(target: str = "adservice") -> None:
    """
    Remove the CPU cap and kill the burner. A `docker restart` reliably kills any
    detached exec'd process (adservice lacks ps/pkill), and we restore the CPU
    allocation the throttle SOP set.
    """
    log.info("fault_resetting", fault="high_cpu", target=target)
    # The throttle SOP sets HostConfig.NanoCpus via `docker update --cpus`, and
    # neither `--cpus=0` nor `cpu_quota=-1` actually clears that field — the cap
    # lingers, which would throttle (and hide) a future burner. A detached burner
    # also needs killing. Recreating the container from the compose spec is the
    # only guaranteed-clean reset: it drops the CPU cap AND the burner in one go.
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE),
             "up", "-d", "--force-recreate", target],
            check=True, capture_output=True, text=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("high_cpu_recreate_failed", target=target, error=str(e),
                    note="falling back to restart (CPU cap may persist)")
        try:
            _get_container(_docker(), target).restart(timeout=10)
        except Exception:
            pass

    _update_graph_status(target, ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="high_cpu", target=target)


# ===========================================================================
# Registry — used by CLI and eval/scenarios.json
# ===========================================================================

FAULTS: dict[str, tuple] = {
    "redis_oom": (
        inject_redis_oom,
        reset_redis_oom,
        None,                       # No --target needed
    ),
    "redis_oom_persistent": (
        inject_persistent_redis_oom,
        reset_persistent_redis_oom,
        None,                       # No --target needed (always redis-cart)
    ),
    "stale_data": (
        inject_stale_data,
        reset_stale_data,
        None,                       # No --target needed (always redis-cart)
    ),
    "high_cpu": (
        inject_high_cpu,
        reset_high_cpu,
        "adservice",                # Default target
    ),
    "service_crash": (
        inject_service_crash,
        reset_service_crash,
        "paymentservice",           # Default target
    ),
    "network_partition": (
        inject_network_partition,
        reset_network_partition,
        "paymentservice",
    ),
    "high_latency": (
        inject_high_latency,
        reset_high_latency,
        "productcatalogservice",
    ),
}


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Online Boutique fault injector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m simulation.fault_injector list\n"
            "  python -m simulation.fault_injector inject redis_oom\n"
            "  python -m simulation.fault_injector inject service_crash --target paymentservice\n"
            "  python -m simulation.fault_injector reset redis_oom\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inject_p = sub.add_parser("inject", help="Inject a fault")
    inject_p.add_argument("fault", choices=FAULTS.keys())
    inject_p.add_argument("--target", default=None,
                          help="Target container name (overrides default)")

    reset_p = sub.add_parser("reset", help="Reset a fault")
    reset_p.add_argument("fault", choices=FAULTS.keys())
    reset_p.add_argument("--target", default=None)

    sub.add_parser("list", help="List available faults and default targets")

    args = parser.parse_args()

    if args.command == "list":
        print("Available faults:")
        for name, (_, _, default_target) in FAULTS.items():
            tgt = f"  (default target: {default_target})" if default_target else ""
            print(f"  {name}{tgt}")
        return

    inject_fn, reset_fn, default_target = FAULTS[args.fault]
    fn = inject_fn if args.command == "inject" else reset_fn

    if default_target is not None:
        target = args.target or default_target
        fn(target)
    else:
        fn()

    print(f"\n{'Injected' if args.command == 'inject' else 'Reset'}: {args.fault}")


if __name__ == "__main__":
    main()