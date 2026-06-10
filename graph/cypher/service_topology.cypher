// =============================================================
// service_topology.cypher
//
// Creates the full Online Boutique dual-graph in Neo4j:
//   - Infrastructure Knowledge Graph (Service nodes + DEPENDS_ON edges)
//   - Semantic Skill Graph (Skill nodes + APPLIES_TO + NEXT_IF_FAIL edges)
//
// Run via: graph/scripts/init_graph.py
// Idempotent: uses MERGE, safe to re-run.
//
// Online Boutique source:
//   https://github.com/GoogleCloudPlatform/microservices-demo
// =============================================================


// ── Indexes (create first for performance) ─────────────────────────────────

CREATE INDEX service_name IF NOT EXISTS FOR (s:Service) ON (s.name);
CREATE INDEX skill_name   IF NOT EXISTS FOR (k:Skill)   ON (k.name);
CREATE INDEX skill_trigger IF NOT EXISTS FOR (k:Skill)  ON (k.trigger_condition);


// =============================================================
// GRAPH 1: INFRASTRUCTURE KNOWLEDGE GRAPH
// Node type: Service
// =============================================================

// ── Frontend ──────────────────────────────────────────────────
MERGE (fe:Service {name: 'frontend'})
SET fe.service_type  = 'frontend',
    fe.port          = 8080,
    fe.language      = 'Go',
    fe.description   = 'Serves the web UI; fan-out to nearly all backend services',
    fe.status        = 'HEALTHY',
    fe.error_code    = null,
    fe.last_updated  = datetime();

// ── Checkout Service ──────────────────────────────────────────
MERGE (co:Service {name: 'checkoutservice'})
SET co.service_type  = 'api',
    co.port          = 5050,
    co.language      = 'Go',
    co.description   = 'Orchestrates the full checkout flow',
    co.status        = 'HEALTHY',
    co.error_code    = null,
    co.last_updated  = datetime();

// ── Cart Service ──────────────────────────────────────────────
MERGE (ca:Service {name: 'cartservice'})
SET ca.service_type  = 'api',
    ca.port          = 7070,
    ca.language      = 'C#',
    ca.description   = 'Manages user shopping carts; backed by Redis',
    ca.status        = 'HEALTHY',
    ca.error_code    = null,
    ca.last_updated  = datetime();

// ── Product Catalog Service ───────────────────────────────────
MERGE (pc:Service {name: 'productcatalogservice'})
SET pc.service_type  = 'api',
    pc.port          = 3550,
    pc.language      = 'Go',
    pc.description   = 'Serves product listings and details',
    pc.status        = 'HEALTHY',
    pc.error_code    = null,
    pc.last_updated  = datetime();

// ── Currency Service ──────────────────────────────────────────
MERGE (cu:Service {name: 'currencyservice'})
SET cu.service_type  = 'api',
    cu.port          = 7000,
    cu.language      = 'Node.js',
    cu.description   = 'Converts prices between currencies',
    cu.status        = 'HEALTHY',
    cu.error_code    = null,
    cu.last_updated  = datetime();

// ── Payment Service ───────────────────────────────────────────
MERGE (pa:Service {name: 'paymentservice'})
SET pa.service_type  = 'api',
    pa.port          = 50051,
    pa.language      = 'Node.js',
    pa.description   = 'Processes payment transactions',
    pa.status        = 'HEALTHY',
    pa.error_code    = null,
    pa.last_updated  = datetime();

// ── Shipping Service ──────────────────────────────────────────
MERGE (sh:Service {name: 'shippingservice'})
SET sh.service_type  = 'api',
    sh.port          = 50051,
    sh.language      = 'Go',
    sh.description   = 'Calculates and executes shipping',
    sh.status        = 'HEALTHY',
    sh.error_code    = null,
    sh.last_updated  = datetime();

// ── Email Service ─────────────────────────────────────────────
MERGE (em:Service {name: 'emailservice'})
SET em.service_type  = 'api',
    em.port          = 5000,
    em.language      = 'Python',
    em.description   = 'Sends order confirmation emails',
    em.status        = 'HEALTHY',
    em.error_code    = null,
    em.last_updated  = datetime();

// ── Recommendation Service ────────────────────────────────────
MERGE (re:Service {name: 'recommendationservice'})
SET re.service_type  = 'api',
    re.port          = 8080,
    re.language      = 'Python',
    re.description   = 'Returns product recommendations',
    re.status        = 'HEALTHY',
    re.error_code    = null,
    re.last_updated  = datetime();

// ── Ad Service ────────────────────────────────────────────────
MERGE (ad:Service {name: 'adservice'})
SET ad.service_type  = 'api',
    ad.port          = 9555,
    ad.language      = 'Java',
    ad.description   = 'Serves contextual advertisements',
    ad.status        = 'HEALTHY',
    ad.error_code    = null,
    ad.last_updated  = datetime();

// ── Redis Cart ────────────────────────────────────────────────
MERGE (rc:Service {name: 'redis-cart'})
SET rc.service_type  = 'cache',
    rc.port          = 6379,
    rc.language      = 'Redis',
    rc.description   = 'In-memory store for cart data',
    rc.status        = 'HEALTHY',
    rc.error_code    = null,
    rc.last_updated  = datetime();

// ── Load Generator ────────────────────────────────────────────
MERGE (lg:Service {name: 'loadgenerator'})
SET lg.service_type  = 'load_generator',
    lg.port          = 0,
    lg.language      = 'Python',
    lg.description   = 'Locust-based synthetic load generator',
    lg.status        = 'HEALTHY',
    lg.error_code    = null,
    lg.last_updated  = datetime();


// =============================================================
// DEPENDENCY EDGES (DEPENDS_ON)
// Read as: (A)-[:DEPENDS_ON]->(B)  means "A fails if B fails"
// =============================================================

// loadgenerator → frontend
MATCH (a:Service {name:'loadgenerator'}), (b:Service {name:'frontend'})
MERGE (a)-[:DEPENDS_ON {criticality:'LOW', timeout_ms:30000}]->(b);

// frontend → checkoutservice
MATCH (a:Service {name:'frontend'}), (b:Service {name:'checkoutservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:10000}]->(b);

// frontend → cartservice
MATCH (a:Service {name:'frontend'}), (b:Service {name:'cartservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:5000}]->(b);

// frontend → productcatalogservice
MATCH (a:Service {name:'frontend'}), (b:Service {name:'productcatalogservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:5000}]->(b);

// frontend → currencyservice
MATCH (a:Service {name:'frontend'}), (b:Service {name:'currencyservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'MEDIUM', timeout_ms:3000}]->(b);

// frontend → shippingservice (for shipping quotes on product pages)
MATCH (a:Service {name:'frontend'}), (b:Service {name:'shippingservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'MEDIUM', timeout_ms:3000}]->(b);

// frontend → recommendationservice
MATCH (a:Service {name:'frontend'}), (b:Service {name:'recommendationservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'LOW', timeout_ms:3000}]->(b);

// frontend → adservice
MATCH (a:Service {name:'frontend'}), (b:Service {name:'adservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'LOW', timeout_ms:2000}]->(b);

// checkoutservice → cartservice
MATCH (a:Service {name:'checkoutservice'}), (b:Service {name:'cartservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'CRITICAL', timeout_ms:5000}]->(b);

// checkoutservice → productcatalogservice
MATCH (a:Service {name:'checkoutservice'}), (b:Service {name:'productcatalogservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:5000}]->(b);

// checkoutservice → currencyservice
MATCH (a:Service {name:'checkoutservice'}), (b:Service {name:'currencyservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:3000}]->(b);

// checkoutservice → paymentservice
MATCH (a:Service {name:'checkoutservice'}), (b:Service {name:'paymentservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'CRITICAL', timeout_ms:10000}]->(b);

// checkoutservice → shippingservice
MATCH (a:Service {name:'checkoutservice'}), (b:Service {name:'shippingservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:5000}]->(b);

// checkoutservice → emailservice
MATCH (a:Service {name:'checkoutservice'}), (b:Service {name:'emailservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'LOW', timeout_ms:3000}]->(b);

// cartservice → redis-cart  (CRITICAL: cart data lives here)
MATCH (a:Service {name:'cartservice'}), (b:Service {name:'redis-cart'})
MERGE (a)-[:DEPENDS_ON {criticality:'CRITICAL', timeout_ms:1000}]->(b);

// recommendationservice → productcatalogservice
MATCH (a:Service {name:'recommendationservice'}), (b:Service {name:'productcatalogservice'})
MERGE (a)-[:DEPENDS_ON {criticality:'HIGH', timeout_ms:3000}]->(b);


// =============================================================
// GRAPH 2: SEMANTIC SKILL GRAPH
// Node type: Skill (SOP)
// =============================================================

MERGE (sk1:Skill {name: 'Redis_Flush_SOP'})
SET sk1.script_path       = '/sops/redis/cache_flush.sh',
    sk1.script_type       = 'bash',
    sk1.description       = 'Flushes all keys from Redis to clear stale cart data',
    sk1.trigger_condition = 'STALE_DATA',
    sk1.timeout_seconds   = 20,
    sk1.risk_level        = 'LOW',
    sk1.params            = ['REDIS_HOST', 'REDIS_PORT'];

MERGE (sk2:Skill {name: 'Redis_Restart_SOP'})
SET sk2.script_path       = '/sops/redis/restart.sh',
    sk2.script_type       = 'bash',
    sk2.description       = 'Restarts the Redis container after OOM or connection failure',
    sk2.trigger_condition = 'OOM_KILLED',
    sk2.timeout_seconds   = 30,
    sk2.risk_level        = 'MEDIUM',
    sk2.params            = ['CONTAINER_NAME'];

MERGE (sk3:Skill {name: 'Cart_Restart_SOP'})
SET sk3.script_path       = '/sops/container/restart.sh',
    sk3.script_type       = 'bash',
    sk3.description       = 'Restarts the cartservice container',
    sk3.trigger_condition = 'CONNECTION_REFUSED',
    sk3.timeout_seconds   = 30,
    sk3.risk_level        = 'LOW',
    sk3.params            = ['CONTAINER_NAME'];

MERGE (sk4:Skill {name: 'Payment_Restart_SOP'})
SET sk4.script_path       = '/sops/container/restart.sh',
    sk4.script_type       = 'bash',
    sk4.description       = 'Restarts the paymentservice container',
    sk4.trigger_condition = 'CONNECTION_REFUSED',
    sk4.timeout_seconds   = 30,
    sk4.risk_level        = 'LOW',
    sk4.params            = ['CONTAINER_NAME'];

MERGE (sk5:Skill {name: 'ProductCatalog_Restart_SOP'})
SET sk5.script_path       = '/sops/container/restart.sh',
    sk5.script_type       = 'bash',
    sk5.description       = 'Restarts productcatalogservice after crash loop',
    sk5.trigger_condition = 'CRASH_LOOPING',
    sk5.timeout_seconds   = 30,
    sk5.risk_level        = 'LOW',
    sk5.params            = ['CONTAINER_NAME'];

MERGE (sk6:Skill {name: 'Checkout_Restart_SOP'})
SET sk6.script_path       = '/sops/container/restart.sh',
    sk6.script_type       = 'bash',
    sk6.description       = 'Restarts checkoutservice after degradation',
    sk6.trigger_condition = 'DEGRADED',
    sk6.timeout_seconds   = 30,
    sk6.risk_level        = 'LOW',
    sk6.params            = ['CONTAINER_NAME'];

MERGE (sk7:Skill {name: 'Frontend_Restart_SOP'})
SET sk7.script_path       = '/sops/container/restart.sh',
    sk7.script_type       = 'bash',
    sk7.description       = 'Restarts the frontend container',
    sk7.trigger_condition = 'DEGRADED',
    sk7.timeout_seconds   = 30,
    sk7.risk_level        = 'LOW',
    sk7.params            = ['CONTAINER_NAME'];

MERGE (sk8:Skill {name: 'AdService_CPU_Throttle_SOP'})
SET sk8.script_path       = '/sops/container/restart.sh',
    sk8.script_type       = 'bash',
    sk8.description       = 'Restarts adservice to relieve CPU spike',
    sk8.trigger_condition = 'HIGH_CPU',
    sk8.timeout_seconds   = 30,
    sk8.risk_level        = 'MEDIUM',
    sk8.params            = ['CONTAINER_NAME'];

MERGE (sk9:Skill {name: 'Generic_Restart_SOP'})
SET sk9.script_path       = '/sops/container/restart.sh',
    sk9.script_type       = 'bash',
    sk9.description       = 'Generic container restart for crash-looping services',
    sk9.trigger_condition = 'CRASH_LOOPING',
    sk9.timeout_seconds   = 30,
    sk9.risk_level        = 'LOW',
    sk9.params            = ['CONTAINER_NAME'];


// =============================================================
// SKILL → SERVICE  (APPLIES_TO edges)
// =============================================================

MATCH (sk:Skill {name:'Redis_Flush_SOP'}),    (s:Service {name:'redis-cart'})         MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Redis_Restart_SOP'}),  (s:Service {name:'redis-cart'})         MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Cart_Restart_SOP'}),   (s:Service {name:'cartservice'})        MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Payment_Restart_SOP'}),(s:Service {name:'paymentservice'})     MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'ProductCatalog_Restart_SOP'}),(s:Service {name:'productcatalogservice'}) MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Checkout_Restart_SOP'}),(s:Service {name:'checkoutservice'})  MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Frontend_Restart_SOP'}),(s:Service {name:'frontend'})         MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'AdService_CPU_Throttle_SOP'}),(s:Service {name:'adservice'})  MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Generic_Restart_SOP'}),(s:Service {name:'currencyservice'})   MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Generic_Restart_SOP'}),(s:Service {name:'emailservice'})      MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Generic_Restart_SOP'}),(s:Service {name:'shippingservice'})   MERGE (sk)-[:APPLIES_TO]->(s);
MATCH (sk:Skill {name:'Generic_Restart_SOP'}),(s:Service {name:'recommendationservice'}) MERGE (sk)-[:APPLIES_TO]->(s);


// =============================================================
// SKILL → SKILL  (NEXT_IF_FAIL edges)
// If the first SOP doesn't resolve the issue, try the next one.
// =============================================================

MATCH (a:Skill {name:'Redis_Flush_SOP'}),   (b:Skill {name:'Redis_Restart_SOP'})  MERGE (a)-[:NEXT_IF_FAIL]->(b);
MATCH (a:Skill {name:'Redis_Restart_SOP'}), (b:Skill {name:'Cart_Restart_SOP'})   MERGE (a)-[:NEXT_IF_FAIL]->(b);
MATCH (a:Skill {name:'Cart_Restart_SOP'}),  (b:Skill {name:'Redis_Flush_SOP'})    MERGE (a)-[:NEXT_IF_FAIL]->(b);
MATCH (a:Skill {name:'Checkout_Restart_SOP'}),(b:Skill {name:'Cart_Restart_SOP'}) MERGE (a)-[:NEXT_IF_FAIL]->(b);
