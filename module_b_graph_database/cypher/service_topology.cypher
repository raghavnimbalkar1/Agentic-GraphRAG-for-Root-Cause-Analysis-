// Pre-written Cypher queries for service topology analysis
// Used by agent during reasoning phase to understand blast radius

// Query 1: Find all direct and indirect dependencies of a service
MATCH (service:Service {name: $service_name})
OPTIONAL MATCH path = (service)<-[:DEPENDS_ON*1..]-(dependent:Service)
RETURN DISTINCT dependent.name as service, LENGTH(path) as hops
ORDER BY hops DESC;

// Query 2: Identify critical services (highly depended upon)
MATCH (critical:Service)<-[:DEPENDS_ON]-(dependent:Service)
WITH critical, COUNT(dependent) as dependency_count
RETURN critical.name, dependency_count
ORDER BY dependency_count DESC
LIMIT 10;

// Query 3: Find isolated services (no dependencies)
MATCH (s:Service)
WHERE NOT (s)-[:DEPENDS_ON]->()
RETURN s.name;

// Query 4: Compute transitive closure (all reachable services)
MATCH path = (root:Service {name: $service_name})<-[:DEPENDS_ON*]-(leaf:Service)
RETURN DISTINCT leaf.name
ORDER BY leaf.name;
