"""
Graph population from telemetry events and SOP library.

Phase 2: Ingest events from Module A and transform into graph nodes/edges.
Phase 3+: Real-time streaming population during active monitoring.

Responsibilities:
- Parse TelemetryEvent objects into Service/Pod nodes
- Infer dependencies from communication patterns
- Load remediation SOP library
"""

from typing import List, Dict, Any

from core.logging_config import get_logger
from core.schemas import TelemetryEvent, RemediationSOP, ServiceEntity

logger = get_logger(__name__)


class GraphPopulator:
    """Orchestrates population of Neo4j graph from telemetry and SOPs."""

    def __init__(self, neo4j_client):
        """Initialize with Neo4j client."""
        self.client = neo4j_client

    def ingest_telemetry_batch(self, events: List[TelemetryEvent]) -> int:
        """
        Ingest a batch of telemetry events into the graph.

        Phase 2: Extract service topology from logs.
        """
        logger.info(f"Ingesting {len(events)} telemetry events...")
        nodes_created = 0

        for event in events:
            try:
                # Create Service node if not exists
                if event.source_service:
                    self.client.create_service_node({
                        "id": event.source_service,
                        "name": event.source_service,
                        "status": "running",
                    })
                    nodes_created += 1

                # Phase 2+: Parse log for dependent services
                # (e.g., gRPC call to "checkout" -> create edge)

            except Exception as e:
                logger.error(f"Failed to ingest event: {e}")

        logger.info(f"Ingestion complete: {nodes_created} nodes created")
        return nodes_created

    def load_sop_library(self, sops: List[RemediationSOP]) -> int:
        """Load remediation SOP library into graph."""
        logger.info(f"Loading {len(sops)} SOPs into graph...")
        sops_loaded = 0

        for sop in sops:
            try:
                # Create RemediationSOP node
                query = """
                MERGE (sop:RemediationSOP {id: $id})
                SET sop.name = $name,
                    sop.description = $description,
                    sop.category = $category,
                    sop.risk_level = $risk_level
                """
                self.client.execute_write_query(query, {
                    "id": sop.sop_id,
                    "name": sop.name,
                    "description": sop.description,
                    "category": sop.category,
                    "risk_level": sop.risk_level,
                })

                # Link SOP to applicable services
                for service_id in sop.applicable_services:
                    link_query = """
                    MATCH (sop:RemediationSOP {id: $sop_id})
                    MERGE (svc:Service {id: $service_id})
                    MERGE (sop)-[r:APPLICABLE_TO]->(svc)
                    """
                    self.client.execute_write_query(link_query, {
                        "sop_id": sop.sop_id,
                        "service_id": service_id,
                    })

                sops_loaded += 1
            except Exception as e:
                logger.error(f"Failed to load SOP {sop.sop_id}: {e}")

        logger.info(f"SOP library loaded: {sops_loaded} SOPs")
        return sops_loaded

    def infer_dependencies_from_traces(self, traces: List[Dict[str, Any]]) -> int:
        """
        Infer service dependencies from distributed traces.

        Phase 2: Parse trace spans to identify service calls.
        """
        logger.info("Inferring service dependencies from traces...")
        # Stub: Implement trace parsing and edge creation
        return 0
