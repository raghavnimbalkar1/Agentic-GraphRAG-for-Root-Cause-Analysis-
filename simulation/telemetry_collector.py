"""
Real-time telemetry collection from running cluster.

Phase 1: Stream container stdout/stderr, system metrics, and traces.
Phase 2+: Parse, normalize, and push to Neo4j for correlation.

Output:
- Console: Real-time log streaming (development)
- Files: JSONL format in telemetry_data/ (archive + replay)
- Neo4j: TelemetryEvent nodes and edges (Phase 2+)
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from core.config import get_config
from core.logging_config import get_logger, setup_logging
from core.schemas import TelemetryEvent

logger = get_logger(__name__)


def stream_docker_logs(service_name: str) -> Generator[str, None, None]:
    """Stream logs from a Docker Compose service."""
    logger.info(f"Streaming logs from service: {service_name}")

    # Stub: Use docker-compose logs --follow
    try:
        process = subprocess.Popen(
            ["docker-compose", "logs", "-f", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            yield line.strip()
    except Exception as e:
        logger.error(f"Error streaming logs: {e}")


def stream_all_services() -> Generator[TelemetryEvent, None, None]:
    """Stream logs from all services and emit TelemetryEvent objects."""
    # Stub: In Phase 2, this will parse structured logs and emit TelemetryEvent
    logger.info("Streaming from all services...")


def save_telemetry_to_file(output_path: str) -> None:
    """Stream telemetry events and save to JSONL file."""
    logger.info(f"Saving telemetry to {output_path}")

    output_dir = Path("telemetry_data")
    output_dir.mkdir(exist_ok=True)

    # Stub: Implement file writing
    pass


def main():
    """CLI entrypoint for telemetry collection."""
    parser = argparse.ArgumentParser(description="Telemetry collection")
    parser.add_argument("--service", default="all", help="Service name or 'all'")
    parser.add_argument("--output", default=None, help="Output file path (optional)")
    parser.add_argument("--duration", type=int, default=None, help="Collection duration in seconds")
    args = parser.parse_args()

    setup_logging()

    if args.output:
        save_telemetry_to_file(args.output)
    else:
        # Stream to console
        if args.service == "all":
            stream_all_services()
        else:
            for line in stream_docker_logs(args.service):
                print(line)


if __name__ == "__main__":
    main()
