# Module B: Neo4j Semantic Skill Graph

## Overview

A production-grade Neo4j knowledge graph that maps:
- **Service Topology**: Microservices, dependencies, communication patterns
- **Infrastructure**: Pods, nodes, databases, external services
- **Remediation SOPs**: Executable scripts linked to failure modes and services

This graph is the **brain's reasoning engine** — the agent queries it using multi-hop Cypher
to identify blast radius and retrieve matching remediation scripts.

## Phase 1 Goals

yes Initialize Neo4j instance (standalone or Docker)  
yes Define schema: constraints, indexes, property keys  
yes Validate Neo4j connectivity from Python  
yes Load sample SOP library (15 production-ready scripts)  
yes Run test queries to verify schema integrity  

## Phase 2 Goals (Next)

- Auto-populate from Module A telemetry
- Service dependency inference from gRPC/HTTP traces
- Dynamic SOP recommendation based on failure signatures

## Schema Overview

### Entity Nodes

```
Service {
  id: str (unique)
  name: str
  namespace: str
  image: str
  replicas: int
  status: "running|pending|failed|unknown"
  owner_team: str
}

Pod {
  id: str (unique)
  service_id: str
  ip_address: str
  status: "running|pending|failed"
  cpu_percent: float
  memory_mb: float
}

Database {
  id: str (unique)
  service: str (owner)
  db_type: "postgres|mysql|redis|mongodb"
  connection_limit: int
  active_connections: int
}

RemediationSOP {
  id: str (unique)
  name: str
  description: str
  category: str
  script: str (Python code)
  risk_level: "low|medium|high"
  estimated_duration_sec: int
}
```

### Relationship Types

```
Service -[:DEPENDS_ON]-> Service
Service -[:HOSTED_ON]-> Pod
Pod -[:RUNS_ON]-> Node
Service -[:USES]-> Database
RemediationSOP -[:REMEDIATED_BY]-> Service
RemediationSOP -[:REQUIRES_VALIDATION]-> ValidatorQuery
```

## Setup

### 1. Start Neo4j

**Option A: Docker Compose (included at project root)**
```bash
docker-compose up neo4j
# Access UI at http://localhost:7474
# Default: neo4j / your_secure_password
```

**Option B: Standalone Installation**
```bash
# macOS
brew install neo4j

# Or download from https://neo4j.com/download-center/
neo4j console
```

### 2. Initialize Graph Schema

```bash
python -m module_b_graph_database.scripts.init_graph
```

This creates:
- Unique constraints on entity IDs
- Indexes on frequently-queried properties (service name, status)
- Index on SOP categories for fast filtering

### 3. Load Sample SOP Library

```bash
python -m module_b_graph_database.scripts.load_sops \
  --input module_b_graph_database/sop_library.yaml \
  --validate
```

## Querying the Graph

### Python API

```python
from module_b_graph_database.graph_client import Neo4jClient

client = Neo4jClient()

# Query all services
services = client.execute_read_query(
    "MATCH (s:Service) RETURN s.name, s.status"
)

# Multi-hop: Find blast radius
blast_radius = client.find_blast_radius("frontend")

# Find applicable remediation SOPs
sops = client.find_applicable_sops("database_connection_pool_exhaustion")
```

### Cypher Shell

```bash
cypher-shell -u neo4j -p your_password
```

```cypher
# Find all services that the frontend depends on
MATCH (frontend:Service {name: "frontend"})-[:DEPENDS_ON*1..3]->(dep:Service)
RETURN DISTINCT dep.name;

# Find SOPs for fixing connection pool issues
MATCH (sop:RemediationSOP {category: "database"})
WHERE sop.name CONTAINS "connection"
RETURN sop.name, sop.risk_level, sop.estimated_duration_sec;
```

## Project Structure

```
module_b_graph_database/
├── __init__.py
├── README.md (this file)
├── graph_client.py           # Neo4j connection pool, executors
├── schema_definitions.py      # Constraints, indexes, merge helpers
├── graph_populator.py         # Ingest telemetry → graph
├── sop_library.yaml          # Sample 15+ remediation SOPs
├── scripts/
│   ├── init_graph.py         # One-time: create schema
│   └── load_sops.py          # Load SOPs from YAML/JSON
└── cypher/
    ├── service_topology.cypher     # Service dependency queries
    ├── remediation_queries.cypher  # SOP recommendation
    └── blast_radius.cypher         # Failure propagation
```

## SOP Catalog Structure

Each SOP node contains:

```yaml
- id: "sop-db-pool-reset"
  name: "Database Connection Pool Reset"
  category: "database"
  applicable_services:
    - "cartservice"
    - "checkout"
  preconditions:
    - "connection_count > connection_limit * 0.9"
    - "queries_pending > 100"
  remediation_script: |
    #!/usr/bin/env python
    # Reset stale connections
    import psycopg2
    # ... (actual reset logic)
  validation_queries:
    - "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'"
  rollback_script: |
    # Restore previous pool configuration
  risk_level: "medium"
  estimated_duration_sec: 45
```

## Next Steps (Phase 2)

1. **Populate from Telemetry**: Module A → Module B
   - Ingest container logs, parse service names
   - Infer dependencies from network traffic
   - Auto-create Service/Pod/Database nodes

2. **Query Expansion**: Add more complex multi-hop queries
   - Longest path to root cause
   - High-risk SOP avoidance
   - Service-specific SOP tailoring

3. **Performance Tuning**: Neo4j profile and optimization
   - Index validation
   - Query plan analysis
   - Cache warming

---

Dependencies: Neo4j, neo4j-driver, Pydantic  
Author: AIOps Research Team  
