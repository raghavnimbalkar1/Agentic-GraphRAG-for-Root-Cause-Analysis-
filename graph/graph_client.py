"""
Neo4j graph database client and connection management.

Phase 1: Establish connectivity, execute basic queries.
Phase 2+: Query caching, connection pooling optimization, result streaming.

Usage:
    from module_b_graph_database.graph_client import Neo4jClient
    client = Neo4jClient()
    results = client.execute_read_query("MATCH (s:Service) RETURN s")
"""

from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Session

from core.config import get_config
from core.exceptions import Neo4jError, GraphQueryError
from core.logging_config import get_logger

logger = get_logger(__name__)


class Neo4jClient:
    """Manages Neo4j connection pool and query execution."""

    def __init__(self):
        """Initialize Neo4j client with connection pool."""
        config = get_config()
        self.uri = config.neo4j_uri
        self.user = config.neo4j_user
        self.password = config.neo4j_password

        try:
            self.driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                max_connection_lifetime=3600,  # 1 hour
            )
            # Test connectivity
            with self.driver.session() as session:
                session.run("RETURN 1")
            logger.info(f"Connected to Neo4j at {self.uri}")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise Neo4jError(f"Neo4j connection failed: {e}")

    def close(self) -> None:
        """Close the connection pool."""
        if self.driver:
            self.driver.close()

    def execute_read_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a read-only Cypher query.

        Args:
            query: Parameterized Cypher query (use $param syntax)
            parameters: Query parameter dictionary

        Returns:
            List of result records as dictionaries
        """
        if parameters is None:
            parameters = {}

        try:
            with self.driver.session() as session:
                result = session.run(query, parameters)
                return [record.data() for record in result]
        except Exception as e:
            logger.error(f"Query failed: {e}\nQuery: {query}")
            raise GraphQueryError(f"Cypher query execution failed: {e}")

    def execute_write_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Execute a write query (CREATE, UPDATE, DELETE).

        Args:
            query: Parameterized Cypher query
            parameters: Query parameter dictionary

        Returns:
            Number of nodes/relationships affected
        """
        if parameters is None:
            parameters = {}

        try:
            with self.driver.session() as session:
                result = session.run(query, parameters)
                summary = result.consume()
                affected = (
                    summary.counters.nodes_created
                    + summary.counters.relationships_created
                    + summary.counters.nodes_deleted
                    + summary.counters.relationships_deleted
                )
                logger.info(f"Write query affected {affected} entities")
                return affected
        except Exception as e:
            logger.error(f"Write query failed: {e}\nQuery: {query}")
            raise GraphQueryError(f"Cypher write query failed: {e}")

    def find_blast_radius(self, root_cause_service: str) -> Dict[str, Any]:
        """
        Find all services affected by a root cause service failure.

        Implements multi-hop traversal: Service -[:DEPENDS_ON*1..]-> Service

        Phase 2+: Use graph analysis for more sophisticated blast radius.
        """
        logger.info(f"Computing blast radius for {root_cause_service}")

        query = """
        MATCH (root:Service {name: $service})
        OPTIONAL MATCH path = (root)<-[:DEPENDS_ON*1..]-(affected:Service)
        RETURN DISTINCT affected.name as service, COUNT(path) as distance
        ORDER BY distance DESC
        """

        try:
            results = self.execute_read_query(query, {"service": root_cause_service})
            affected_services = [r["service"] for r in results]
            logger.info(f"Blast radius: {len(affected_services)} services affected")
            return {"root_cause": root_cause_service, "affected": affected_services}
        except Exception as e:
            logger.error(f"Blast radius computation failed: {e}")
            return {"root_cause": root_cause_service, "affected": []}

    def find_applicable_sops(self, failure_mode: str) -> List[Dict[str, Any]]:
        """
        Query the graph for applicable remediation SOPs.

        Searches SOPs by:
        1. Category keyword matching
        2. Applicable services
        3. Risk level (bias toward lower risk)
        """
        logger.info(f"Finding SOPs for failure mode: {failure_mode}")

        query = """
        MATCH (sop:RemediationSOP)
        WHERE sop.name CONTAINS $keyword OR sop.category CONTAINS $keyword
        RETURN sop.id, sop.name, sop.category, sop.risk_level,
               sop.estimated_duration_sec
        ORDER BY sop.risk_level ASC
        LIMIT 10
        """

        try:
            results = self.execute_read_query(query, {"keyword": failure_mode})
            logger.info(f"Found {len(results)} applicable SOPs")
            return results
        except Exception as e:
            logger.error(f"SOP lookup failed: {e}")
            return []

    def create_service_node(self, service_data: Dict[str, Any]) -> bool:
        """
        Create or update a Service node in the graph.

        Phase 2: Called by graph populator during telemetry ingestion.
        """
        query = """
        MERGE (s:Service {id: $id})
        SET s.name = $name,
            s.namespace = $namespace,
            s.status = $status,
            s.image = $image,
            s.updated_at = timestamp()
        """

        try:
            self.execute_write_query(query, service_data)
            logger.debug(f"Created/updated service node: {service_data.get('id')}")
            return True
        except Exception as e:
            logger.error(f"Failed to create service node: {e}")
            return False

    def create_dependency_edge(self, source_service: str, target_service: str) -> bool:
        """
        Create a DEPENDS_ON edge between two services.

        Phase 2: Called during dependency inference.
        """
        query = """
        MATCH (s1:Service {id: $source}), (s2:Service {id: $target})
        MERGE (s1)-[r:DEPENDS_ON]->(s2)
        SET r.created_at = timestamp()
        """

        try:
            self.execute_write_query(query, {"source": source_service, "target": target_service})
            logger.debug(f"Created dependency edge: {source_service} -> {target_service}")
            return True
        except Exception as e:
            logger.error(f"Failed to create dependency edge: {e}")
            return False
