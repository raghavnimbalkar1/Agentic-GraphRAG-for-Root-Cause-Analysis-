// =============================================================
// remediation_queries.cypher
//
// The 5 Cypher queries the LangGraph agent runs at runtime.
// This file is documentation + testing reference.
// The actual queries live as strings in graph/graph_client.py.
//
// Test all of these in Neo4j Browser after running init_graph.py.
// =============================================================


// ── Q1: Multi-hop root cause traversal ────────────────────────────────────
//
// Given an alerting service name, walk the DEPENDS_ON graph to its dependencies
// to find the deepest unhealthy upstream node — the root cause.
//
// Parameters: $alert_service (string)
//
// Example: alert_service = 'frontend'
// Returns: root_cause_node='redis-cart', chain=['redis-cart','cartservice','frontend']

MATCH path = (alert:Service {name: $alert_service})-[:DEPENDS_ON*1..8]->(root:Service)
WHERE root.status <> 'HEALTHY'
WITH root,
     reverse([n IN nodes(path) | n.name]) AS dependency_chain,
     length(path)                  AS depth
RETURN root.name        AS root_cause_node,
       dependency_chain  AS dependency_chain,
       depth             AS depth
ORDER BY depth DESC
LIMIT 1;


// ── Q2: Retrieve SOP skill for a root cause node ──────────────────────────
//
// Given a root cause service and its error type, find the matching SOP.
// Excludes already-visited skills to prevent re-execution loops.
//
// Parameters: $root_node (string), $error_type (string), $visited (list[string])

MATCH (svc:Service {name: $root_node})<-[:APPLIES_TO]-(skill:Skill)
WHERE skill.trigger_condition = $error_type
  AND NOT skill.name IN $visited
RETURN skill.name             AS skill_name,
       skill.script_path      AS script_path,
       skill.script_type      AS script_type,
       skill.description      AS description,
       skill.params           AS params,
       skill.timeout_seconds  AS timeout_seconds
LIMIT 1;


// ── Q3: Get next SOP in failure chain ────────────────────────────────────
//
// If the current SOP didn't resolve the issue, follow NEXT_IF_FAIL
// to get the next skill to try.
//
// Parameters: $current_skill (string)

MATCH (current:Skill {name: $current_skill})-[:NEXT_IF_FAIL]->(next:Skill)
RETURN next.name             AS skill_name,
       next.script_path      AS script_path,
       next.script_type      AS script_type,
       next.description      AS description,
       next.params           AS params,
       next.timeout_seconds  AS timeout_seconds;


// ── Q4: Update service health status ────────────────────────────────────
//
// Called by the sandbox executor after script runs, and by telemetry
// collector when it reads container health.
//
// Parameters: $service_name (string), $status (string), $error_code (string|null)

MATCH (s:Service {name: $service_name})
SET s.status       = $status,
    s.error_code   = $error_code,
    s.last_updated = datetime()
RETURN s.name AS updated_service, s.status AS new_status;


// ── Q5: Count unhealthy services in affected chain ───────────────────────
//
// Used by the evaluator node to determine if the loop should terminate.
// Returns 0 when all services in the dependency chain are healthy.
//
// Parameters: $services (list[string])

MATCH (s:Service)
WHERE s.name IN $services
  AND s.status <> 'HEALTHY'
RETURN count(s) AS still_unhealthy,
       collect(s.name) AS unhealthy_services;


// ── BONUS: Full graph health overview ────────────────────────────────────
//
// Useful for the dashboard and debugging. Run manually.

MATCH (s:Service)
RETURN s.name         AS service,
       s.service_type AS type,
       s.status       AS status,
       s.error_code   AS error_code
ORDER BY s.service_type, s.name;


// ── BONUS: Visualise full dependency graph ────────────────────────────────
//
// Run in Neo4j Browser to see the full topology.

MATCH (a:Service)-[r:DEPENDS_ON]->(b:Service)
RETURN a, r, b;


// ── BONUS: Visualise full skill graph ────────────────────────────────────

MATCH (k:Skill)-[r]->(n)
RETURN k, r, n;
