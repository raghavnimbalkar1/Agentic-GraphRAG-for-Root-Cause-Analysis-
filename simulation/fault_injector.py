"""
simulation/fault_injector.py

Chaos engineering fault injection against the running Online Boutique stack.

Each fault function follows the same contract:
    1. Break something real via Docker (container/network/exec)
    2. Update Neo4j Service node to reflect ground-truth status
    3. POST an AlertPayload to the agent webhook (best-effort — Phase 4
       agent may not be running yet, failures are logged, not fatal)

Each fault has a matching reset_* function for cleanup between scenarios.

CLI usage:
    python -m simulation.fault_injector inject redis_oom
    python -m simulation.fault_injector inject service_crash --target paymentservice
    python -m simulation.fault_injector reset redis_oom
    python -m simulation.fault_injector list
"""

from __future__ import annotations

import argparse
import time

import docker
import httpx
from docker.errors import NotFound, APIError

from core import get_logger, settings
from core.schemas import AlertPayload, AlertSeverity, ServiceStatus
from graph.graph_client import GraphClient

log = get_logger(__name__)

BOUTIQUE_NETWORK = "boutique-sim"
ALERT_ENDPOINT = f"http://localhost:{settings.alert_listen_port}/alert"


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


# ── Alert dispatch ───────────────────────────────────────────────────────

def _send_alert(service: str, error_type: ServiceStatus, message: str,
                severity: AlertSeverity = AlertSeverity.CRITICAL) -> None:
    """
    POST an alert to the agent webhook. Best-effort — if the agent
    (Phase 4) isn't running yet, logs a warning and continues.
    """
    payload = AlertPayload(
        service=service,
        error_type=error_type,
        message=message,
        severity=severity,
    )
    try:
        # The agent handles /alert synchronously: graph traversal + LLM
        # reasoning + sandbox execution + verification can take well over the
        # old 5s client timeout (and longer with multi-attempt escalation or
        # transient LLM 503 retries). Wait for the real resolution so we log
        # the true outcome instead of a spurious timeout.
        resp = httpx.post(
            ALERT_ENDPOINT,
            json=payload.model_dump(mode="json"),
            timeout=180.0,
        )
        resp.raise_for_status()
        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        log.info("alert_sent", alert_id=payload.alert_id, service=service,
                 error_type=error_type.value,
                 resolution=body.get("resolution") or body.get("status"),
                 root_cause=body.get("root_cause"))
    except httpx.ConnectError:
        log.warning("alert_endpoint_unreachable",
                    endpoint=ALERT_ENDPOINT,
                    alert_id=payload.alert_id,
                    note="Agent not running yet — expected before Phase 4")
    except httpx.HTTPError as e:
        log.error("alert_send_failed", error=str(e), alert_id=payload.alert_id)


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
    _send_alert(
        service="frontend",
        error_type=ServiceStatus.OOM_KILLED,
        message="cartservice reporting cache errors — redis-cart evicting keys under memory pressure",
    )


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

    gc = GraphClient()
    dependents = gc.get_dependents(target)

    alert_from = dependents[0] if dependents else target
    _send_alert(
        service=alert_from,
        error_type=ServiceStatus.CRASH_LOOPING,
        message=f"gRPC connection refused to {target} — container unreachable",
    )


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

    gc = GraphClient()
    dependents = gc.get_dependents(target)

    alert_from = dependents[0] if dependents else target
    _send_alert(
        service=alert_from,
        error_type=ServiceStatus.CONNECTION_REFUSED,
        message=f"Network partition — {target} unreachable from {alert_from}",
    )


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

    gc = GraphClient()
    dependents = gc.get_dependents(target)

    alert_from = dependents[0] if dependents else target
    _send_alert(
        service=alert_from,
        error_type=ServiceStatus.DEGRADED,
        message=f"{target} response times degraded — {delay_ms}ms latency injected",
        severity=AlertSeverity.HIGH,
    )


def reset_high_latency(target: str) -> None:
    """Removes the tc qdisc netem rule from the target container."""
    log.info("fault_resetting", fault="high_latency", target=target)
    client = _docker()
    container = _get_container(client, target)

    container.exec_run("tc qdisc del dev eth0 root netem", privileged=True)

    _update_graph_status(target, ServiceStatus.HEALTHY)
    log.info("fault_reset_complete", fault="high_latency", target=target)


# ===========================================================================
# Registry — used by CLI and eval/scenarios.json
# ===========================================================================

FAULTS: dict[str, tuple] = {
    "redis_oom": (
        inject_redis_oom,
        reset_redis_oom,
        None,                       # No --target needed
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