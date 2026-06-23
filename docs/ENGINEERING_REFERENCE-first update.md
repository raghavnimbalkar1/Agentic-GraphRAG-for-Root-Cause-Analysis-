# Agentic GraphRAG — Master Engineering Reference

![Status](https://img.shields.io/badge/Status-Phase_3_Complete-green?style=flat-square)
![Domain](https://img.shields.io/badge/Domain-AIOps-blueviolet?style=flat-square)
![Stack](https://img.shields.io/badge/Stack-LangGraph_|_Neo4j_|_Docker-informational?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2.4-orange?style=flat-square)

---

## Table of Contents

1. [Problem Space and Theoretical Foundation](#module-1-problem-space-and-theoretical-foundation)
2. [System Architecture and Data Engineering](#module-2-system-architecture-and-data-engineering)
3. [Technology Stack](#module-3-technology-stack)
4. [Multi-Agent Logic and State Machine](#module-4-multi-agent-logic-and-state-machine)
5. [Security Engineering and Containment](#module-5-security-engineering-and-containment)
6. [Implementation Roadmap](#module-6-implementation-roadmap)
7. [Actual Build Log and Key Decisions](#module-7-actual-build-log-and-key-decisions)
8. [Research Questions](#module-8-research-questions)

---

## Current Progress Snapshot

| Phase | Description | Status | Key Output |
|---|---|---|---|
| Phase 0 | Foundation — venv, deps, core/ module | ✅ Complete | `core/config.py`, `core/schemas.py`, `core/exceptions.py`, `core/logging_config.py` |
| Phase 1 | Docker environment — Neo4j + DinD | ✅ Complete | Neo4j healthy at `localhost:7474`, `bolt://localhost:7687` |
| Phase 2 | Neo4j dual-graph — Online Boutique topology | ✅ Complete | 12 Service nodes, 9 Skill nodes, 16 DEPENDS_ON, 12 APPLIES_TO, 4 NEXT_IF_FAIL edges |
| Phase 3 | Simulation environment — Online Boutique + fault injection | ✅ Complete | 12 containers running, `curl localhost:8080` → 200, 4 fault types verified |
| Phase 4 | LangGraph agent core — ingest → retrieve → reason | ⬜ Next | — |
| Phase 5 | Docker sandbox + SOP scripts | ⬜ Pending | — |
| Phase 6 | End-to-end chaos integration | ⬜ Pending | — |
| Phase 6.5 | Streamlit dashboard | ⬜ Pending | — |
| Phase 7 | Evaluation — RQ1/RQ2/RQ3+RQ4 | ⬜ Pending | — |
| Phase 8 | Report + final presentation | ⬜ Pending | — |

---

## Module 1: Problem Space and Theoretical Foundation

### 1.1 The Architecture of Cascading Failures

In contemporary microservice ecosystems running on container orchestrators like Kubernetes, applications are decomposed into hundreds of loosely coupled, distributed services. While this architecture maximizes horizontal scaling and deployment velocity, it exponentially complicates fault localization.

A fault inside an enterprise system rarely remains contained. It follows a pattern known as a **cascading failure**:

| Stage | Description |
|---|---|
| Root Cause | A performance bottleneck or bug occurs in a downstream dependency — for example, database connection pool depletion or Redis cache eviction failure |
| Blast Radius | Upstream API gateway or orchestrator services experience thread starvation waiting for the downstream response |
| Surface Symptom | The user-facing frontend service throws a generic `504 Gateway Timeout` |

Traditional monitoring systems fire dozens of correlated alerts simultaneously across the full application chain. Human engineers must parse massive volumes of distributed traces and logs under time pressure, inflating Mean Time to Resolution (MTTR).

**Verified example from running system:** In the deployed Online Boutique stack, a `redis-cart` OOM fault propagates as:

```
redis-cart (OOM_KILLED)
  → cartservice (cache errors)
    → checkoutservice (cart unavailable)
      → frontend (503 on checkout)
```

This 3-hop chain was confirmed in Phase 2 smoke tests via Neo4j traversal.

---

### 1.2 Gaps in the State of the Art

An analysis of historical and current AIOps strategies reveals a progression of distinct engineering limitations:

```
[Mathematical Inference Models]  -->  Fragile, uninterpretable numerical outputs
        |
        v
[Static Knowledge Graphs]        -->  Cannot reason or automate fixes autonomously
        |
        v
[Standard LLM RAG]               -->  Hallucinated code, no system topology awareness
        |
        v
[Base Paper: Flow-of-Action]     -->  CRITICAL FLAW: native live-server code execution
```

**Mathematical Causal Inference** (PC Algorithm, Granger Causality): These methods evaluate metric time-series using heavy statistical dependencies. They are computationally expensive — O(N³) or worse — frequently hit memory timeouts in large-scale cluster environments, and output raw numerical statistics with no actionable instructions.

**Static Knowledge Graphs**: Deterministic maps of system topology (e.g., Pod `X` `IS_HOSTED_ON` Node `Y`). Useful for highlighting dependencies, but inherently passive — they lack an active reasoning engine to autonomously traverse paths, cross-reference symptoms with runtime state, or execute remediation steps.

**Standard LLM RAG**: Passing logs directly into text-based LLMs results in severe hallucinations. Text RAG has no topological context; it cannot recognize spatial relationships between microservices, leading to inaccurate diagnostic assumptions and incorrect remediation code.

**The Base Paper Flaw (Flow-of-Action, WWW 2025)**: The state-of-the-art Flow-of-Action architecture introduces Standard Operating Procedures (SOPs) to constrain LLM code generation — a meaningful step forward. However, it retains a critical security flaw: AI-generated diagnostic and repair code is executed natively on the host server. In production infrastructure, this grants an AI model unsandboxed execution privileges, posing an unacceptable risk of catastrophic data loss or unauthorized state mutations.

---

## Module 2: System Architecture and Data Engineering

### 2.1 The Four-Module Unified Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE A: TARGET ENVIRONMENT (Google Online Boutique v0.10.5)          │
│                                                                        │
│  [frontend] ──► [checkoutservice] ──► [cartservice] ──► [redis-cart]  │
│                        └──────────────► [paymentservice]               │
│                        └──────────────► [productcatalogservice]        │
│           ✅ RUNNING: 12 containers, curl localhost:8080 → 200         │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │  (AlertPayload via HTTP POST)
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE C: LANGGRAPH AGENTIC BRAIN                            ⬜ NEXT   │
│                                                                        │
│  [ingest.py] ──► [retriever.py] ──► [reasoner.py] ──► [executor.py]  │
│       └──────────────────────────── [evaluator.py] ◄──────────────────┘
└──────────────▲──────────────────────────────────────┬──────────────────┘
               │                                      │
               │  (GraphRAG Context Lookup)           │  (Secure Tool Call)
               ▼                                      ▼
┌──────────────────────────────────┐   ┌──────────────────────────────────┐
│ MODULE B: NEO4J SKILL GRAPH      │   │ MODULE D: DOCKER SANDBOX         │
│                                  │   │                                  │
│  ✅ 12 Service nodes             │   │  ⬜ sop-executor image           │
│  ✅ 9 Skill (SOP) nodes         │   │  ⬜ OpenClaw integration          │
│  ✅ 16 DEPENDS_ON edges         │   │  ⬜ sops/ scripts written         │
│  ✅ 12 APPLIES_TO edges         │   │                                  │
│  ✅ 4 NEXT_IF_FAIL chains       │   │                                  │
└──────────────────────────────────┘   └──────────────────────────────────┘
```

---

### Module A — Target Cloud Environment

**Decision made during implementation:** Rather than building custom FastAPI simulation services (original plan), the project uses Google Online Boutique v0.10.5 — an open-source, production-realistic microservice benchmark used in AIOps academic papers. This makes results directly comparable to prior art.

**Running stack:**

| Container | Image | Role | Status |
|---|---|---|---|
| `frontend` | online-boutique/frontend:v0.10.5 | Web UI, top of dependency chain | ✅ Running |
| `checkoutservice` | online-boutique/checkoutservice:v0.10.5 | Orchestrates checkout — depends on 6 services | ✅ Running |
| `cartservice` | online-boutique/cartservice:v0.10.5 | Cart state — depends on redis-cart | ✅ Running |
| `productcatalogservice` | online-boutique/productcatalogservice:v0.10.5 | Product listings | ✅ Running |
| `currencyservice` | online-boutique/currencyservice:v0.10.5 | Currency conversion | ✅ Running |
| `paymentservice` | online-boutique/paymentservice:v0.10.5 | Payment processing | ✅ Running |
| `shippingservice` | online-boutique/shippingservice:v0.10.5 | Shipping quotes | ✅ Running |
| `emailservice` | online-boutique/emailservice:v0.10.5 | Confirmation emails | ✅ Running |
| `recommendationservice` | online-boutique/recommendationservice:v0.10.5 | Product recommendations | ✅ Running |
| `adservice` | online-boutique/adservice:v0.10.5 | Ad serving (Java) | ✅ Running |
| `redis-cart` | redis:alpine | Cart session store | ✅ Healthy |
| `loadgenerator` | online-boutique/loadgenerator:v0.10.5 | Synthetic traffic (5 users) | ✅ Running |

**Network:** `boutique-sim` (bridge, internal). Will be bridged to `agent-net` in Phase 4.

**Fault Injection — Verified Scenarios:**

| Fault | Method | Target | Alert Service | Graph Updated | Verified |
|---|---|---|---|---|---|
| `redis_oom` | `CONFIG SET maxmemory 1mb` + fill | `redis-cart` | `frontend` | ✅ | ✅ |
| `service_crash` | `SIGKILL` via Docker SDK | any | upstream dependent | ✅ | ✅ |
| `network_partition` | `network.disconnect()` | any | upstream dependent | ✅ | ✅ |
| `high_latency` | `tc qdisc netem` | any | upstream dependent | ✅ | ⬜ |

---

### Module B — Neo4j Semantic Skill Graph

**Actual graph state (Phase 2 verified):**

```
Node counts:
  Service  → 12   (full Online Boutique topology)
  Skill    →  9   (SOP nodes, Phase 5 will attach real scripts)

Relationship counts:
  DEPENDS_ON   → 16
  APPLIES_TO   → 12
  NEXT_IF_FAIL →  4

Verified traversal (smoke test):
  Alert: frontend (HTTP_TIMEOUT)
  Q1 query result: redis-cart → cartservice → checkoutservice → frontend
  Depth: 3 hops  ✅
```

**Online Boutique DEPENDS_ON edges (actual wiring from env vars):**

```cypher
// Verified against docker-compose.yml environment variable configuration
(cartservice)           -[:DEPENDS_ON]-> (redis-cart)
(checkoutservice)       -[:DEPENDS_ON]-> (emailservice)
(checkoutservice)       -[:DEPENDS_ON]-> (shippingservice)
(checkoutservice)       -[:DEPENDS_ON]-> (paymentservice)
(checkoutservice)       -[:DEPENDS_ON]-> (currencyservice)
(checkoutservice)       -[:DEPENDS_ON]-> (productcatalogservice)
(checkoutservice)       -[:DEPENDS_ON]-> (cartservice)
(frontend)              -[:DEPENDS_ON]-> (adservice)
(frontend)              -[:DEPENDS_ON]-> (recommendationservice)
(frontend)              -[:DEPENDS_ON]-> (shippingservice)
(frontend)              -[:DEPENDS_ON]-> (currencyservice)
(frontend)              -[:DEPENDS_ON]-> (productcatalogservice)
(frontend)              -[:DEPENDS_ON]-> (cartservice)
(frontend)              -[:DEPENDS_ON]-> (checkoutservice)
(loadgenerator)         -[:DEPENDS_ON]-> (frontend)
(recommendationservice) -[:DEPENDS_ON]-> (productcatalogservice)
```

**GraphClient — implemented queries:**

| Method | Query | Used By |
|---|---|---|
| `get_root_cause(alert_service, error_type)` | Q1: multi-hop DEPENDS_ON traversal | Agent: retriever node |
| `get_skill(root_node, error_type, visited)` | Q2: APPLIES_TO skill lookup | Agent: retriever node |
| `get_next_skill(current_skill)` | Q3: NEXT_IF_FAIL traversal | Agent: evaluator node |
| `update_service_status(service, status)` | Q4: SET node status | Fault injector + evaluator |
| `count_unhealthy(service_names)` | Q5: termination check | Agent: evaluator node |
| `get_dependents(service_name)` | Q6: reverse DEPENDS_ON | Fault injector (alert routing) |

**Node type expansion plan (Layer 2, Phase 5):**

```
Layer 1 (current):  Service, Skill
Layer 2 (Phase 5):  Container, Metric, HealthCheck
Layer 3 (Phase 6):  Fault, FaultHistory (execution tracking for evaluation)
```

---

### Module C — LangGraph Agentic Brain

**File structure (built, content pending Phase 4):**

```
agent/
├── graph.py          ← LangGraph StateGraph definition
├── state.py          ← AgentState TypedDict
├── nodes/
│   ├── ingest.py     ← Alert payload parsing
│   ├── retriever.py  ← Neo4j Q1 + Q2 calls
│   ├── reasoner.py   ← LLM decision (execute/skip/escalate)
│   ├── executor.py   ← Calls sandbox tools
│   └── evaluator.py  ← Q5 health check + loop decision
└── tools/
    ├── graph_tools.py
    ├── sandbox_tools.py
    └── health_tools.py
```

**AgentState schema (core/schemas.py + agent/state.py):**

```python
class AgentState(TypedDict):
    alert_payload:      Dict[str, Any]   # Raw AlertPayload from webhook
    alert_service:      str              # e.g. "frontend"
    alert_error_type:   str              # e.g. "OOM_KILLED"
    root_cause_node:    Optional[str]    # Identified root service
    dependency_chain:   List[str]        # [root, ..., alert_service]
    current_skill:      Optional[str]    # SOP name being executed
    script_path:        Optional[str]
    execution_history:  List[Dict]       # [{skill, exit_code, stdout, stderr}]
    visited_skills:     List[str]        # Prevents revisiting
    attempt_count:      int
    max_attempts:       int              # Default: 5
    service_health_map: Dict[str, str]
    all_healthy:        bool
    rca_report:         Optional[Dict]
    error_message:      Optional[str]
```

---

### Module D — Docker Remediation Sandbox

**Architecture (Phase 5 target):**

```
agent/tools/sandbox_tools.py
    │
    ├── execute_sop(script_path, env_vars, timeout)
    │       │
    │       └── docker run \
    │               --rm \
    │               --name sop-run-{uuid} \
    │               --network sim-net \        ← can reach boutique services
    │               --cap-drop ALL \
    │               --cap-add NET_BIND_SERVICE \
    │               --security-opt no-new-privileges \
    │               --read-only \
    │               --tmpfs /tmp:size=64m \
    │               --memory=256m \
    │               --cpus=0.5 \
    │               --pids-limit=50 \
    │               --stop-timeout=60 \
    │               -v /sops/{script}:/script/{script}:ro \
    │               sop-executor:latest \
    │               python /script/{script} {params}
    │
    └── Returns: ExecutionResult(exit_code, stdout, stderr, duration_s)
```

**SOP scripts planned (sops/ directory):**

```
sops/
├── redis/
│   ├── cache_flush.sh          ← FLUSHALL + restore maxmemory
│   └── restart.sh              ← docker restart redis-cart
├── container/
│   └── restart.sh              ← docker restart {service}
└── network/
    └── reconnect.sh            ← docker network connect boutique-sim {service}
```

---

### 2.2 Data Generation via Chaos Engineering

The project generates a dynamic, reproducible fault dataset using Chaos Engineering directly within the simulation environment.

| Fault Type | Injection Method | Target Service | Cascade Path |
|---|---|---|---|
| Redis OOM | `CONFIG SET maxmemory 1mb` + key fill | `redis-cart` | redis-cart → cartservice → checkoutservice → frontend |
| Service Crash | `SIGKILL` via Docker SDK | any service | upstream dependents |
| Network Partition | `network.disconnect()` | any service | upstream dependents |
| High Latency | `tc qdisc netem delay Xms` | any service | upstream timeouts cascade |

**Fault injector CLI (verified):**

```bash
# Inject
python -m simulation.fault_injector inject redis_oom
python -m simulation.fault_injector inject service_crash --target paymentservice
python -m simulation.fault_injector inject network_partition --target productcatalogservice

# Reset
python -m simulation.fault_injector reset redis_oom
python -m simulation.fault_injector reset service_crash --target paymentservice

# List available faults
python -m simulation.fault_injector list
```

---

## Module 3: Technology Stack

| Layer | Technology | Version | Engineering Rationale |
|---|---|---|---|
| Agent State Machine | LangGraph | 1.2.4 | Supports cyclic graph routing, state validation, checkpoints, and multi-turn memory |
| LLM Framework | LangChain | 1.3.6 | LLM abstraction, tool binding, prompt management |
| LLM (local) | Llama 3.1:8b via Ollama | — | Zero API cost during dev; supports tool calling |
| LLM (eval) | GPT-4o / Claude 3.5 | — | Commercial baseline for RQ3/RQ4 comparison |
| Graph Database | Neo4j | 5.18 (Community) | Native graph storage, Cypher multi-hop traversal |
| Graph Driver | neo4j (Python) | 6.2.0 | Bolt protocol, connection pooling, typed records |
| Container Runtime | Docker Desktop | 24+ | Simulation environment + sandbox execution |
| Compose | Docker Compose | v2 | Full stack orchestration |
| API Server | FastAPI | 0.136.3 | Alert ingestion webhook (POST /alert) |
| Validation | Pydantic | 2.13.4 | AlertPayload, AgentState, ExecutionResult schemas |
| Logging | structlog | 26.1.0 | Structured JSON logs across all modules |
| Config | pydantic-settings | — | Typed .env loading via BaseSettings |
| Simulation | Online Boutique | v0.10.5 | Real-world microservice benchmark (Go/gRPC) |
| Chaos | Docker SDK (Python) | 7.1.0 | Programmatic fault injection |
| Eval baselines | FAISS + SentenceTransformers | — | Vector RAG baseline for RQ1/RQ2 |

---

## Module 4: Multi-Agent Logic and State Machine

### 4.1 LangGraph State and Execution Loop

The agent avoids single-turn prompt execution by establishing an explicit directed workflow state machine:

```
START: POST /alert (AlertPayload)
         │
         ▼
┌─────────────────────────┐
│  nodes/ingest.py        │  Parse alert_service, alert_error_type
│  Layer 1: Orchestration │  Initialise AgentState
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  nodes/retriever.py     │  Q1: get_root_cause(alert_service)
│  Layer 2: Graph Lookup  │  Q2: get_skill(root_node, error_type)
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  nodes/reasoner.py      │  LLM: evaluate SOP context
│  Layer 3: LLM Reasoning │  Output: {action: execute|skip|escalate}
└────────────┬────────────┘
             ▼
     Conditional Edge
        /           \
  execute          escalate
     |                |
     ▼                ▼
┌──────────────┐   ┌─────────────────────┐
│  nodes/      │   │  END: RCA Report    │
│  executor.py │   │  (unresolved)       │
│  Docker SOP  │   └─────────────────────┘
└──────┬───────┘
       ▼
┌─────────────────────────┐
│  nodes/evaluator.py     │  Q5: count_unhealthy(dependency_chain)
│  Layer 5: Verification  │  Q3: get_next_skill(current_skill)
└────────────┬────────────┘
             │
     Conditional Edge
        /           \
  all_healthy       still_unhealthy
     |                |
     ▼                └──► back to retriever (next skill)
┌─────────────────────────┐
│  reporter.py            │  Write /audit/rca_{alert_id}.json
│  Layer 5: Resolution    │  RCAReport: root_cause, chain, scripts, MTTR
└─────────────────────────┘
```

---

### 4.2 State Transition Algorithm

**Step 1 — Alert Ingestion:** `AlertPayload` arrives at `POST /alert`. FastAPI validates against the Pydantic schema and initialises `AgentState` with zeroed execution counters.

**Step 2 — GraphRAG Retrieval:** Q1 traverses DEPENDS_ON edges backwards from the alerting service, returning the deepest unhealthy node and the full dependency chain. Q2 looks up the matching SOP Skill node.

```cypher
-- Q1: Root cause traversal (graph/graph_client.py)
MATCH path = (alert:Service {name: $alert_service})-[:DEPENDS_ON*1..8]->(root:Service)
WHERE root.status <> 'HEALTHY'
WITH root, reverse([n IN nodes(path) | n.name]) AS chain, length(path) AS depth
RETURN root.name AS root_cause_node, chain AS dependency_chain, depth AS depth
ORDER BY depth DESC LIMIT 1
```

**Step 3 — LLM Reasoning:** The LLM receives ONLY the current skill node's context (Progressive Context Injection — not the full graph). Returns structured JSON: `{action: "execute"|"skip"|"escalate", reason: "..."}`.

**Step 4 — Sandbox Execution:** `executor.py` calls `sandbox_tools.execute_sop()` which spawns an ephemeral container via Docker SDK, captures stdout/stderr, returns `ExecutionResult`.

**Step 5 — Evaluation Loop:** Evaluator runs Q5 to count unhealthy services. If zero → generate report and terminate. If non-zero and attempts < max → follow NEXT_IF_FAIL edge (Q3) and loop back to Step 2. If attempts exhausted → escalate.

---

## Module 5: Security Engineering and Containment

### 5.1 Sandbox Architecture

To eliminate the execution exposure present in the base paper, all code execution is encapsulated inside a tightly restricted containerization layer:

```
┌────────────────────────────────────────────────────────┐
│  HOST SYSTEM / HARDWARE KERNEL                         │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │  DOCKER API DAEMON (/var/run/docker.sock)        │  │
│  └────────────────────────┬─────────────────────────┘  │
│                           │  (Spawns ephemeral container)
│                           ▼                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │  ISOLATED SANDBOX CONTAINER (sop-executor:latest)│  │
│  │                                                  │  │
│  │  --cap-drop ALL                                  │  │
│  │  --cap-add NET_BIND_SERVICE                      │  │
│  │  --security-opt no-new-privileges                │  │
│  │  --read-only (--tmpfs /tmp:size=64m)             │  │
│  │  --memory=256m --cpus=0.5 --pids-limit=50       │  │
│  │  --stop-timeout=60                               │  │
│  │  --network sim-net (internal, no internet)       │  │
│  │  --rm (auto-destroyed after execution)           │  │
│  │                                                  │  │
│  │  ┌──────────────────────────────────────────┐   │  │
│  │  │  Python / Bash SOP Script Runner         │   │  │
│  │  │  Mounted read-only from sops/            │   │  │
│  │  └──────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

---

### 5.2 Containment Constraints

| Control | Implementation | Purpose |
|---|---|---|
| Network Isolation | `--network sim-net` (internal bridge, no outbound) | Prevents AI from fetching external packages or reaching unauthorized endpoints |
| Resource Limits | `--memory=256m --cpus=0.5 --pids-limit=50` | Guards against infinite recursion or resource exhaustion |
| Execution Timeout | `--stop-timeout=60` (hard kill) | Protects compute if a generated script stalls |
| Read-Only Filesystem | `--read-only --tmpfs /tmp:size=64m` | Prevents scripts from overwriting system images or binaries |
| Script Allowlist | Only paths present in the Neo4j Skill graph can be executed | LLM cannot call arbitrary scripts — graph edges constrain the tool space |
| Capability Drop | `--cap-drop ALL --cap-add NET_BIND_SERVICE` | Prevents privilege escalation even if script is malicious |
| Ephemeral Containers | `--rm` flag | Zero state carry-over between SOP executions |

---

## Module 6: Implementation Roadmap

### Phase 0 — Foundation ✅ Complete

**Objective:** Establish reproducible Python environment and shared project infrastructure.

- [x] Python 3.11 virtual environment (`.venv/`) — isolated from global packages
- [x] `pyproject.toml` — all deps pinned: LangGraph 1.2.4, LangChain 1.3.6, Neo4j 6.2.0, FastAPI 0.136.3
- [x] `.env` + `.env.example` — secrets management, never committed
- [x] `.gitignore` — `.venv/`, `.env`, `neo4j/data/`, `audit/` excluded
- [x] `core/config.py` — typed Pydantic BaseSettings singleton
- [x] `core/schemas.py` — AlertPayload, SkillNode, ExecutionResult, RCAReport
- [x] `core/exceptions.py` — typed hierarchy: AgentError → GraphError, SandboxError, LLMError
- [x] `core/logging_config.py` — structlog, JSON in prod, coloured in dev
- [x] Verification: `python -c "from core import settings, get_logger" → OK`

---

### Phase 1 — Docker Environment ✅ Complete

**Objective:** Neo4j and Docker daemon running, all imports verified.

- [x] `docker-compose.yml` — Neo4j 5.18 + DinD + commented Phase 3/4 services
- [x] Neo4j browser accessible at `http://localhost:7474`
- [x] Bolt protocol at `bolt://localhost:7687`
- [x] Authentication working (`neo4j/supersecretpassword`)
- [x] Persistent volume `neo4j-data` surviving restarts
- [x] Git branch structure: `main → dev → phase/*`

**Fix applied:** First run failed due to `.env` created after initial Neo4j volume init. Fixed with `docker compose down -v && docker compose up -d` to recreate volume with correct credentials.

---

### Phase 2 — Neo4j Dual-Graph ✅ Complete

**Objective:** Populated Infrastructure KG + Semantic Skill Graph with verified traversal.

- [x] `graph/schema_definitions.py` — node/relationship type definitions
- [x] `graph/cypher/service_topology.cypher` — 56 Cypher statements (Online Boutique topology)
- [x] `graph/cypher/remediation_queries.cypher` — Q1–Q5 runtime queries
- [x] `graph/graph_client.py` — singleton driver, 6 typed query methods
- [x] `graph/graph_populator.py` — quote-aware Cypher splitter (handles semicolons in string properties)
- [x] `graph/scripts/init_graph.py` — population + full validation script

**Verified graph state:**

```
Service nodes:  12 (full Online Boutique topology)
Skill nodes:     9
DEPENDS_ON:     16
APPLIES_TO:     12
NEXT_IF_FAIL:    4

Smoke test: Alert=frontend → Root=redis-cart, Chain=[redis-cart, cartservice, checkoutservice, frontend], Depth=3 ✅
```

**Fix applied:** Naive `raw.split(";")` in init script broke on Cypher statements containing semicolons inside string descriptions. Fixed with quote-aware state-machine splitter.

---

### Phase 3 — Simulation Environment ✅ Complete

**Objective:** Real running microservice stack with verified fault injection.

- [x] Cloned Google Online Boutique v0.10.5 → `simulation/online-boutique/`
- [x] Translated Kubernetes manifests → `simulation/docker-compose.yml`
- [x] All 12 containers running, `curl localhost:8080 → 200`
- [x] `simulation/fault_injector.py` — 4 fault types with matching reset functions
- [x] `graph/graph_client.py` — added `get_dependents()` reverse traversal (Q6)
- [x] Verified: `inject redis_oom` → Neo4j `redis-cart.status = OOM_KILLED` → `reset` → `HEALTHY` ✅
- [x] Verified: `inject service_crash --target paymentservice` → `CRASH_LOOPING` → reset → `HEALTHY` ✅

**Fixes applied (M2 Mac + v0.10.5 compatibility):**

| Issue | Root Cause | Fix |
|---|---|---|
| Services failing health checks at ~91.7s | v0.10.5 uses K8s-native gRPC probes — `grpc_health_probe` binary not bundled in images | Removed gRPC health checks; used `service_started` condition |
| `frontend` crash loop (exit code 2) | `mustMapEnv()` panics if `SHOPPING_ASSISTANT_SERVICE_ADDR` unset, even when feature unused | Added `SHOPPING_ASSISTANT_SERVICE_ADDR: "shoppingassistantservice:50051"` to env |
| Platform warnings on all services | Images are `linux/amd64`, host is `linux/arm64/v8` (M2) | Added `platform: linux/amd64` to all GCR images |
| `with GraphClient() as gc:` error | `GraphClient` is a singleton with `atexit` cleanup — context manager would close shared driver | Replaced all `with` blocks with direct instantiation `gc = GraphClient()` |
| Redis fill loop producing 0 keys | `sh -c 'for i in ...'` exec doesn't work in minimal container images | Changed to 500 individual `exec_run(["redis-cli", "SET", ...])` calls |

---

### Phase 4 — LangGraph Agent Core ⬜ Next

**Objective:** Working ReAct loop: alert → graph retrieval → LLM reasoning.

- [ ] `agent/state.py` — AgentState TypedDict finalised
- [ ] `agent/nodes/ingest.py` — AlertPayload parsing, state initialisation
- [ ] `agent/nodes/retriever.py` — calls `gc.get_root_cause()` + `gc.get_skill()`
- [ ] `agent/nodes/reasoner.py` — LLM call with Progressive Context Injection
- [ ] `agent/graph.py` — LangGraph StateGraph + conditional edges
- [ ] `agent/main.py` — FastAPI webhook server on port 8888
- [ ] Validation: mock alert → root cause correctly identified without sandbox

---

### Phase 5 — Docker Sandbox + SOP Scripts ⬜ Pending

**Objective:** Agent executes real scripts safely in isolated containers.

- [ ] `sop-executor/Dockerfile` — minimal Python image, non-root user
- [ ] `sops/redis/cache_flush.sh` — FLUSHALL + restore maxmemory
- [ ] `sops/redis/restart.sh` — docker restart redis-cart
- [ ] `sops/container/restart.sh` — generic service restart
- [ ] `sops/network/reconnect.sh` — network reconnect
- [ ] `agent/tools/sandbox_tools.py` — Docker SDK integration
- [ ] Update Skill graph: `script_path` fields → real paths in `sops/`

---

### Phase 6 — End-to-End Integration + Chaos Testing ⬜ Pending

**Objective:** Full pipeline: fault injection → agent detects → graph query → SOP → verified resolution.

- [ ] Bridge `boutique-sim` and `agent-net` networks for agent ↔ services
- [ ] Run all 4 fault scenarios end-to-end
- [ ] Verify multi-hop scenario: redis_oom resolves cache → frontend
- [ ] Commit final scenario results to `eval/scenarios.json`

---

### Phase 6.5 — Dashboard ⬜ Pending

**Objective:** Streamlit live dashboard for demo impact.

- [ ] `dashboard/app.py` — Streamlit main
- [ ] Agent activity log feed (live events from `/audit/`)
- [ ] Neo4j graph visualization (Neovis.js)
- [ ] LangGraph node state display
- [ ] Final RCA report rendering

---

### Phase 7 — Evaluation ⬜ Pending

**Objective:** Quantitative answers to RQ1–RQ4.

- [ ] `eval/baselines/zero_shot.py` — Standard LLM baseline
- [ ] `eval/baselines/vector_rag.py` — FAISS + SentenceTransformers baseline
- [ ] `eval/benchmark.py` — automated run across all baselines
- [ ] `eval/scenarios.json` — ground truth for 6+ fault scenarios
- [ ] Produce: MTTR comparison table, hallucination rates, blast-radius F1, LLM sensitivity table

---

### Phase 8 — Report + Presentation ⬜ Pending

- [ ] MTech seminar report (chapters 1–7)
- [ ] 12-slide presentation
- [ ] Architecture diagrams (Neo4j graph screenshots, LangGraph flow)
- [ ] Results figures (MTTR bar chart, token efficiency, RQ comparison tables)

---

## Module 7: Actual Build Log and Key Decisions

### 7.1 Architecture Decisions

| Decision | Original Plan | Final Decision | Reason |
|---|---|---|---|
| Simulation environment | Custom FastAPI microservices | Google Online Boutique v0.10.5 | Real benchmark used in AIOps papers; comparable to prior art; free and maintained |
| LLM during development | GPT-4o API | Llama 3.1:8b via Ollama | Zero cost; supports tool calling; directly relevant to RQ4 |
| Graph context injection | Full graph per LLM call | Progressive Context Injection (one node) | Eliminates context bloat; mathematically constrains tool hallucination |
| GraphClient lifecycle | Context manager (`with`) | Singleton with `atexit` | One driver + connection pool per process; context manager closes shared resource |
| Health checks | `grpc_health_probe` | `service_started` + HTTP check for frontend | v0.10.5 uses K8s-native gRPC probes; binary not bundled |

### 7.2 Installed Package Versions (Actual)

```
langgraph    1.2.4
langchain    1.3.6
neo4j        6.2.0     # Note: major version jump from pinned 5.x
fastapi      0.136.3
docker       7.1.0
pydantic     2.13.4
structlog    26.1.0
```

### 7.3 Project File Map

```
agentic-graphrag-rca/
├── .env                        ✅  (gitignored)
├── .env.example                ✅
├── .gitignore                  ✅
├── pyproject.toml              ✅  (all deps pinned)
├── docker-compose.yml          ✅  (Neo4j + DinD + Phase 4+ services commented)
├── Dockerfile                  ⬜  (agent image — Phase 4)
├── core/
│   ├── config.py               ✅
│   ├── schemas.py              ✅
│   ├── exceptions.py           ✅
│   ├── logging_config.py       ✅
│   └── __init__.py             ✅
├── graph/
│   ├── graph_client.py         ✅  (Q1–Q6 implemented)
│   ├── graph_populator.py      ✅
│   ├── schema_definitions.py   ✅
│   ├── cypher/
│   │   ├── service_topology.cypher     ✅  (56 statements)
│   │   └── remediation_queries.cypher  ✅
│   └── scripts/
│       ├── init_graph.py       ✅  (populate + validate)
│       └── load_sops.py        ⬜  (Phase 5)
├── agent/
│   ├── graph.py                ⬜  (Phase 4)
│   ├── state.py                ⬜  (Phase 4)
│   ├── nodes/                  ⬜  (Phase 4)
│   └── tools/                  ⬜  (Phase 5)
├── sandbox/                    ⬜  (Phase 5)
├── sops/                       ⬜  (Phase 5 — scripts written here)
├── simulation/
│   ├── docker-compose.yml      ✅  (Online Boutique v0.10.5)
│   ├── fault_injector.py       ✅  (4 faults, verified)
│   └── online-boutique/        ✅  (gitignored subdir)
├── eval/
│   ├── baselines/              ⬜  (Phase 7)
│   ├── benchmark.py            ⬜  (Phase 7)
│   └── scenarios.json          ⬜  (Phase 6)
└── docs/
    └── ENGINEERING_REFERENCE.md ✅  (this file)
```

---

## Module 8: Research Questions

These four research questions frame the formal evaluation in Phase 7.

| RQ | Question | Experiment | Metric |
|---|---|---|---|
| **RQ1** | Does GraphRAG improve root cause identification accuracy compared to standard LLM approaches? | Compare: Zero-Shot LLM vs Vector RAG vs Agentic GraphRAG across all fault scenarios | Root Cause Accuracy (%), MTTR (seconds) |
| **RQ2** | Does graph-based retrieval improve blast-radius estimation? | Measure which services each system identifies as affected vs. ground truth dependency chain | Blast Radius F1 Score |
| **RQ3** | How sensitive is the system to the choice of LLM? | Run full evaluation with: GPT-4o, Claude 3.5 Sonnet, Llama 3.1 8B, Qwen 2.5 Coder 7B | MTTR and Root Cause Accuracy per model |
| **RQ4** | Can an 8B local model achieve performance comparable to commercial models when supported by a structured dependency graph? | Same experiment as RQ3 — delta between Llama 3.1 8B and GPT-4o | Performance gap with/without graph constraints |

**Core hypothesis (RQ3 + RQ4 combined):** Structured dependency graph retrieval reduces the performance gap between large commercial LLMs and small local models by constraining the action space and providing explicit topology context — compensating for reduced parametric knowledge.

---

*Department of Computer Engineering & Technology, MIT World Peace University*
*Developer: Raghav Nimbalkar (PRN: 1262251354) | Guide: Dr. Bhavana Tiple*
*Last updated: Phase 3 complete — proceeding to Phase 4*
