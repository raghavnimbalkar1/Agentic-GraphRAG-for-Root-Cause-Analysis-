# Agentic GraphRAG — Autonomous RCA in Cloud-Native Microservices
## Project Roadmap: From Scratch to Evaluation

> **Student:** Raghav Nimbalkar · PRN 1262251354  
> **Guide:** Dr. Bhavana Tiple · MIT-WPU, Pune  
> **Domain:** AIOps · LLMs · Graph ML · DevOps  
> **Repo:** [github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-](https://github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-)

---

## Table of Contents

1. [Project Summary & Core Architecture](#1-project-summary--core-architecture)
2. [Master Timeline](#2-master-timeline)
3. [Phase 0 — Research Lock & Baseline Survey](#3-phase-0--research-lock--baseline-survey)
4. [Phase 1 — Environment Setup & Containerization](#4-phase-1--environment-setup--containerization)
5. [Phase 2 — Neo4j Dual-Graph Construction](#5-phase-2--neo4j-dual-graph-construction)
6. [Phase 3 — Simulated Microservice Environment](#6-phase-3--simulated-microservice-environment)
7. [Phase 4 — LangGraph Agentic Brain](#7-phase-4--langgraph-agentic-brain)
8. [Phase 5 — Docker Execution Sandbox (OpenClaw)](#8-phase-5--docker-execution-sandbox-openclaw)
9. [Phase 6 — Chaos Engineering & End-to-End Integration](#9-phase-6--chaos-engineering--end-to-end-integration)
10. [Phase 7 — Evaluation & Benchmarking](#10-phase-7--evaluation--benchmarking)
11. [Phase 8 — Paper, Report & Final Presentation](#11-phase-8--paper-report--final-presentation)
12. [Full Tech Stack Reference](#12-full-tech-stack-reference)
13. [Metrics & KPI Definitions](#13-metrics--kpi-definitions)
14. [Risk Register](#14-risk-register)
15. [Project Directory Structure](#15-project-directory-structure)

---

## 1. Project Summary & Core Architecture

### Problem
Cloud-native microservice failures cascade across dependency chains. Current AI approaches are either:
- **Advisory-only** (identify root cause, page an engineer, no execution)
- **Execution-unsafe** (run raw code on host, no sandbox, security risk)
- **Context-blind** (flat text retrieval, no topology awareness)

### Solution
An **Agentic GraphRAG** system that:
1. Traces cascading failures via multi-hop graph traversal (Neo4j)
2. Retrieves only the active SOP skill node per LLM call (Progressive Context Injection)
3. Executes remediation scripts in an isolated Docker sandbox
4. Loops until all services are verified healthy (ReAct cycle)

### Dual-Graph Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        NEO4J GRAPH DB                           │
│                                                                 │
│  Graph 1: Infrastructure Knowledge Graph  │  Graph 2: Skill    │
│  (THE WHERE)                              │  Graph (THE HOW)   │
│                                                                 │
│  postgres-db ──DEPENDS_ON──► redis-cache  │  PG_Deadlock_SOP   │
│  redis-cache ──DEPENDS_ON──► api-gateway  │     │NEXT_IF_FAIL  │
│  api-gateway ──DEPENDS_ON──► web-frontend │  Redis_Flush_SOP   │
│                                           │                    │
│  Service nodes: status, error_code        │  Skill nodes:      │
│  Edge props:   criticality, timeout_ms    │  script_path, type │
└─────────────────────────────────────────────────────────────────┘
         ▲  Cypher queries (bidirectional)  ▲
         │                                  │
┌────────┴──────────────────────────────────┴────────────────────┐
│                   LANGGRAPH AGENTIC BRAIN                       │
│                                                                 │
│  Layer 1: Orchestration (LangGraph StateGraph)                  │
│  Layer 2: Hybrid Graph Retrieval (Neo4j Cypher)                 │
│  Layer 3: Cognitive Processing (GPT-4o / Qwen / DeepSeek)      │
│  Layer 4: Secure Execution (OpenClaw → Docker Sandbox)          │
│  Layer 5: Evaluation & Report Generation                        │
└────────────────────────────┬───────────────────────────────────┘
                             │ Alert webhook (JSON)
                             ▼
┌────────────────────────────────────────────────────────────────┐
│          SIMULATED CLOUD ENVIRONMENT (Docker Compose)          │
│  postgres-db │ redis-cache │ payment-api │ web-frontend        │
│  + chaos-injector (fault injection engine)                     │
└────────────────────────────────────────────────────────────────┘
```

### Key Design Invariants
| Invariant | Mechanism |
|---|---|
| 0% tool hallucination | Agent can only call tools reachable via current graph edges |
| No host exposure | All scripts run in ephemeral Docker containers (--rm, --cap-drop ALL) |
| Context minimality | Only the active skill node is injected into LLM context |
| Loop termination | Max hop count + visited-skills registry prevents infinite loops |

---

## 2. Master Timeline

| Phase | Description | Duration | Deliverable |
|---|---|---|---|
| **Phase 0** | Research lock, literature finalization, baseline setup | 2 weeks | Finalized lit survey table, confirmed tech stack |
| **Phase 1** | Docker env, project scaffold, Neo4j setup | 1 week | Running `docker-compose up`, all containers healthy |
| **Phase 2** | Neo4j dual-graph design, schema, population | 3 weeks | Populated graph, Cypher queries validated |
| **Phase 3** | Simulated microservice environment | 2 weeks | 6-service simulation running, health checks passing |
| **Phase 4** | LangGraph agent: state machine + ReAct loop | 4 weeks | Agent receives alert, queries graph, reasons |
| **Phase 5** | Docker execution sandbox + OpenClaw integration | 2 weeks | Agent executes SOP scripts safely in sandbox |
| **Phase 6** | Chaos engineering + end-to-end integration | 3 weeks | Agent autonomously resolves injected faults |
| **Phase 7** | Evaluation, benchmarking, baseline comparison | 3 weeks | MTTR/F1/hallucination metrics, comparison table |
| **Phase 8** | Report, paper draft, final presentation | 3 weeks | Thesis chapter, seminar slides, poster |
| **Total** | | **~23 weeks** | Fully evaluated system + written report |

> **Note:** Phases 2–5 have interdependencies but can be partially parallelised. Start Phase 3 (simulation) immediately after Phase 1 while Phase 2 (graph) is in progress.

---

## 3. Phase 0 — Research Lock & Baseline Survey

**Duration:** 2 weeks  
**Goal:** Finalise the 8-paper literature survey, confirm gaps, lock the tech stack, set up version control.

### 3.1 Tasks

- [ ] Complete full reads of all 8 papers (see table below)
- [ ] Extract gap matrix: what each paper does NOT solve
- [ ] Confirm that no published paper delivers: graph-aware retrieval + executable SOP nodes + sandboxed autonomous execution + cyclical verification
- [ ] Lock the LLM choice (GPT-4o API vs local Qwen 2.5 Coder 7B via Ollama)
- [ ] Initialise GitHub repo with branch structure: `main`, `dev`, `phase/*`
- [ ] Set up project management: GitHub Projects or Notion board

### 3.2 Literature Gap Matrix

| Paper | Venue | What It Does | Critical Gap |
|---|---|---|---|
| Flow-of-Action (Base) | ACM WWW 2025 | SOP-guided multi-agent LLM | Flat text, no graph topology, no sandbox |
| AetherLog | IEEE ISSRE 2025 | LLM + KG for log analysis | Advisory only — cannot execute fixes |
| Intelligent RCL Survey | ACM 2025 | Reviews AI/DL/GNN for RCL | All surveyed systems advisory, no autonomous exec |
| Causal Inference RCA | IEEE/ACM ASE 2024 | 30+ causal discovery algorithms | Brittle, timeouts, no semantic interpretability |
| Intervention Recognition (eBay) | ACM KDD 2024 | Latent space RCA | Uninterpretable output, no SOPs, no execution |
| KGroot | Recent | Fault propagation GNN | Cannot act on recommendations |
| TRACEDIAG | Microsoft 2023 | RL-based graph pruning | Dashboard alert only — no execution |
| KG Survey | ACM Comp. Surveys 2022 | KG as static data structure | Static, human-queried — not agent-traversable |

### 3.3 LLM Selection Decision Tree

```
Do you have reliable internet + budget for API calls?
├── YES → GPT-4o (best tool-calling reliability)
│          or Codex 3.5 Sonnet (strong structured output)
└── NO  → Do you have a GPU with ≥8GB VRAM?
           ├── YES → Qwen 2.5 Coder 7B via Ollama (best local tool-calling)
           └── NO  → DeepSeek V3 API (cheap) or Qwen via CPU (slow)
```

### 3.4 Deliverables Checklist
- [ ] `docs/literature_survey.md` — annotated gap table
- [ ] `docs/architecture_decisions.md` — ADR (Architecture Decision Record) for each major choice
- [ ] `README.md` — project overview with badges
- [ ] `.env.example` — template for API keys

---

## 4. Phase 1 — Environment Setup & Containerization

**Duration:** 1 week  
**Goal:** All containers running, agent scaffold compiling, Neo4j browser accessible.

### 4.1 Prerequisites

```bash
# Minimum local hardware
# CPU: Intel i7 / AMD Ryzen 7 (8+ cores recommended)
# RAM: 16 GB (32 GB for local LLM inference)
# Storage: 20 GB free (Docker images + Neo4j data)
# Optional: NVIDIA GPU 6GB+ VRAM for local LLM

# Install
docker --version   # Docker Desktop 24+
docker compose version  # Compose v2.20+
python --version   # Python 3.11+
node --version     # Node 18+ (optional, for any JS tooling)
```

### 4.2 Project Scaffold

```bash
mkdir agentic-graphrag-rca && cd agentic-graphrag-rca

# Directory structure (detailed in Section 15)
mkdir -p agent/{nodes,tools,state,utils}
mkdir -p neo4j/{import,init}
mkdir -p sops/{postgres,redis,api,container}
mkdir -p sim/{postgres,redis,payment,user,frontend,nginx}
mkdir -p chaos
mkdir -p eval/{baselines,results,scripts}
mkdir -p docs

touch docker-compose.yml .env .env.example
touch agent/main.py agent/graph.py agent/state.py
touch requirements.txt
```

### 4.3 Core `docker-compose.yml`

```yaml
version: '3.8'

networks:
  agent-net:      # Agent ↔ Neo4j ↔ Docker daemon (has internet for LLM API)
    driver: bridge
  sim-net:        # Simulated microservices + sandbox (NO internet)
    driver: bridge
    internal: true

volumes:
  neo4j-data:
  neo4j-logs:
  audit-logs:

services:

  neo4j:
    image: neo4j:5.18-community
    container_name: neo4j
    networks: [agent-net]
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_memory_heap_max__size: 1G
    volumes:
      - neo4j-data:/data
      - neo4j-logs:/logs
      - ./neo4j/import:/import
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j",
             "-p", "${NEO4J_PASSWORD}", "RETURN 1"]
      interval: 10s
      retries: 10

  docker-daemon:
    image: docker:24-dind
    container_name: docker-daemon
    privileged: true
    networks: [agent-net]
    environment:
      DOCKER_TLS_CERTDIR: ""
    healthcheck:
      test: ["CMD", "docker", "info"]
      interval: 10s
      retries: 5

  langgraph-agent:
    build: ./agent
    container_name: langgraph-agent
    networks: [agent-net]
    depends_on:
      neo4j:          {condition: service_healthy}
      docker-daemon:  {condition: service_healthy}
    environment:
      NEO4J_URI:          bolt://neo4j:7687
      NEO4J_USER:         neo4j
      NEO4J_PASSWORD:     ${NEO4J_PASSWORD}
      DOCKER_HOST:        tcp://docker-daemon:2375
      LLM_PROVIDER:       ${LLM_PROVIDER:-openai}
      LLM_MODEL:          ${LLM_MODEL:-gpt-4o}
      OPENAI_API_KEY:     ${OPENAI_API_KEY}
      ANTHROPIC_API_KEY:  ${ANTHROPIC_API_KEY}
    volumes:
      - audit-logs:/audit
      - ./sops:/sops:ro
    ports: ["8888:8888"]

  # --- Simulated microservices (Phase 3) ---
  postgres-db:
    image: postgres:15-alpine
    container_name: postgres-db
    networks: [sim-net]
    environment:
      POSTGRES_DB: appdb
      POSTGRES_USER: appuser
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U appuser"]
      interval: 5s

  redis-cache:
    image: redis:7-alpine
    container_name: redis-cache
    networks: [sim-net]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s

  # payment-api, user-api, api-gateway, web-frontend → Phase 3
  # chaos-injector → Phase 6
```

### 4.4 Agent `Dockerfile`

```dockerfile
# agent/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Docker CLI for communicating with DinD
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8888
CMD ["python", "-m", "agent.main"]
```

### 4.5 `requirements.txt`

```text
# Core agent
langchain==0.2.16
langgraph==0.2.28
langchain-openai==0.1.25
langchain-anthropic==0.1.23
langchain-community==0.2.16

# Neo4j
neo4j==5.24.0

# Docker SDK
docker==7.1.0

# API server
fastapi==0.115.0
uvicorn==0.30.6

# Utilities
pydantic==2.8.2
python-dotenv==1.0.1
structlog==24.4.0
```

### 4.6 Validation

```bash
docker compose up neo4j docker-daemon -d
# Verify Neo4j browser: http://localhost:7474
# Verify DinD: docker exec docker-daemon docker info
```

### 4.7 Phase 1 Metrics
| Check | Pass Condition |
|---|---|
| All containers healthy | `docker compose ps` shows all `healthy` |
| Neo4j accessible | Browser UI on `localhost:7474` |
| DinD running | `docker exec docker-daemon docker run hello-world` |
| Agent imports clean | `python -c "import langgraph; import neo4j; import docker"` |

---

## 5. Phase 2 — Neo4j Dual-Graph Construction

**Duration:** 3 weeks  
**Goal:** Fully populated dual-graph in Neo4j with validated Cypher traversal queries.

### 5.1 Graph 1 — Infrastructure Knowledge Graph Schema

#### Node: `:Service`
```cypher
// Create a Service node
CREATE (s:Service {
  name:         'postgres-db',     // Unique ID — must match container_name
  service_type: 'database',        // database|cache|api|gateway|frontend|lb
  host:         'postgres-db',     // Resolvable hostname in sim-net
  port:         5432,
  status:       'HEALTHY',         // HEALTHY|DEADLOCK_ERROR|OOM_KILLED|DOWN|DEGRADED
  last_updated: datetime(),
  error_code:   null               // Populated by chaos injector
})
```

#### Relationship Types
```cypher
// DEPENDS_ON: upstream service fails if downstream fails
(web-frontend)-[:DEPENDS_ON {criticality: 'HIGH',   timeout_ms: 30000}]->(api-gateway)
(api-gateway) -[:DEPENDS_ON {criticality: 'HIGH',   timeout_ms: 5000}] ->(payment-api)
(api-gateway) -[:DEPENDS_ON {criticality: 'MEDIUM', timeout_ms: 5000}] ->(user-api)
(payment-api) -[:DEPENDS_ON {criticality: 'HIGH',   timeout_ms: 3000}] ->(redis-cache)
(user-api)    -[:DEPENDS_ON {criticality: 'HIGH',   timeout_ms: 3000}] ->(postgres-db)
(redis-cache) -[:DEPENDS_ON {criticality: 'CRITICAL',timeout_ms: 1000}]->(postgres-db)
```

#### Population Script
```cypher
// neo4j/init/01_infrastructure_graph.cypher

MERGE (pg:Service {name:'postgres-db', service_type:'database', port:5432, status:'HEALTHY'})
MERGE (rd:Service {name:'redis-cache',  service_type:'cache',    port:6379, status:'HEALTHY'})
MERGE (pa:Service {name:'payment-api',  service_type:'api',      port:8001, status:'HEALTHY'})
MERGE (ua:Service {name:'user-api',     service_type:'api',      port:8002, status:'HEALTHY'})
MERGE (ag:Service {name:'api-gateway',  service_type:'gateway',  port:8000, status:'HEALTHY'})
MERGE (fe:Service {name:'web-frontend', service_type:'frontend', port:3000, status:'HEALTHY'})

CREATE (pg)<-[:DEPENDS_ON {criticality:'CRITICAL'}]-(rd)
CREATE (pg)<-[:DEPENDS_ON {criticality:'HIGH'}]-(ua)
CREATE (rd)<-[:DEPENDS_ON {criticality:'HIGH'}]-(pa)
CREATE (pa)<-[:DEPENDS_ON {criticality:'HIGH'}]-(ag)
CREATE (ua)<-[:DEPENDS_ON {criticality:'MEDIUM'}]-(ag)
CREATE (ag)<-[:DEPENDS_ON {criticality:'HIGH'}]-(fe)
```

### 5.2 Graph 2 — Semantic Skill Graph Schema

#### Node: `:Skill`
```cypher
CREATE (sk:Skill {
  name:                 'Postgres_Deadlock_Kill_SOP',
  script_path:          '/sops/postgres/deadlock_kill.py',
  script_type:          'python',      // python|bash
  description:          'Terminates blocking transaction PIDs in PostgreSQL',
  trigger_condition:    'DEADLOCK_ERROR',
  params:               ['--host', '--port', '--db-user', '--db-password'],
  timeout_seconds:      30,
  risk_level:           'LOW',         // LOW|MEDIUM|HIGH
  requires_confirmation: false,
  success_check:        'deadlock_count_after == 0'
})
```

#### Full Skill Graph Population
```cypher
// neo4j/init/02_skill_graph.cypher

// --- PostgreSQL Skills ---
MERGE (pg_deadlock:Skill {name:'Postgres_Deadlock_Kill_SOP',
  script_path:'/sops/postgres/deadlock_kill.py', trigger_condition:'DEADLOCK_ERROR'})
MERGE (pg_restart:Skill  {name:'Postgres_Restart_SOP',
  script_path:'/sops/postgres/restart.sh',       trigger_condition:'CONNECTION_REFUSED'})
MERGE (pg_vacuum:Skill   {name:'Postgres_Vacuum_SOP',
  script_path:'/sops/postgres/vacuum.py',        trigger_condition:'TABLE_BLOAT'})

// --- Redis Skills ---
MERGE (rd_flush:Skill    {name:'Redis_Cache_Flush_SOP',
  script_path:'/sops/redis/cache_flush.sh',      trigger_condition:'STALE_DATA'})
MERGE (rd_restart:Skill  {name:'Redis_Restart_SOP',
  script_path:'/sops/redis/restart.sh',          trigger_condition:'OOM_KILLED'})

// --- Container / API Skills ---
MERGE (ct_restart:Skill  {name:'Container_Restart_SOP',
  script_path:'/sops/container/restart.sh',      trigger_condition:'CRASH_LOOPING'})
MERGE (ct_scale:Skill    {name:'Container_Scale_SOP',
  script_path:'/sops/container/scale_up.sh',     trigger_condition:'HIGH_CPU'})
MERGE (gw_reload:Skill   {name:'APIGateway_Reload_SOP',
  script_path:'/sops/api/nginx_reload.sh',       trigger_condition:'CONFIG_STALE'})

// --- NEXT_IF_FAIL chain ---
MERGE (pg_deadlock)-[:NEXT_IF_FAIL]->(rd_flush)
MERGE (rd_flush)   -[:NEXT_IF_FAIL]->(ct_restart)
MERGE (pg_restart) -[:NEXT_IF_FAIL]->(ct_restart)

// --- APPLIES_TO: link skills to infrastructure nodes ---
MERGE (pg_deadlock)-[:APPLIES_TO]->(:Service {name:'postgres-db'})
MERGE (pg_restart) -[:APPLIES_TO]->(:Service {name:'postgres-db'})
MERGE (rd_flush)   -[:APPLIES_TO]->(:Service {name:'redis-cache'})
MERGE (rd_restart) -[:APPLIES_TO]->(:Service {name:'redis-cache'})
MERGE (ct_restart) -[:APPLIES_TO]->(:Service {name:'payment-api'})
MERGE (ct_restart) -[:APPLIES_TO]->(:Service {name:'user-api'})
MERGE (gw_reload)  -[:APPLIES_TO]->(:Service {name:'api-gateway'})
```

### 5.3 Core Cypher Queries (Agent uses these)

#### Q1 — Multi-hop root cause traversal
```cypher
// Given: alert from $alert_service with $error_type
// Returns: the unhealthy root node and full dependency chain
MATCH path = (root:Service)-[:DEPENDS_ON*1..6]->(alert:Service {name: $alert_service})
WHERE root.status <> 'HEALTHY'
  AND NOT (root)<-[:DEPENDS_ON]-(:Service {status: 'UNHEALTHY'})
RETURN root,
       [n IN nodes(path) | n.name] AS dependency_chain,
       length(path) AS depth
ORDER BY depth DESC
LIMIT 1
```

#### Q2 — Retrieve SOP for root cause
```cypher
MATCH (svc:Service {name: $root_node})<-[:APPLIES_TO]-(skill:Skill)
WHERE skill.trigger_condition = $error_type
RETURN skill.name          AS skill_name,
       skill.script_path   AS script_path,
       skill.script_type   AS script_type,
       skill.description   AS description,
       skill.params        AS params,
       skill.timeout_seconds AS timeout
```

#### Q3 — Get next SOP in failure chain
```cypher
MATCH (:Skill {name: $current_skill})-[:NEXT_IF_FAIL]->(next:Skill)
RETURN next.name AS next_skill,
       next.script_path AS script_path,
       next.description AS description
```

#### Q4 — Update service health status
```cypher
MATCH (s:Service {name: $service_name})
SET s.status = $new_status,
    s.error_code = $error_code,
    s.last_updated = datetime()
RETURN s
```

#### Q5 — Check all-healthy for loop termination
```cypher
MATCH (s:Service)
WHERE s.name IN $affected_services
  AND s.status <> 'HEALTHY'
RETURN count(s) AS still_unhealthy
```

### 5.4 Phase 2 Validation
```bash
# Load init scripts into Neo4j
docker cp neo4j/init/01_infrastructure_graph.cypher neo4j:/import/
docker cp neo4j/init/02_skill_graph.cypher neo4j:/import/

# Run via cypher-shell
docker exec neo4j cypher-shell -u neo4j -p $NEO4J_PASSWORD \
  --file /import/01_infrastructure_graph.cypher

# Validate graph exists
docker exec neo4j cypher-shell -u neo4j -p $NEO4J_PASSWORD \
  "MATCH (n) RETURN labels(n), count(n)"
```

Expected output:
```
labels(n) | count(n)
Service   | 6
Skill     | 8
```

---

## 6. Phase 3 — Simulated Microservice Environment

**Duration:** 2 weeks  
**Goal:** A realistic 6-service Docker Compose stack that mimics a production cloud environment.

### 6.1 Services to Build

| Service | Tech | Failure Modes to Simulate |
|---|---|---|
| `postgres-db` | postgres:15-alpine | Deadlock, connection exhaustion, slow queries |
| `redis-cache` | redis:7-alpine | OOM, stale data after DB recovery, connection refused |
| `payment-api` | FastAPI (Python) | High error rate when DB/cache unavailable |
| `user-api` | FastAPI (Python) | Dependency on postgres-db |
| `api-gateway` | nginx:1.25-alpine | Config stale, upstream unavailable |
| `web-frontend` | FastAPI (Python) | HTTP 503 when api-gateway upstream fails |

### 6.2 Sample Service — `payment-api`

```python
# sim/payment/app.py
from fastapi import FastAPI, HTTPException
import psycopg2
import redis
import os, time

app = FastAPI()

def get_db():
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=5432,
        dbname="appdb", user="appuser",
        password=os.environ["DB_PASSWORD"],
        connect_timeout=5
    )

def get_cache():
    return redis.Redis(host=os.environ["REDIS_HOST"],
                       port=6379, socket_timeout=2)

@app.get("/pay/{amount}")
def process_payment(amount: float):
    try:
        cache = get_cache()
        cached = cache.get(f"rate:{amount}")
        if cached:
            return {"status": "ok", "source": "cache", "amount": float(cached)}

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT processing_rate FROM fees LIMIT 1")
        rate = cur.fetchone()[0]
        cache.setex(f"rate:{amount}", 60, str(rate))
        return {"status": "ok", "source": "db", "amount": amount * rate}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/health")
def health():
    return {"status": "healthy"}
```

### 6.3 Chaos Injection Design

```python
# chaos/injector.py
# Called by the chaos-injector container to simulate faults

import docker, psycopg2, time, requests, os

client = docker.from_env()
ALERT_WEBHOOK = os.environ["ALERT_WEBHOOK"]

def inject_postgres_deadlock():
    """Creates two competing transactions that deadlock each other."""
    conn1 = psycopg2.connect(host="postgres-db", ...)
    conn2 = psycopg2.connect(host="postgres-db", ...)
    # Transaction 1 locks row A, Transaction 2 locks row B
    # Then each tries to lock the other's row → deadlock
    # PostgreSQL will kill one and raise ERROR 40P01
    ...
    _send_alert("postgres-db", "DEADLOCK_ERROR",
                "Connection pool exhausted — deadlock on table 'fees'")

def inject_redis_oom():
    """Fills Redis memory to trigger OOM eviction."""
    r = redis.Redis(host="redis-cache")
    for i in range(100_000):
        r.set(f"bloat:{i}", "x" * 1024)  # ~100 MB
    _send_alert("redis-cache", "OOM_KILLED",
                "Redis maxmemory reached, keys evicted")

def _send_alert(service: str, error_type: str, message: str):
    payload = {
        "alert_id":    f"INC-{int(time.time())}",
        "service":     service,
        "error_type":  error_type,
        "message":     message,
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "severity":    "CRITICAL"
    }
    requests.post(ALERT_WEBHOOK, json=payload, timeout=5)
```

### 6.4 Scenario Library (build these progressively)

| Scenario ID | Fault Injected | Services Affected | Expected Resolution Chain |
|---|---|---|---|
| S-01 | PostgreSQL deadlock | postgres→redis→payment→frontend | Postgres_Deadlock_Kill_SOP → Redis_Flush_SOP |
| S-02 | Redis OOM | redis→payment→frontend | Redis_Restart_SOP → Container_Restart_SOP |
| S-03 | payment-api crash loop | payment→api-gateway→frontend | Container_Restart_SOP |
| S-04 | Nginx config stale | api-gateway→frontend | APIGateway_Reload_SOP |
| S-05 | postgres DOWN | postgres→all | Postgres_Restart_SOP |
| S-06 | Cascading (S-01 + cache stale) | full stack | 4-hop multi-step resolution |

---

## 7. Phase 4 — LangGraph Agentic Brain

**Duration:** 4 weeks  
**Goal:** A working LangGraph StateGraph that ingests alerts, queries Neo4j, reasons with the LLM, and produces execution plans.

### 7.1 Agent State Schema

```python
# agent/state.py
from typing import TypedDict, List, Optional, Dict, Any

class AgentState(TypedDict):
    # ── Input ──────────────────────────────
    alert_payload:      Dict[str, Any]   # Raw alert JSON from webhook
    alert_service:      str              # e.g. "web-frontend"
    alert_error_type:   str              # e.g. "HTTP_TIMEOUT"

    # ── Graph traversal ───────────────────
    root_cause_node:    Optional[str]    # Identified root service
    dependency_chain:   List[str]        # [postgres-db, redis-cache, ..., web-frontend]
    current_skill:      Optional[str]    # SOP name currently being executed
    script_path:        Optional[str]    # Filesystem path to SOP script

    # ── Execution tracking ────────────────
    execution_history:  List[Dict]       # [{skill, exit_code, stdout, stderr, timestamp}]
    visited_skills:     List[str]        # Prevent re-visiting same SOP
    attempt_count:      int
    max_attempts:       int              # Hard limit (default: 5)

    # ── Health tracking ───────────────────
    service_health_map: Dict[str, str]   # {service_name: HEALTHY|DEGRADED|DOWN}
    all_healthy:        bool

    # ── Output ────────────────────────────
    rca_report:         Optional[Dict]
    error_message:      Optional[str]
```

### 7.2 LangGraph Node Functions

```python
# agent/nodes/orchestrator.py
from agent.state import AgentState

def ingest_alert(state: AgentState) -> AgentState:
    """Layer 1: Parse alert payload, initialise traversal state."""
    payload = state["alert_payload"]
    return {
        **state,
        "alert_service":    payload["service"],
        "alert_error_type": payload["error_type"],
        "visited_skills":   [],
        "execution_history":[],
        "attempt_count":    0,
        "max_attempts":     state.get("max_attempts", 5),
        "all_healthy":      False,
    }
```

```python
# agent/nodes/graph_retriever.py
from neo4j import GraphDatabase
from agent.state import AgentState

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def retrieve_root_cause(state: AgentState) -> AgentState:
    """Layer 2: Multi-hop traversal to find root cause node."""
    with driver.session() as session:
        result = session.run("""
            MATCH path = (root:Service)-[:DEPENDS_ON*1..6]->
                         (alert:Service {name: $alert_service})
            WHERE root.status <> 'HEALTHY'
            RETURN root.name AS root_node,
                   [n IN nodes(path) | n.name] AS chain
            ORDER BY length(path) DESC LIMIT 1
        """, alert_service=state["alert_service"])
        record = result.single()

        if not record:
            # No graph path found — alert service IS the root
            return {**state, "root_cause_node": state["alert_service"],
                    "dependency_chain": [state["alert_service"]]}

        return {**state,
                "root_cause_node":  record["root_node"],
                "dependency_chain": record["chain"]}

def retrieve_skill(state: AgentState) -> AgentState:
    """Layer 2: Get the SOP skill for the identified root cause."""
    with driver.session() as session:
        result = session.run("""
            MATCH (svc:Service {name: $root})<-[:APPLIES_TO]-(skill:Skill)
            WHERE skill.trigger_condition = $error_type
              AND NOT skill.name IN $visited
            RETURN skill.name AS name, skill.script_path AS path,
                   skill.description AS desc, skill.timeout_seconds AS timeout
            LIMIT 1
        """, root=state["root_cause_node"],
             error_type=state["alert_error_type"],
             visited=state["visited_skills"])
        record = result.single()

        if not record:
            return {**state, "current_skill": None, "script_path": None}

        return {**state,
                "current_skill": record["name"],
                "script_path":   record["path"]}
```

```python
# agent/nodes/llm_reasoner.py
from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage
from agent.state import AgentState
import json

llm = ChatOpenAI(model="gpt-4o", temperature=0)

SYSTEM_PROMPT = """You are an autonomous SRE agent performing root cause analysis.
You will be given:
1. The alert that triggered the investigation
2. The identified root cause service and dependency chain
3. A single SOP (Standard Operating Procedure) to evaluate

Your job: Decide whether to execute this SOP or request the next one.
Respond ONLY with valid JSON: {"action": "execute"|"skip"|"escalate", "reason": "..."}
"""

def llm_decide(state: AgentState) -> AgentState:
    """Layer 3: LLM evaluates the current SOP context."""
    context = {
        "alert":            state["alert_payload"],
        "root_cause":       state["root_cause_node"],
        "chain":            state["dependency_chain"],
        "current_sop":      state["current_skill"],
        "sop_path":         state["script_path"],
        "attempt":          state["attempt_count"],
        "previous_attempts": state["execution_history"][-2:]  # last 2 only
    }
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(context, indent=2))
    ])
    decision = json.loads(response.content)
    return {**state, "llm_decision": decision["action"],
            "llm_reason": decision["reason"]}
```

### 7.3 LangGraph StateGraph Definition

```python
# agent/graph.py
from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes.orchestrator import ingest_alert
from agent.nodes.graph_retriever import retrieve_root_cause, retrieve_skill
from agent.nodes.llm_reasoner import llm_decide
from agent.nodes.executor import execute_sop        # Phase 5
from agent.nodes.evaluator import evaluate_health   # Phase 5

def should_continue(state: AgentState) -> str:
    """Conditional edge: what to do after evaluation."""
    if state["all_healthy"]:
        return "generate_report"
    if state["attempt_count"] >= state["max_attempts"]:
        return "escalate"
    if not state["current_skill"]:
        return "escalate"          # No more skills in chain
    return "retrieve_skill"        # Loop: get next skill

workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("ingest_alert",       ingest_alert)
workflow.add_node("retrieve_root_cause",retrieve_root_cause)
workflow.add_node("retrieve_skill",     retrieve_skill)
workflow.add_node("llm_decide",         llm_decide)
workflow.add_node("execute_sop",        execute_sop)        # Phase 5
workflow.add_node("evaluate_health",    evaluate_health)    # Phase 5
workflow.add_node("generate_report",    generate_report)

# Define flow
workflow.set_entry_point("ingest_alert")
workflow.add_edge("ingest_alert",        "retrieve_root_cause")
workflow.add_edge("retrieve_root_cause", "retrieve_skill")
workflow.add_edge("retrieve_skill",      "llm_decide")
workflow.add_edge("llm_decide",          "execute_sop")
workflow.add_edge("execute_sop",         "evaluate_health")
workflow.add_conditional_edges("evaluate_health", should_continue, {
    "retrieve_skill":  "retrieve_skill",
    "generate_report": "generate_report",
    "escalate":        END
})
workflow.add_edge("generate_report", END)

agent = workflow.compile()
```

### 7.4 Alert Webhook Server

```python
# agent/main.py
from fastapi import FastAPI
from pydantic import BaseModel
from agent.graph import agent
from agent.state import AgentState

app = FastAPI()

class Alert(BaseModel):
    alert_id:   str
    service:    str
    error_type: str
    message:    str
    severity:   str

@app.post("/alert")
async def handle_alert(alert: Alert):
    initial_state: AgentState = {
        "alert_payload":    alert.dict(),
        "alert_service":    alert.service,
        "alert_error_type": alert.error_type,
        "visited_skills":   [],
        "execution_history":[], "attempt_count": 0,
        "max_attempts":     5,  "all_healthy": False,
        "root_cause_node":  None, "dependency_chain": [],
        "current_skill":    None, "script_path": None,
        "service_health_map": {}, "rca_report": None,
        "error_message":    None
    }
    result = await agent.ainvoke(initial_state)
    return {"status": "resolved" if result["all_healthy"] else "escalated",
            "report": result.get("rca_report")}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
```

### 7.5 Phase 4 Milestones
- [ ] Agent receives a hardcoded mock alert and produces a root cause node
- [ ] Cypher Q1 (traversal) returns correct chain for each test scenario
- [ ] Cypher Q2 (skill retrieval) returns the correct SOP for each fault type
- [ ] LLM returns valid JSON decision (not free text)
- [ ] LangGraph graph compiles without errors: `agent.get_graph().print_ascii()`

---

## 8. Phase 5 — Docker Execution Sandbox (OpenClaw)

**Duration:** 2 weeks  
**Goal:** Agent executes SOP scripts in isolated containers with captured telemetry, no host exposure.

### 8.1 Sandbox Security Constraints

```bash
# Full Docker run command generated by OpenClaw for each SOP execution
docker run \
  --rm \                             # Destroy container on exit
  --name sop-run-$(uuidgen) \        # Unique name per run
  --network sim-net \                # Can reach microservices — NOT agent-net/internet
  --cap-drop ALL \                   # Drop all Linux capabilities
  --cap-add NET_BIND_SERVICE \       # Only: bind to network ports
  --security-opt no-new-privileges \ # Prevent privilege escalation
  --read-only \                      # Read-only root filesystem
  --tmpfs /tmp:size=64m \            # Writable temp space (64 MB max)
  --memory=256m \                    # Max RAM
  --memory-swap=256m \               # No swap extension
  --cpus=0.5 \                       # Max 0.5 CPU cores
  --pids-limit=50 \                  # Max 50 processes
  --stop-timeout=60 \                # Hard kill after 60 seconds
  -v /sops/${SCRIPT_NAME}:/script/${SCRIPT_NAME}:ro \  # Mount only this script
  -e DB_HOST=postgres-db \           # Inject only required env vars
  -e DB_PORT=5432 \
  sop-executor:latest \
  python /script/${SCRIPT_NAME} ${PARAMS}
```

### 8.2 SOP Executor Base Image

```dockerfile
# sop-executor/Dockerfile
FROM python:3.11-slim

# Only dependencies SOPs might need
RUN pip install --no-cache-dir \
    psycopg2-binary==2.9.9 \
    redis==5.0.8 \
    requests==2.32.3 \
    docker==7.1.0

# Non-root user for extra security
RUN useradd -m sopuser
USER sopuser

WORKDIR /script
```

### 8.3 Sample SOP Scripts

```python
# sops/postgres/deadlock_kill.py
"""Kill all blocking transactions in PostgreSQL."""
import psycopg2, os, sys, json

def main():
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ.get("DB_NAME", "appdb"),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"]
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Find blocking PIDs
    cur.execute("""
        SELECT pid, query, wait_event_type, wait_event
        FROM pg_stat_activity
        WHERE wait_event_type = 'Lock'
          AND state = 'active'
    """)
    blockers = cur.fetchall()

    killed = []
    for pid, query, *_ in blockers:
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        killed.append(pid)

    # Verify
    cur.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
    remaining = cur.fetchone()[0]

    result = {"pids_killed": killed, "deadlocks_remaining": remaining,
              "success": remaining == 0}
    print(json.dumps(result))
    sys.exit(0 if result["success"] else 1)

if __name__ == "__main__":
    main()
```

```bash
#!/bin/bash
# sops/redis/cache_flush.sh
# Flush all Redis keys to clear stale data after DB recovery

set -euo pipefail

HOST="${REDIS_HOST:-redis-cache}"
PORT="${REDIS_PORT:-6379}"

echo "Connecting to Redis at $HOST:$PORT"
KEY_COUNT=$(redis-cli -h "$HOST" -p "$PORT" DBSIZE)
echo "Keys before flush: $KEY_COUNT"

redis-cli -h "$HOST" -p "$PORT" FLUSHALL ASYNC

sleep 1
KEY_COUNT_AFTER=$(redis-cli -h "$HOST" -p "$PORT" DBSIZE)
echo "Keys after flush: $KEY_COUNT_AFTER"

if [ "$KEY_COUNT_AFTER" -eq 0 ]; then
  echo '{"success": true, "keys_flushed": '"$KEY_COUNT"'}'
  exit 0
else
  echo '{"success": false, "keys_remaining": '"$KEY_COUNT_AFTER"'}'
  exit 1
fi
```

### 8.4 OpenClaw Integration Layer

```python
# agent/tools/openclaw.py
"""Mediates between LangGraph agent and Docker daemon."""
import docker, json, uuid
from langchain.tools import tool

docker_client = docker.DockerClient(base_url="tcp://docker-daemon:2375")

@tool
def execute_sop(script_path: str, env_vars: dict, timeout: int = 60) -> dict:
    """
    Execute an SOP script inside an isolated Docker sandbox.
    Returns stdout, stderr, exit_code from the container.
    """
    script_name = script_path.split("/")[-1]
    script_type = "python" if script_path.endswith(".py") else "bash"
    cmd = ["python", f"/script/{script_name}"] if script_type == "python" \
          else ["bash", f"/script/{script_name}"]

    container = docker_client.containers.run(
        image="sop-executor:latest",
        command=cmd,
        name=f"sop-run-{uuid.uuid4().hex[:8]}",
        network="sim-net",
        cap_drop=["ALL"],
        cap_add=["NET_BIND_SERVICE"],
        security_opt=["no-new-privileges"],
        read_only=True,
        tmpfs={"/tmp": "size=64m"},
        mem_limit="256m",
        memswap_limit="256m",
        nano_cpus=500_000_000,    # 0.5 CPUs
        pids_limit=50,
        volumes={
            script_path: {"bind": f"/script/{script_name}", "mode": "ro"}
        },
        environment=env_vars,
        remove=True,
        detach=False,
        stdout=True,
        stderr=True,
        stop_signal="SIGKILL"
    )

    # container.run() returns bytes when detach=False
    stdout = container.decode("utf-8") if isinstance(container, bytes) else ""
    return {"stdout": stdout, "exit_code": 0, "success": True}
```

### 8.5 Evaluator Node

```python
# agent/nodes/evaluator.py
from agent.state import AgentState
from neo4j import GraphDatabase
import json

driver = GraphDatabase.driver(...)

def evaluate_health(state: AgentState) -> AgentState:
    """Check if all affected services are healthy after script execution."""
    execution = state["execution_history"][-1] if state["execution_history"] else {}

    # Mark current skill as visited regardless of outcome
    visited = state["visited_skills"] + [state["current_skill"]]

    with driver.session() as session:
        result = session.run("""
            MATCH (s:Service)
            WHERE s.name IN $services AND s.status <> 'HEALTHY'
            RETURN count(s) AS unhealthy_count
        """, services=state["dependency_chain"])
        unhealthy = result.single()["unhealthy_count"]

    return {
        **state,
        "all_healthy":    unhealthy == 0,
        "visited_skills": visited,
        "attempt_count":  state["attempt_count"] + 1,
    }
```

---

## 9. Phase 6 — Chaos Engineering & End-to-End Integration

**Duration:** 3 weeks  
**Goal:** Agent autonomously resolves all 6 fault scenarios with no human intervention.

### 9.1 Integration Test Matrix

| Scenario | Fault | Hops Expected | Pass Condition |
|---|---|---|---|
| S-01 | PG deadlock | 2 | All services healthy, MTTR < 120s |
| S-02 | Redis OOM | 2 | Redis healthy, no stale keys |
| S-03 | Payment crash | 1 | Container restarted, 200 response |
| S-04 | Nginx config | 1 | Gateway reloaded, routes working |
| S-05 | PG DOWN | 1 | Postgres restarted, connections restored |
| S-06 | Cascade (S-01→stale) | 4 | Full stack healthy, 4-hop traversal |

### 9.2 End-to-End Test Runner

```python
# eval/scripts/run_e2e_tests.py
import requests, time, json
from chaos.injector import SCENARIOS

AGENT_URL = "http://localhost:8888/alert"
RESULTS = []

for scenario in SCENARIOS:
    print(f"\n[TEST] {scenario['id']}: {scenario['name']}")

    # Inject fault
    scenario["inject_fn"]()
    t_start = time.time()

    # Wait for agent to resolve (poll health endpoint)
    resolved = False
    for _ in range(60):   # 60 × 2s = 2 min timeout
        time.sleep(2)
        r = requests.get("http://web-frontend:3000/health")
        if r.status_code == 200:
            resolved = True
            break

    t_end = time.time()
    mttr = t_end - t_start

    RESULTS.append({
        "scenario": scenario["id"],
        "resolved": resolved,
        "mttr_seconds": round(mttr, 2)
    })
    print(f"  Resolved: {resolved} | MTTR: {mttr:.1f}s")

with open("eval/results/e2e_results.json", "w") as f:
    json.dump(RESULTS, f, indent=2)
```

### 9.3 Logging & Observability

```python
# agent/utils/logger.py
import structlog, json
from datetime import datetime

log = structlog.get_logger()

def log_hop(state: dict, hop_num: int):
    log.info("agent_hop",
             hop=hop_num,
             root_cause=state.get("root_cause_node"),
             skill=state.get("current_skill"),
             attempt=state.get("attempt_count"),
             all_healthy=state.get("all_healthy"))

def write_rca_report(state: dict) -> dict:
    report = {
        "alert_id":          state["alert_payload"]["alert_id"],
        "root_cause":        state["root_cause_node"],
        "dependency_chain":  state["dependency_chain"],
        "skills_executed":   [e["skill"] for e in state["execution_history"]],
        "total_hops":        state["attempt_count"],
        "all_healthy":       state["all_healthy"],
        "resolution_time_s": None,   # Set by caller
        "timestamp":         datetime.utcnow().isoformat()
    }
    path = f"/audit/rca_{report['alert_id']}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    return report
```

---

## 10. Phase 7 — Evaluation & Benchmarking

**Duration:** 3 weeks  
**Goal:** Produce quantitative comparison against two baselines, prove the project's claims.

### 10.1 Baseline Systems to Implement

| Baseline | Description | Implementation |
|---|---|---|
| **B1: Zero-Shot LLM** | Feed raw alert + all logs directly to GPT-4o, ask for fix command | Single API call, no graph, no sandbox |
| **B2: Vector RAG** | Embed all SOP text docs into FAISS, retrieve top-k, feed to LLM | FAISS + SentenceTransformers + LLM |
| **Ours: Agentic GraphRAG** | Full system as built | Full pipeline |

### 10.2 Metrics Definition

#### SRE Operational Metrics (Business Impact)

| Metric | Formula | Target |
|---|---|---|
| **MTTR** | `t_resolved - t_alert_fired` (seconds) | ≥60% reduction vs. baselines |
| **MTTD** | `t_root_cause_identified - t_alert_fired` | < 10s for known fault types |
| **Auto-Resolution Rate** | `resolved / total_scenarios × 100` | ≥80% |
| **False Root Cause Rate** | `wrong_root / total_scenarios × 100` | ≤10% |

#### AI Cognitive Metrics (System Quality)

| Metric | Formula | Target |
|---|---|---|
| **Tool Hallucination Rate** | `invalid_tool_calls / total_tool_calls × 100` | 0% |
| **Multi-Hop Success Rate** | `scenarios_needing_3+_hops_resolved / total` | ≥80% |
| **Token Efficiency** | `avg_tokens_per_call (ours vs B2)` | ≥70% fewer tokens |
| **Context Precision** | `relevant_nodes_retrieved / total_nodes_retrieved` | ≥90% |

### 10.3 Benchmarking Script

```python
# eval/scripts/benchmark.py
import time, json, os
from eval.baselines.zero_shot import ZeroShotBaseline
from eval.baselines.vector_rag import VectorRAGBaseline
from agent.graph import agent as agentic_graphrag

SCENARIOS = json.load(open("eval/scenarios.json"))
RESULTS = {"zero_shot": [], "vector_rag": [], "agentic_graphrag": []}

for scenario in SCENARIOS:
    for name, system in [
        ("zero_shot",       ZeroShotBaseline()),
        ("vector_rag",      VectorRAGBaseline()),
        ("agentic_graphrag", agentic_graphrag)
    ]:
        # Inject fault
        scenario["inject_fn"]()
        t0 = time.time()

        # Run system
        result = system.resolve(scenario["alert_payload"])
        t1 = time.time()

        # Reset environment
        scenario["reset_fn"]()

        RESULTS[name].append({
            "scenario":     scenario["id"],
            "mttr":         round(t1 - t0, 2),
            "resolved":     result.get("resolved", False),
            "root_correct": result.get("root_cause") == scenario["true_root"],
            "tokens_used":  result.get("tokens_used", 0),
            "hops":         result.get("hops", 0)
        })

with open("eval/results/benchmark.json", "w") as f:
    json.dump(RESULTS, f, indent=2)
print("Benchmark complete.")
```

### 10.4 Expected Results Table (Template)

| Metric | Zero-Shot LLM | Vector RAG | Agentic GraphRAG (Ours) |
|---|---|---|---|
| Avg MTTR (s) | ~240 | ~180 | ~**47** |
| Auto-Resolution Rate | ~30% | ~55% | ~**90%** |
| Tool Hallucination Rate | ~40% | ~25% | **0%** |
| Multi-Hop Success (3+ hops) | ~10% | ~20% | ~**80%** |
| Avg Tokens / Call | ~4,200 | ~2,800 | ~**620** |
| False Root Cause Rate | ~45% | ~30% | ~**8%** |

> Fill this table with actual numbers from your evaluation runs.

### 10.5 Ablation Study (Optional but Strong for Paper)

Run your system with components removed to prove each one matters:

| Ablation | What's Removed | Hypothesis |
|---|---|---|
| **No Skill Graph** | Remove Skill Graph, use text SOPs only | MTTR increases, hallucination rises |
| **No Infrastructure Graph** | Remove KG, use service name lookup only | False root cause rate increases |
| **No Progressive Injection** | Feed full graph context per LLM call | Token usage increases, latency spikes |
| **No Sandbox** | Execute directly (mocked) | Security risk visible, same MTTR |
| **No ReAct Loop** | Single-pass execution, no verify | Multi-hop failures not caught |

---

## 11. Phase 8 — Paper, Report & Final Presentation

**Duration:** 3 weeks

### 11.1 MTech Seminar Report Chapters

| Chapter | Content |
|---|---|
| 1. Introduction | Problem statement, motivation, contributions |
| 2. Literature Review | 8-paper survey table, identified gaps |
| 3. System Design | Dual-graph architecture, 5-layer design, state machine |
| 4. Implementation | Neo4j schema, LangGraph code, Docker sandbox, SOP scripts |
| 5. Evaluation | Baselines, metrics, results table, ablation study |
| 6. Discussion | Why graph beats vector RAG, security analysis, limitations |
| 7. Conclusion & Future Work | Summary, extensions (Kubernetes, real infra, multi-cluster) |
| References | 8+ IEEE/ACM citations |

### 11.2 Figures to Create

- [ ] High-level architecture diagram (4-block overview)
- [ ] 5-layer internal architecture diagram
- [ ] Neo4j dual-graph schema diagram (nodes + edge types)
- [ ] LangGraph state machine flowchart
- [ ] Multi-hop execution trace (Postgres → Redis → WebApp)
- [ ] MTTR comparison bar chart (3 baselines)
- [ ] Token efficiency comparison chart
- [ ] Confusion matrix for root cause identification

### 11.3 Seminar Presentation Outline (12 slides)

| Slide | Title | Key Content |
|---|---|---|
| 1 | Title | Project title, student, guide |
| 2 | The Problem | Alert storm, cascading failures, human latency |
| 3 | Why Current AI Fails | 3-column comparison table (Zero-Shot / Vector RAG / Ours) |
| 4 | Proposed Solution | Dual-graph concept, progressive context injection |
| 5 | System Architecture | 4-block + 5-layer diagram |
| 6 | Graph Design | KG schema + Skill graph schema in Neo4j |
| 7 | Execution Flow | 4-hop Postgres → Redis example (step-by-step) |
| 8 | Docker Sandbox | Security flags, OpenClaw, isolation model |
| 9 | Implementation | Tech stack table, key code snippets |
| 10 | Results | MTTR/hallucination/resolution rate charts |
| 11 | Ablation | What happens when each component is removed |
| 12 | Conclusion | Contribution, future work |

---

## 12. Full Tech Stack Reference

| Category | Tool | Version | Purpose |
|---|---|---|---|
| **Orchestration** | LangGraph | 0.2.x | Stateful multi-hop ReAct agent |
| **LLM Framework** | LangChain | 0.2.x | LLM abstraction, tool binding |
| **LLM (Cloud)** | GPT-4o / Codex 3.5 | latest | Cognitive reasoning engine |
| **LLM (Local)** | Qwen 2.5 Coder 7B | latest | Via Ollama, tool-calling capable |
| **Graph DB** | Neo4j | 5.18 | Dual-graph store |
| **Graph Query** | Cypher | — | Multi-hop traversal language |
| **Python Neo4j Driver** | neo4j | 5.24.0 | Driver for Cypher from Python |
| **Containerization** | Docker + DinD | 24+ | Execution sandbox |
| **Compose** | Docker Compose | v2 | Full stack orchestration |
| **Tool Calling** | OpenClaw | latest | LLM ↔ Docker mediation |
| **API Server** | FastAPI | 0.115 | Alert ingestion webhook |
| **Simulation** | Docker Compose | v2 | Fake microservice environment |
| **Chaos Eng** | Pumba / custom | — | Fault injection |
| **Validation** | Pydantic | 2.8 | State schema validation |
| **Logging** | structlog | 24.x | Structured JSON logs |
| **Eval / Viz** | Pandas + Matplotlib | latest | Results analysis + charts |
| **Testing** | pytest | 8.x | Unit + integration tests |
| **Dev** | VS Code + Remote Containers | — | Devcontainer workflow |

---

## 13. Metrics & KPI Definitions

### Operational Metrics
```
MTTR = time_system_restored - time_alert_fired  (seconds)
MTTD = time_root_cause_identified - time_alert_fired  (seconds)
Auto-Resolution Rate = count(scenarios resolved autonomously) / count(total scenarios)
False Root Cause Rate = count(wrong root identified) / count(total scenarios)
```

### AI Cognitive Metrics
```
Tool Hallucination Rate = count(LLM called non-existent/invalid tool) / count(total tool calls)
Multi-Hop Success = count(3+-hop scenarios resolved) / count(total 3+-hop scenarios)
Token Efficiency = avg_tokens_per_call(ours) vs avg_tokens_per_call(baseline)
Context Precision = relevant_nodes_retrieved / total_nodes_in_context
```

### How to Instrument
```python
# agent/utils/metrics.py
from dataclasses import dataclass, field
from typing import List
import time

@dataclass
class IncidentMetrics:
    alert_id:          str
    t_alert:           float = field(default_factory=time.time)
    t_root_identified: float = None
    t_resolved:        float = None
    hops:              int = 0
    tokens_used:       int = 0
    tool_calls:        List[str] = field(default_factory=list)
    invalid_calls:     int = 0
    resolved:          bool = False
    true_root:         str = None
    identified_root:   str = None

    @property
    def mttr(self): return self.t_resolved - self.t_alert if self.t_resolved else None
    @property
    def mttd(self): return self.t_root_identified - self.t_alert if self.t_root_identified else None
    @property
    def hallucination_rate(self):
        return self.invalid_calls / len(self.tool_calls) if self.tool_calls else 0
    @property
    def root_correct(self): return self.identified_root == self.true_root
```

---

## 14. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Neo4j performance on complex multi-hop queries | Medium | High | Add APOC indexes; limit traversal depth to 6 |
| LLM API rate limits / cost overrun | Medium | Medium | Use local Qwen for dev; GPT-4o only for eval |
| DinD security concerns in real environment | Low | High | Document limitation; use Docker socket with allowlist for prod |
| Docker sandbox escape (script attacks host) | Low | High | `--cap-drop ALL`, `--read-only`, no-new-privileges, network isolation |
| OpenClaw instability / missing features | Medium | Medium | Fall back to direct Docker SDK calls if needed |
| Simulation not realistic enough for strong claims | High | High | Implement 6+ distinct fault types; use Minikube for Kubernetes validation |
| LangGraph state corruption in long loops | Low | Medium | Checkpoint after every node; add visited_skills registry |
| LLM returns non-JSON / malformed tool calls | Medium | Medium | Pydantic validation on every LLM output; retry logic (max 3 retries) |

---

## 15. Project Directory Structure

```
agentic-graphrag-rca/
│
├── agent/                          # LangGraph agent (Phase 4)
│   ├── Dockerfile
│   ├── main.py                     # FastAPI alert webhook
│   ├── graph.py                    # LangGraph StateGraph definition
│   ├── state.py                    # AgentState TypedDict
│   ├── nodes/
│   │   ├── orchestrator.py         # Layer 1: ingest_alert
│   │   ├── graph_retriever.py      # Layer 2: retrieve_root_cause, retrieve_skill
│   │   ├── llm_reasoner.py         # Layer 3: llm_decide
│   │   ├── executor.py             # Layer 4: execute_sop (calls OpenClaw)
│   │   ├── evaluator.py            # Layer 5: evaluate_health
│   │   └── reporter.py             # generate_rca_report
│   ├── tools/
│   │   └── openclaw.py             # Docker sandbox interface
│   └── utils/
│       ├── logger.py               # structlog configuration
│       └── metrics.py              # IncidentMetrics dataclass
│
├── neo4j/
│   ├── init/
│   │   ├── 01_infrastructure_graph.cypher
│   │   └── 02_skill_graph.cypher
│   └── import/                     # CSV imports if needed
│
├── sops/                           # SOP scripts (read-only mount in sandbox)
│   ├── postgres/
│   │   ├── deadlock_kill.py
│   │   ├── restart.sh
│   │   └── vacuum.py
│   ├── redis/
│   │   ├── cache_flush.sh
│   │   └── restart.sh
│   ├── container/
│   │   ├── restart.sh
│   │   └── scale_up.sh
│   └── api/
│       └── nginx_reload.sh
│
├── sop-executor/                   # Base image for sandbox containers
│   └── Dockerfile
│
├── sim/                            # Simulated microservices (Phase 3)
│   ├── payment/app.py
│   ├── user/app.py
│   ├── frontend/app.py
│   └── nginx/nginx.conf
│
├── chaos/                          # Chaos engineering (Phase 6)
│   ├── injector.py
│   └── scenarios.json
│
├── eval/                           # Evaluation (Phase 7)
│   ├── baselines/
│   │   ├── zero_shot.py
│   │   └── vector_rag.py
│   ├── scripts/
│   │   ├── run_e2e_tests.py
│   │   └── benchmark.py
│   ├── results/
│   │   ├── benchmark.json
│   │   └── e2e_results.json
│   └── scenarios.json
│
├── docs/
│   ├── architecture_decisions.md
│   ├── literature_survey.md
│   └── Project_Roadmap.md          ← THIS FILE
│
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

*Generated for Raghav Nimbalkar · Agentic GraphRAG RCA · MIT-WPU · 2025-2026*  
*Last updated: June 2026*