"""
Module B: Neo4j Semantic Skill Graph

Phase 1 (CURRENT): Initialize Neo4j instance, define schema, validate connectivity.

Phase 2: Populate graph from telemetry (Module A) and SOP library.
- ServiceEntity nodes with DEPENDS_ON edges (service topology)
- RemediationSOP nodes with REMEDIATED_BY edges (SOPs linked to failure modes)
- Database nodes, Pod nodes for fine-grained reasoning

Responsibilities:
1. Graph Connectivity: Connect to Neo4j, validate credentials
2. Schema Definition: Create constraints and indexes for performance
3. Graph Population: Ingest telemetry and SOPs (Phase 2+)
4. Query Interface: Efficient multi-hop Cypher queries for agent reasoning

Files:
- graph_client.py: Neo4j connection pool, basic query executor
- schema_definitions.py: Constraint and index definitions
- graph_populator.py: Ingest telemetry and SOP data
- scripts/init_graph.py: One-time schema initialization
- scripts/load_sops.py: Load remediation SOP library from YAML/JSON
- cypher/: Pre-written Cypher queries for common patterns
"""

__version__ = "0.1.0"
