"""
Load remediation SOP library into Neo4j graph.

Phase 2: Load SOP definitions from YAML/JSON file.

Usage:
    python -m module_b_graph_database.scripts.load_sops \
      --input sop_library.yaml --validate
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from module_b_graph_database.graph_client import Neo4jClient
from module_b_graph_database.graph_populator import GraphPopulator
from core.logging_config import get_logger, setup_logging
from core.schemas import RemediationSOP

logger = get_logger(__name__)


def load_sop_library(file_path: str) -> list:
    """Load SOP definitions from YAML or JSON file."""
    path = Path(file_path)

    if not path.exists():
        logger.error(f"File not found: {file_path}")
        return []

    try:
        if path.suffix in [".yaml", ".yml"]:
            with open(path) as f:
                data = yaml.safe_load(f)
        else:
            with open(path) as f:
                data = json.load(f)

        sops = [RemediationSOP(**sop) for sop in data.get("sops", [])]
        logger.info(f"Loaded {len(sops)} SOPs from {file_path}")
        return sops

    except Exception as e:
        logger.error(f"Failed to load SOP library: {e}")
        return []


def main():
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Load remediation SOP library")
    parser.add_argument("--input", required=True, help="SOP library file (YAML or JSON)")
    parser.add_argument("--validate", action="store_true", help="Validate SOPs before loading")
    args = parser.parse_args()

    setup_logging()

    try:
        # Load SOPs from file
        sops = load_sop_library(args.input)
        if not sops:
            logger.error("No SOPs loaded")
            sys.exit(1)

        # Validate if requested
        if args.validate:
            logger.info(f"Validating {len(sops)} SOPs...")
            # All SOPs validated by Pydantic during instantiation

        # Load into graph
        client = Neo4jClient()
        populator = GraphPopulator(client)
        loaded = populator.load_sop_library(sops)

        logger.info(f"Successfully loaded {loaded}/{len(sops)} SOPs into Neo4j")
        client.close()

        sys.exit(0 if loaded == len(sops) else 1)

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
