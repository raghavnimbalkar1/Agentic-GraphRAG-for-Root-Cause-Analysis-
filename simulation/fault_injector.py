"""
Fault injection engine for chaos engineering experiments.

Phase 1: Inject CPU spikes, network latency, connection pool exhaustion, pod kills.
Phase 2+: Coordinate with agent for controlled remediation testing.

Faults:
- cpu_spike: Increase CPU utilization to near 100%
- memory_pressure: Trigger OOM or memory pressure
- network_latency: Add millisecond-scale delay to service communication
- network_loss: Drop percentage of packets
- db_connection_pool: Exhaust database connection slots
- pod_kill: Force container restart
- disk_full: Fill temporary storage
"""

import argparse
import subprocess
import sys
import time
from typing import Optional

from core.config import get_config
from core.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


def inject_cpu_spike(service: str, duration: int = 60) -> bool:
    """Inject CPU spike on a service for specified duration."""
    logger.info(f"Injecting CPU spike on {service} for {duration}s...")
    # Stub: Use stress-ng or similar tool inside container
    # Docker exec container stress-ng --cpu 1 --timeout {duration}s
    return True


def inject_network_latency(service: str, latency_ms: int = 500) -> bool:
    """Inject network latency to service communication."""
    logger.info(f"Injecting {latency_ms}ms network latency to {service}...")
    # Stub: Use tc (traffic control) or Docker network delays
    return True


def inject_connection_pool_exhaustion(service: str, duration: int = 120) -> bool:
    """Exhaust database connection pool."""
    logger.info(f"Exhausting connection pool for {service} ({duration}s)...")
    # Stub: Open many connections without closing them
    return True


def inject_pod_kill(service: str) -> bool:
    """Kill a container pod to simulate crash."""
    logger.info(f"Killing pod for service {service}...")
    try:
        subprocess.run(
            ["docker-compose", "kill", service],
            check=True,
        )
        logger.info(f"Pod killed: {service}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to kill pod: {e}")
        return False


def main():
    """CLI entrypoint for fault injection."""
    parser = argparse.ArgumentParser(description="Fault injection engine")
    parser.add_argument(
        "--fault",
        choices=["cpu_spike", "memory_pressure", "network_latency", "network_loss",
                 "db_connection_pool", "pod_kill", "disk_full"],
        required=True,
    )
    parser.add_argument("--service", required=True, help="Target service name")
    parser.add_argument("--duration", type=int, default=60, help="Fault duration in seconds")
    parser.add_argument("--latency", type=int, default=500, help="Network latency in ms")
    parser.add_argument("--loss-percent", type=int, default=10, help="Packet loss percentage")
    args = parser.parse_args()

    setup_logging()

    success = False
    if args.fault == "cpu_spike":
        success = inject_cpu_spike(args.service, args.duration)
    elif args.fault == "network_latency":
        success = inject_network_latency(args.service, args.latency)
    elif args.fault == "db_connection_pool":
        success = inject_connection_pool_exhaustion(args.service, args.duration)
    elif args.fault == "pod_kill":
        success = inject_pod_kill(args.service)
    else:
        logger.warning(f"Fault {args.fault} not yet implemented")
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
