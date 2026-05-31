## Neo4j Schema Reference

Complete definition of all node types, relationships, and indexes.

### Node Types

#### Service
Represents a microservice in the cluster.

```cypher
CREATE CONSTRAINT service_id_unique FOR (s:Service) REQUIRE s.id IS UNIQUE;
CREATE INDEX service_name FOR (s:Service) ON (s.name);
CREATE INDEX service_status FOR (s:Service) ON (s.status);

Node Properties:
  id: String (unique, UUID)
  name: String (human-readable, e.g., "frontend", "checkout")
  namespace: String (optional, K8s namespace or Docker service)
  image: String (container image URI)
  replicas: Integer (default 1)
  status: Enum ["running", "pending", "failed", "unknown"]
  owner_team: String (optional)
  updated_at: Timestamp (automatically set)
```

#### Pod
Represents a container instance (replica of a Service).

```cypher
CREATE CONSTRAINT pod_id_unique FOR (p:Pod) REQUIRE p.id IS UNIQUE;
CREATE INDEX pod_service_id FOR (p:Pod) ON (p.service_id);

Node Properties:
  id: String (unique, UUID)
  service_id: String (reference to Service)
  status: Enum ["running", "pending", "failed", "terminating"]
  ip_address: String (optional)
  node: String (optional, K8s node name)
  cpu_percent: Float (optional, 0-100)
  memory_mb: Float (optional, in MB)
  updated_at: Timestamp
```

#### Database
Represents a persistent data store.

```cypher
Node Properties:
  id: String (unique)
  service_id: String (owning service)
  db_type: Enum ["postgres", "mysql", "redis", "mongodb"]
  connection_limit: Integer
  active_connections: Integer
  updated_at: Timestamp
```

#### RemediationSOP
Represents a Standard Operating Procedure (fix script).

```cypher
CREATE CONSTRAINT sop_id_unique FOR (sop:RemediationSOP) REQUIRE sop.id IS UNIQUE;
CREATE INDEX sop_category FOR (sop:RemediationSOP) ON (sop.category);
CREATE INDEX sop_risk_level FOR (sop:RemediationSOP) ON (sop.risk_level);

Node Properties:
  id: String (unique, e.g., "sop-db-pool-reset")
  name: String (human-readable)
  description: String
  category: String (e.g., "database", "network", "compute")
  script: String (Python code, multi-line)
  validation_queries: List[String] (Cypher or SQL)
  rollback_script: String (optional)
  risk_level: Enum ["low", "medium", "high"]
  estimated_duration_sec: Integer
  applicable_services: List[String] (service IDs)
  created_at: Timestamp
```

#### Node (Kubernetes)
Represents a physical or virtual machine.

```cypher
Node Properties:
  id: String (unique, node name)
  status: String ("Ready", "NotReady")
  capacity_cpu: String (e.g., "4")
  capacity_memory_gb: Float
```

### Relationship Types

#### DEPENDS_ON
Service A depends on Service B (calls B's API/RPC).

```cypher
(serviceA:Service)-[:DEPENDS_ON]->(serviceB:Service)

Properties:
  latency_p99_ms: Float (optional, measured)
  error_rate: Float (optional, 0-1)
  created_at: Timestamp
```

#### HOSTED_ON
Pod runs on a Node.

```cypher
(pod:Pod)-[:HOSTED_ON]->(node:Node)
```

#### RUNS_ON
Service has Pods running on Node.

```cypher
(service:Service)-[:RUNS_ON]->(pod:Pod)

(pod:Pod)-[:ON]->(node:Node)
```

#### USES
Service uses a Database.

```cypher
(service:Service)-[:USES]->(database:Database)

Properties:
  connection_pool_size: Integer
  read_write: String ("read", "write", "read-write")
```

#### REMEDIATED_BY
SOP can fix a Service issue.

```cypher
(sop:RemediationSOP)-[:REMEDIATED_BY]->(service:Service)
```

#### APPLICABLE_TO
SOP is applicable to Service.

```cypher
(sop:RemediationSOP)-[:APPLICABLE_TO]->(service:Service)
```

### Indexes (Performance)

```cypher
-- Query-critical indexes (mandatory)
CREATE INDEX service_name FOR (s:Service) ON (s.name);
CREATE INDEX pod_service_id FOR (p:Pod) ON (p.service_id);
CREATE INDEX sop_category FOR (sop:RemediationSOP) ON (sop.category);

-- Optional: Text search indexes
CREATE TEXT INDEX service_description FOR (s:Service) ON (s.description);
CREATE TEXT INDEX sop_description FOR (sop:RemediationSOP) ON (sop.description);
```

### Common Queries

#### Find all services that a service depends on (transitive)
```cypher
MATCH (root:Service {name: $service_name})-[:DEPENDS_ON*1..10]->(dep:Service)
RETURN DISTINCT dep.name ORDER BY dep.name
```

#### Find blast radius (all services that depend on root cause)
```cypher
MATCH (root:Service {name: $service_name})<-[:DEPENDS_ON*1..10]-(affected:Service)
RETURN DISTINCT affected.name
```

#### Find applicable SOPs for a service failure
```cypher
MATCH (sop:RemediationSOP)-[:APPLICABLE_TO]->(service:Service {id: $service_id})
WHERE sop.risk_level = $risk_level OR sop.category = $category
RETURN sop.id, sop.name, sop.estimated_duration_sec
ORDER BY sop.risk_level ASC, sop.estimated_duration_sec ASC
LIMIT 5
```

#### Find critical services (highly depended upon)
```cypher
MATCH (critical:Service)<-[:DEPENDS_ON]-(dependent:Service)
WITH critical, COUNT(dependent) as dep_count
RETURN critical.name, dep_count
ORDER BY dep_count DESC
LIMIT 10
```

#### Show service topology (all services + dependencies)
```cypher
MATCH (s1:Service)-[r:DEPENDS_ON]->(s2:Service)
RETURN s1.name as source, s2.name as target, r.latency_p99_ms as latency
ORDER BY s1.name, s2.name
```

---

**Reference**: Neo4j 5.14+  
**Cypher Version**: 5  
