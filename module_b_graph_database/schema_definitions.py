"""
Neo4j schema definitions: constraints, indexes, and merge helpers.

Phase 1: Define the schema structure.
Phase 2+: Execute during graph initialization.
"""

# Schema Constraints (ensure data integrity)
CONSTRAINTS = [
    "CREATE CONSTRAINT service_id_unique IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT pod_id_unique IF NOT EXISTS FOR (p:Pod) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT database_id_unique IF NOT EXISTS FOR (d:Database) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT sop_id_unique IF NOT EXISTS FOR (sop:RemediationSOP) REQUIRE sop.id IS UNIQUE",
]

# Schema Indexes (query performance)
INDEXES = [
    "CREATE INDEX service_name IF NOT EXISTS FOR (s:Service) ON (s.name)",
    "CREATE INDEX service_status IF NOT EXISTS FOR (s:Service) ON (s.status)",
    "CREATE INDEX pod_service_id IF NOT EXISTS FOR (p:Pod) ON (p.service_id)",
    "CREATE INDEX sop_category IF NOT EXISTS FOR (sop:RemediationSOP) ON (sop.category)",
    "CREATE INDEX sop_risk_level IF NOT EXISTS FOR (sop:RemediationSOP) ON (sop.risk_level)",
]

# Schema Definition Queries
SERVICE_NODE_QUERY = """
MERGE (s:Service {id: $id})
SET s.name = $name,
    s.namespace = $namespace,
    s.image = $image,
    s.replicas = $replicas,
    s.status = $status,
    s.updated_at = timestamp()
"""

POD_NODE_QUERY = """
MERGE (p:Pod {id: $id})
SET p.service_id = $service_id,
    p.ip_address = $ip_address,
    p.status = $status,
    p.cpu_percent = $cpu_percent,
    p.memory_mb = $memory_mb,
    p.updated_at = timestamp()
"""

REMEDIATION_SOP_QUERY = """
MERGE (sop:RemediationSOP {id: $id})
SET sop.name = $name,
    sop.description = $description,
    sop.category = $category,
    sop.script = $script,
    sop.validation_queries = $validation_queries,
    sop.rollback_script = $rollback_script,
    sop.risk_level = $risk_level,
    sop.estimated_duration_sec = $estimated_duration_sec,
    sop.created_at = timestamp()
"""

DEPENDENCY_EDGE_QUERY = """
MATCH (s1:Service {id: $source_id}), (s2:Service {id: $target_id})
MERGE (s1)-[r:DEPENDS_ON]->(s2)
SET r.created_at = timestamp()
"""

REMEDIATION_EDGE_QUERY = """
MATCH (s:Service {id: $service_id}), (sop:RemediationSOP {id: $sop_id})
MERGE (sop)-[r:REMEDIATED_BY]->(s)
SET r.created_at = timestamp()
"""

# Utility queries for schema inspection
LIST_NODES = "CALL db.labels() YIELD label RETURN label"
LIST_RELATIONSHIPS = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
SCHEMA_INFO = "CALL apoc.schema.properties.overview() YIELD label, properties RETURN label, properties"
