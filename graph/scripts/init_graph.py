"""
Initialize Neo4j schema: constraints, indexes, and initial data.

Phase 1: Run once to set up database.

Usage:
    python -m module_b_graph_database.scripts.init_graph
"""

import sys

from module_b_graph_database.graph_client import Neo4jClient
from module_b_graph_database.schema_definitions import CONSTRAINTS, INDEXES
from core.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


def initialize_schema(client: Neo4jClient) -> bool:
    """Create all constraints and indexes."""
    logger.info("Initializing Neo4j schema...")

    try:
        # Create constraints
        for constraint in CONSTRAINTS:
            logger.info(f"Executing: {constraint[:60]}...")
            client.execute_write_query(constraint)

        # Create indexes
        for index in INDEXES:
            logger.info(f"Executing: {index[:60]}...")
            client.execute_write_query(index)

        logger.info("Schema initialization complete!")
        return True

    except Exception as e:
        logger.error(f"Schema initialization failed: {e}")
        return False


def main():
    """CLI entrypoint."""
    setup_logging()

    try:
        client = Neo4jClient()
        success = initialize_schema(client)
        client.close()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
