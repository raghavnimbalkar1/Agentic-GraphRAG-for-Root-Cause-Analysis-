// Pre-written Cypher queries for remediation SOP recommendation
// Used by agent to find applicable fixes for failure scenarios

// Query 1: Find SOPs matching a failure category
MATCH (sop:RemediationSOP {category: $failure_category})
RETURN sop.id, sop.name, sop.risk_level, sop.estimated_duration_sec
ORDER BY sop.risk_level ASC, sop.estimated_duration_sec ASC
LIMIT 10;

// Query 2: Find SOPs applicable to specific service
MATCH (sop:RemediationSOP)-[:APPLICABLE_TO]->(service:Service {id: $service_id})
RETURN sop.id, sop.name, sop.category, sop.risk_level
ORDER BY sop.risk_level ASC;

// Query 3: Recommend low-risk SOPs for emergency remediation
MATCH (sop:RemediationSOP)
WHERE sop.risk_level = "low"
AND sop.estimated_duration_sec < 120
RETURN sop.id, sop.name, sop.estimated_duration_sec
ORDER BY sop.estimated_duration_sec ASC;

// Query 4: Find related SOPs (same category + service)
MATCH (ref_sop:RemediationSOP {id: $reference_sop_id})
MATCH (related:RemediationSOP)
WHERE related.category = ref_sop.category
AND related.id <> ref_sop.id
RETURN related.id, related.name, related.risk_level;
