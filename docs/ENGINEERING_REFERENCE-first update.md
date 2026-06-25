# Agentic GraphRAG — Master Engineering Reference

![Status](https://img.shields.io/badge/Status-Phase_7_Complete-green?style=flat-square)
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
| Phase 4 | LangGraph agent core — ingest → retrieve → reason → execute → evaluate | ✅ Complete | Full ReAct loop verified; FastAPI webhook on port 8888 |
| Phase 5 | Docker sandbox + SOP scripts — all fault types | ✅ Complete | redis_oom ✅ service_crash ✅ network_partition ✅ — all RESOLVED autonomously |
| Phase 6 | End-to-end chaos integration + ground-truth scenarios | ✅ Complete | `eval/scenarios.json` — 3 verified scenarios with blast-radius from live Neo4j |
| Phase 6.5 | Streamlit dashboard | ✅ Complete | `dashboard/app.py` — 3 tabs (live RCA console, incident history, eval results); live red→green graph via Neo4j polling; verified inject→resolve through UI |
| Phase 7 | Evaluation — RQ1/RQ2 baselines + benchmark | ✅ Complete | blast-F1: GraphRAG=1.000 vs B1=B2=0.739; `eval/results/EVALUATION_SUMMARY.md` |
| Phase 8 | Report + final presentation | ⬜ Pending | — |
| Phase 9 | Closed-loop upgrade — real detection, verification, fallback chains | ✅ Complete | telemetry collector + real verification + NEXT_IF_FAIL + 2 new skills + live dashboard; see "Closed-Loop Upgrade" below |

---

## Closed-Loop Upgrade (2026-06-24) — making detection & verification real

The investigation (see investigation report) found the system *appeared* autonomous
but had **faked detection** (the injector hand-wrote the alert with a trigger
guaranteed to match a SOP) and **faked verification** (RESOLVED was an optimistic
Neo4j flip on sandbox exit-code 0, never a real health re-check). This phase closes
the loop. Five steps, each verified end-to-end before the next.

### Step 1 — Real telemetry ✅ DONE & VERIFIED

`simulation/telemetry_collector.py` (new) — a standalone process polling every 5s:

- For each of the 12 services: Docker SDK `inspect` → `State.Status`; network
  attachment to `boutique-sim`; for `redis-cart`, live `redis-cli ping` +
  `CONFIG GET maxmemory`.
- Maps real state → `ServiceStatus`: not running → `CRASH_LOOPING`; off network →
  `CONNECTION_REFUSED`; redis ping fail / maxmemory in (1, 10MB] → `OOM_KILLED`;
  else `HEALTHY`. (STALE_DATA + HIGH_CPU hooks added in Step 4.)
- **Level-triggered sync:** if observed status ≠ Neo4j status, `update_service_status()`.
  This is what makes the graph (and dashboard) reflect *observed reality*.
- **Edge-triggered alert:** on a `HEALTHY → unhealthy` transition (after a no-alert
  baseline first pass), POST `/alert` on a daemon thread (non-blocking). One incident
  per break; re-arms on recovery. No alert storm during remediation.

`simulation/fault_injector.py` — removed `_send_alert()` and all its calls + now-unused
imports (`httpx`, `AlertPayload`, `AlertSeverity`). **The injector now only breaks things
and records intended ground-truth status; it never alerts.** Detection/alerting is wholly
the collector's job — this deletes the faked-detection path.

**Verified (INC-F10F84FE):** collector running → `inject redis_oom` fires NO alert →
collector detects `maxmemory capped at 1048576 bytes` within 5s → syncs Neo4j
(HEALTHY→OOM_KILLED) → fires alert itself → agent RESOLVED in 6.62s → redis maxmemory
back to 0 → collector re-syncs HEALTHY. **Zero manual `curl`.**

Run: `python -m simulation.telemetry_collector`

> Note: with real at-source detection, the redis_oom alert now originates from
> `redis-cart` itself (depth-0 self-diagnosis), not a crafted upstream `frontend`
> alert. The multi-hop root-cause advantage is still demonstrated by the *benchmark*
> scenarios (S-04 uses a deliberately ambiguous upstream alert); live operation
> detects at the source, which is the honest behaviour. `service_crash` is racy to
> detect at 5s polling because `restart: unless-stopped` can revive the container
> faster than a poll; `redis_oom` / `network_partition` / `docker pause` persist and
> are reliably detected.

### Step 2 — Real verification ✅ DONE & VERIFIED

`agent/nodes/evaluator.py` — added `verify_real_health(root_cause_node, script_path)`.
After a sandbox exit_code 0 the evaluator no longer optimistically flips Neo4j to
HEALTHY. It re-probes the REAL state via the Docker SDK:

- redis SOP (`script_path` contains "redis"): `redis-cli ping == PONG` AND maxmemory
  **not** OOM-capped. *(Healthy = maxmemory 0/unlimited OR > 10MB. This corrects the
  literal spec "> 10485760", which would wrongly fail a freshly-restarted redis at
  maxmemory=0.)*
- container SOP (contains "container"): target `State.Status == running` AND attached
  to `boutique-sim`.
- adservice SOP (contains "adservice"): container running (Step 4).
- unknown: trust exit code (no probe available).

Only a passing real check flips the graph to HEALTHY → **RESOLVED now means genuinely
recovered.** A failing check leaves the service unhealthy and the loop continues.

**Verified (INC-27707FE3):** basic redis_oom → restart → real probe `redis PONG,
maxmemory=0 (uncapped)` → RESOLVED.

### Step 3 — NEXT_IF_FAIL wired (Q3) ✅ DONE & VERIFIED — *first time ever executed*

When real verification fails after a clean (exit 0) execution, the evaluator now calls
`gc.get_next_skill(current_skill)` (Q3) and, if an unvisited fallback exists, loads it
directly into `current_*` and sets `fallback_pending=True`. `agent/graph.py`'s
`route_after_evaluate` sends a pending fallback straight to `reason` (bypassing Q2,
which filters by trigger and would never cross OOM_KILLED → a STALE_DATA flush SOP).
New state field `fallback_pending` in `agent/state.py`.

Graph data: the `NEXT_IF_FAIL` edge was rewired `Redis_Restart_SOP → Redis_Flush_SOP`
(was `→ Cart_Restart_SOP`) in `graph/cypher/service_topology.cypher` + live Neo4j, so
the fallback actually remediates an OOM (flush + raise maxmemory).

Scenario: new `inject_persistent_redis_oom()` / `redis_oom_persistent` fault recreates
redis-cart as `redis-server --maxmemory 1mb` so the cap **survives `docker restart`**
(stock `redis:alpine` has no config file, so a runtime CONFIG SET can't persist).
`cache_flush.sh` success criterion changed from "0 keys" (races against live cart
traffic) to "maxmemory restored above the OOM ceiling" — the real remediation.

Collector hardening: alerts are now **debounced** (same unhealthy status on 2
consecutive polls, one alert per episode) so transient container-recreate churn
("removing") doesn't raise spurious incidents. Detection latency ~10s; sync still ~5s.

**Verified (INC-D18B6704):** `redis_oom_persistent` → Redis_Restart_SOP (exit 0) →
real verify FAILS (maxmemory still 1048576) → NEXT_IF_FAIL → Redis_Flush_SOP (exit 0)
→ real verify PASSES (maxmemory 268435456) → **RESOLVED, skills_executed=
['Redis_Restart_SOP','Redis_Flush_SOP'], 2 hops.**

### Step 4 — Activate dead skills (STALE_DATA, HIGH_CPU) ✅ DONE & VERIFIED

Two of the four previously-unreachable skills now fire from real faults + real detection:

**STALE_DATA → Redis_Flush_SOP** (was unreachable — no injector emitted STALE_DATA):
- `inject_stale_data()` (`stale_data` fault) writes 1000 TTL-bearing keys via a single
  redis `EVAL` (fast) to simulate a cache full of stale entries.
- Collector `_check_redis_stale()` flags STALE_DATA when the volatile keyspace is
  anomalously large (`expires >= 200`).
- **Verified (INC-03834F12):** detect → Redis_Flush_SOP → RESOLVED.

**HIGH_CPU → AdService_CPU_Throttle_SOP** (was unreachable; also the first NON-RESTART
remediation):
- `inject_high_cpu()` (`high_cpu` fault) starts a detached `sh` busy-loop inside
  adservice (it ships /bin/sh), pinning a core to ~100%.
- Collector `_check_adservice_cpu()` flags HIGH_CPU when `docker stats` CPU% ≥ 80.
- New SOP `sops/adservice/throttle.sh`: `docker update --cpus=0.1` caps the container
  at the cgroup level — no restart, no dropped requests. `AdService_CPU_Throttle_SOP`
  script_path repointed from the wrong `container/restart.sh` → `adservice/throttle.sh`
  (live Neo4j + `service_topology.cypher`).
- `verify_real_health()` adservice branch confirms the cap deterministically
  (`HostConfig.NanoCpus` set to ≤ 0.2 CPU), not a noisy CPU sample.
- **Verified (INC-49F55333):** CPU 100% → detect HIGH_CPU → throttle → CPU 9.97%,
  NanoCpus=100000000 → RESOLVED, skills_executed=['AdService_CPU_Throttle_SOP'].

Reset note: `docker update --cpus=0` does NOT clear an existing cap; `reset_high_cpu`
restarts adservice (kills the burner) and sets `cpu_quota=-1` to truly restore
unlimited CPU so re-injection can spike again.

Still-dead skills (no injector / not a single-container remediation): `Checkout_Restart_SOP`
and `Frontend_Restart_SOP` (both trigger DEGRADED, which no fault emits).

### Step 5 — Real dashboard health ✅ DONE & VERIFIED

The dashboard reads health from Neo4j `Service.status` (`graph_viz.build_network` →
`get_all_service_statuses`). Because the telemetry collector (Step 1) now continuously
syncs *real* container state into Neo4j, the dashboard reflects reality with no extra
code — closing the gap where a `docker pause` left the dashboard showing green.

Changes: `dashboard/app.py` — added the new faults (`redis_oom_persistent`,
`stale_data`, `high_cpu`) to the inject dropdown; added a "🔄 Refresh real health"
button; rewrote `_run_live_scenario` to wait for the collector-driven resolution (poll
for the new audit report) instead of expecting the injector to alert. `container/restart.sh`
now `docker unpause`s a paused target before restart. `Generic_Restart_SOP` was mapped
APPLIES_TO `frontend` (CRASH_LOOPING) so a frontend pause auto-resolves.

**Verified (INC-55D78034) — the definitive "system is real" test:** with NO injector
involvement, ran `docker pause frontend`. Within ~7s the collector synced Neo4j
frontend → CRASH_LOOPING and the dashboard rendered frontend RED + loadgenerator AMBER
(blast radius). The collector then fired the alert; the agent ran Generic_Restart_SOP
(unpause + restart), frontend returned to running, Neo4j → HEALTHY, dashboard → all
green. A fault the injector never touched was detected, diagnosed, remediated, and
verified entirely autonomously.

---

## Closed-Loop Upgrade — Summary

The system is now a genuine closed loop, not a scripted demo:

| Concern | Before | After |
|---|---|---|
| Detection | Injector hand-wrote the alert with a SOP-matching trigger | Telemetry collector observes real container/redis state and raises the incident |
| Verification | RESOLVED = sandbox exit-code 0 (optimistic graph flip) | RESOLVED = real re-probe (redis-cli / docker inspect / cgroup cap) passes |
| Fallback | `NEXT_IF_FAIL` defined but never executed | Q3 wired; two-SOP chain runs when the first SOP fails real verification |
| Skill coverage | 5 of 9 reachable; all remediation = restart | +STALE_DATA (flush) +HIGH_CPU (cgroup throttle, non-restart) +frontend |
| Dashboard | Showed Neo4j graph state (could be stale/faked) | Reflects real container health (collector-synced); auto-recovers on `docker pause` |

Run order for the full loop: `docker compose up -d` · boutique up ·
`python -m agent.main` · `python -m simulation.telemetry_collector` ·
`streamlit run dashboard/app.py`.

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
                               │  (AlertPayload via HTTP POST :8888)
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE C: LANGGRAPH AGENTIC BRAIN                           ✅ BUILT   │
│                                                                        │
│  [ingest.py] ──► [retriever.py] ──► [reasoner.py] ──► [executor.py]  │
│       └──────────────────────────── [evaluator.py] ◄──────────────────┘
│  LLM: Gemini 2.5 Flash Lite (gemini-2.5-flash-lite)                   │
│  Verified: redis_oom INC-4AC84F16 6.3s RESOLVED ✅                    │
└──────────────▲──────────────────────────────────────┬──────────────────┘
               │                                      │
               │  (GraphRAG Context Lookup)           │  (Secure Tool Call)
               ▼                                      ▼
┌──────────────────────────────────┐   ┌──────────────────────────────────┐
│ MODULE B: NEO4J SKILL GRAPH      │   │ MODULE D: DOCKER SANDBOX         │
│                                  │   │                                  │
│  ✅ 12 Service nodes             │   │  ✅ sop-executor:latest image    │
│  ✅ 9 Skill (SOP) nodes         │   │  ✅ sandbox_tools.py             │
│  ✅ 16 DEPENDS_ON edges         │   │  ✅ sops/redis/restart.sh        │
│  ✅ 12 APPLIES_TO edges         │   │  ✅ sops/redis/cache_flush.sh    │
│  ✅ 4 NEXT_IF_FAIL chains       │   │  ✅ sops/container/restart.sh    │
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

**AgentState schema (agent/state.py — actual built version):**

```python
class AgentState(TypedDict):
    # ── Input — set once by ingest.py ─────────────────────────────────────
    alert_id:          str               # e.g. "INC-A3F2B1C0"
    alert_service:     str               # e.g. "frontend"
    alert_error_type:  str               # e.g. "OOM_KILLED"
    alert_message:     str               # raw message from fault injector
    alert_raw:         dict              # full original AlertPayload dict

    # ── Graph traversal — set by retriever.py ─────────────────────────────
    root_cause_node:   Optional[str]     # e.g. "redis-cart"
    dependency_chain:  list[str]         # ["redis-cart", ..., "frontend"]
    traversal_depth:   int

    # ── Current skill — updated each loop iteration ────────────────────────
    current_skill:       Optional[str]
    current_script:      Optional[str]
    current_script_type: Optional[str]   # "python" | "bash"
    current_description: Optional[str]
    current_risk_level:  Optional[str]   # "LOW" | "MEDIUM" | "HIGH" from Skill node

    # ── Timing & telemetry ────────────────────────────────────────────────
    t_alert:           float             # time.time() at ingestion (for MTTR)
    tokens_used:       int               # accumulated LLM tokens this incident

    # ── Execution tracking ─────────────────────────────────────────────────
    visited_skills:    list[str]
    execution_history: list[ExecutionResult]
    attempt_count:     int
    max_attempts:      int               # default: 5

    # ── LLM decision ──────────────────────────────────────────────────────
    llm_decision:      Optional[str]     # "execute" | "skip" | "escalate"
    llm_reason:        Optional[str]

    # ── Resolution state ───────────────────────────────────────────────────
    all_healthy:       bool
    services_still_unhealthy: int

    # ── Output ────────────────────────────────────────────────────────────
    rca_report:        Optional[RCAReport]
    error_message:     Optional[str]
```

**RCAReport schema (core/schemas.py — includes Phase 7 instrumentation fields):**

```python
class RCAReport(BaseModel):
    alert_id:          str
    alert_service:     str
    alert_error_type:  str
    root_cause_node:   str
    dependency_chain:  list[str]
    skills_executed:   list[str]
    execution_history: list[ExecutionResult]
    total_hops:        int
    resolution_status: ResolutionStatus   # RESOLVED | ESCALATED | PARTIAL | FAILED
    mttr_seconds:      float | None       # time.time() - t_alert (wired Phase 7)
    tokens_used:       int                # sum of LLM usage_metadata (wired Phase 7)
    all_services_healthy: bool
    timestamp:         datetime
    notes:             str
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

**SOP scripts (sops/ directory — all written and verified):**

```
sops/
├── redis/
│   ├── cache_flush.sh          ✅ FLUSHALL ASYNC + verify 0 keys
│   └── restart.sh              ✅ docker restart, waits for PONG, risk_level=MEDIUM
└── container/
    └── restart.sh              ✅ generic restart — idempotently reconnects to
                                    boutique-sim BEFORE restart (fixes network_partition),
                                    verifies network membership as success signal,
                                    risk_level=MEDIUM (Docker socket required)
```

**sop-executor:latest image:**
- Base: `python:3.11-slim`
- Extras: `redis-tools`, `docker-cli` (v25.0.3 pinned)
- Default user: `sopuser` (UID 1000, non-root)
- Size: ~280 MB
- MEDIUM-risk SOPs: sandbox_tools.py overrides to root + mounts Docker socket

**Per-SOP privilege scoping (sandbox_tools.py):**

| risk_level | Docker socket | User inside sandbox | Use case |
|---|---|---|---|
| LOW | Not mounted | `1000:1000` (sopuser) | Redis flush, metric reads |
| MEDIUM | `/var/run/docker.sock` RW | root | Container restarts, network reconnect |
| HIGH | Reserved | Reserved | — |

All tiers enforce: `--cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges --read-only --tmpfs /tmp:size=64m --memory=256m --cpus=0.5 --pids-limit=50 --network boutique-sim --rm`

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

### Phase 4 — LangGraph Agent Core ✅ Complete

**Objective:** Working ReAct loop: alert → graph retrieval → LLM reasoning.

- [x] `agent/state.py` — AgentState TypedDict with all fields including `current_risk_level`, `t_alert`, `tokens_used`
- [x] `agent/nodes/ingest.py` — AlertPayload parsing, state init, `t_alert = time.time()`
- [x] `agent/nodes/retriever.py` — Q1 (first iteration) + Q2 (every iteration), Progressive Context Injection
- [x] `agent/nodes/reasoner.py` — Gemini 2.5 Flash Lite, structured JSON output, retry on parse failure, token accumulation
- [x] `agent/nodes/executor.py` — resolves Neo4j script_path to host path, calls `sandbox_tools.execute_sop()`
- [x] `agent/nodes/evaluator.py` — Q5 health check, graph-sync after success, MTTR+tokens into RCAReport, audit write
- [x] `agent/graph.py` — LangGraph StateGraph, two conditional routers: `route_after_reason` + `route_after_evaluate`
- [x] `agent/main.py` — FastAPI on port 8888, `/alert` (POST), `/health` (GET), `/status` (GET)
- [x] Verified: mock alert → root cause identified, Q1/Q2 returning correct chains

**LLM configuration:**
- Provider: Gemini 2.5 Flash Lite (`gemini-2.5-flash-lite`) via `langchain-google-genai`
- `gemini-2.0-flash` has `limit:0` on this project — do NOT use it
- API key: `GOOGLE_API_KEY` in `.env`

**Two conditional edges in graph.py:**

```python
route_after_reason:   "execute" → executor | "skip"/"escalate" → evaluator
route_after_evaluate: all_healthy → report | attempts≥max → report | no_skill → report | else → retriever
```

---

### Phase 5 — Docker Sandbox + SOP Scripts ✅ Complete

**Objective:** Agent executes real scripts safely in isolated containers.

- [x] `sop-executor/Dockerfile` — `python:3.11-slim` + redis-tools + docker-cli 25.0.3, non-root `sopuser` (UID 1000)
- [x] `sops/redis/cache_flush.sh` — `FLUSHALL ASYNC`, verifies 0 keys remain
- [x] `sops/redis/restart.sh` — `docker restart redis-cart`, waits for `redis-cli ping → PONG`
- [x] `sops/container/restart.sh` — generic restart; idempotently reconnects to `boutique-sim` **before** restart (critical for `network_partition` correctness); verifies network membership post-restart
- [x] `agent/tools/sandbox_tools.py` — Docker SDK execution; per-SOP privilege scoping by `risk_level`
- [x] All 7 container-restart Skill nodes: `risk_level = MEDIUM` in Neo4j (corrected from LOW — LOW means no Docker socket, breaking `docker restart`)
- [x] `agent/nodes/executor.py`: uses `state.get("current_risk_level", "LOW")` (not name-based hack)
- [x] Neo4j: all `script_path` fields point to real `sops/` paths

**End-to-end verified runs:**

| Alert ID | Fault | Root Found | MTTR | Result |
|---|---|---|---|---|
| INC-4AC84F16 | redis_oom | redis-cart | 6.3s | ✅ RESOLVED |
| INC-111D3B59 | service_crash (productcatalog) | productcatalogservice | 5.22s | ✅ RESOLVED |
| INC-2EFDDAD1 | network_partition (paymentservice) | paymentservice | 3.63s | ✅ RESOLVED |

**Critical fix — hollow-resolution bug (network_partition):**
`docker restart` preserves existing network connections but does NOT restore connections removed by `docker network disconnect`. Without the explicit `docker network connect boutique-sim $TARGET` before restart in `container/restart.sh`, the container returns to "running" state but is unreachable — the evaluator's optimistic graph-sync would report RESOLVED while real connectivity was broken.

---

### Phase 6 — End-to-End Integration + Chaos Testing ✅ Complete

**Objective:** Full pipeline: fault injection → agent detects → graph query → SOP → verified resolution.

- [x] Agent runs on host (not inside Docker), accesses `boutique-sim` network directly via Docker socket
- [x] All 3 feasible fault scenarios verified end-to-end (see Phase 5 verified runs above)
- [x] `eval/scenarios.json` — 3 ground-truth scenarios with root cause, dependency chain, transitive blast radius (queried from live Neo4j), verified MTTR, expected SOP + risk level

**`high_latency` fault — infeasible on this stack:**
Online Boutique v0.10.5 uses distroless images that lack `tc`/`iproute2`. `inject_high_latency()` in `fault_injector.py` detects this and no-ops with a warning. Documented in `eval/scenarios.json` `unsupported_faults` array. Feasible on Kubernetes with Istio VirtualService fault injection.

**eval/scenarios.json structure:**
```json
{
  "scenarios": [
    {
      "id": "S-01", "name": "Redis OOM", "fault_type": "redis_oom",
      "inject": {...}, "reset": {...}, "alert": {...},
      "ground_truth": {
        "root_cause": "redis-cart",
        "blast_radius": ["cartservice", "checkoutservice", "frontend", "loadgenerator"],
        "expected_skill": "Redis_Restart_SOP",
        "expected_risk_level": "MEDIUM"
      },
      "verified": {"status": "RESOLVED", "alert_id": "INC-4AC84F16", "mttr_s": 6.3, "hops": 1}
    }
    // S-02: service_crash / S-03: network_partition
  ],
  "unsupported_faults": [{"fault_type": "high_latency", "reason": "distroless images..."}]
}
```

---

### Phase 6.5 — Dashboard ✅ Complete

**Objective:** Streamlit live dashboard for demo impact.

- [x] `dashboard/app.py` — Streamlit main, 3 tabs (Live RCA Console / Incident History / Evaluation Results)
- [x] Live agent run feed — reconstructed ReAct timeline from the audit report the agent writes
- [x] Neo4j graph visualization — `dashboard/components/graph_viz.py` (pyvis, health-coloured: red root / amber blast radius / green healthy)
- [x] Live red→green transition — background-thread inject + main-thread Neo4j health polling
- [x] Final RCA report rendering — `dashboard/components/rca_report.py`
- [x] Evaluation tab — RQ1/RQ2 benchmark charts from `eval/results/benchmark_all.json`

**Run:** `pip install -e ".[dashboard]"` then `streamlit run dashboard/app.py` (needs agent server on :8888). See `dashboard/README.md`.

**Verified through the UI:** clicking *Inject fault* → `INC-78CD6994` redis_oom RESOLVED (redis-cart, MTTR 7.49s, 451 tokens) — graph went red then green, timeline + report rendered.

**Bug fixed during this phase:** `simulation/fault_injector.py` `_send_alert()` used a 5s client timeout while the agent processes synchronously for 6–15s, so every injection logged a spurious `alert_send_failed: timed out` even on success. Raised to 180s and now logs the real resolution (`alert_sent ... resolution=RESOLVED root_cause=...`).

---

### Phase 7 — Evaluation ✅ Complete (RQ1 + RQ2)

**Objective:** Quantitative answers to RQ1/RQ2. RQ3/RQ4 (LLM sensitivity) pending.

- [x] `eval/baselines/zero_shot.py` — `ZeroShotBaseline`: single Gemini call, raw alert only, no graph. Returns `ZeroShotResult` with root_cause, blast_radius, tokens_used, latency_s
- [x] `eval/baselines/vector_rag.py` — `VectorRAGBaseline`: FAISS + `all-MiniLM-L6-v2`; embeds 12 SOP docs from Neo4j at init; top-3 retrieval at query time. Returns `VectorRAGResult` with retrieval_latency_s split from LLM latency_s
- [x] `eval/benchmark.py` — scores all 3 systems; `--dry-run` validates without API calls; `--scenario S-01` for single run
- [x] Token instrumentation wired: `t_alert` stamped in ingest.py; `usage_metadata` dict extracted in reasoner.py; `mttr_seconds + tokens_used` written to `RCAReport` in evaluator.py
- [x] `eval/results/benchmark_all.json` — canonical combined 3-scenario results
- [x] `eval/results/EVALUATION_SUMMARY.md` — paper-ready results section

**Benchmark results (3 scenarios, Gemini 2.5 Flash Lite):**

| System | Root Accuracy | Avg Blast-Radius F1 | Avg Latency | Avg Tokens |
|---|---|---|---|---|
| **Agentic GraphRAG (Ours)** | **100%** | **1.000** | **5.05s** † | 451 ‡ |
| Zero-Shot LLM (B1) | 100% | 0.739 | 3.83s | 449 |
| Vector RAG (B2) | 100% | 0.739 | 3.75s | 643 |

† GraphRAG latency = actual MTTR (traversal + reasoning + sandbox + verification). Baseline = inference only.  
‡ Agent tokens from post-instrumentation run INC-38BFE69C. Phase 5 runs pre-date instrumentation (tokens_used=0 in those audit JSONs).

**Key finding:** Both baselines achieve identical scores — SOP retrieval via semantic similarity adds no blast-radius information beyond what zero-shot knows. The 35% relative F1 improvement (1.000 vs 0.739) comes exclusively from DEPENDS_ON edge traversal, which only the graph-based system performs. Vector RAG uses 43% more tokens than zero-shot with no accuracy gain.

**Instrumentation verification:** `audit/rca_INC-38BFE69C.json` — `mttr_seconds: 33.89`, `tokens_used: 451`

**Gemini token measurement fix:** `usage_metadata` is returned as a plain `dict` (not an object) by `langchain-google-genai`. Use `um.get("total_tokens", 0)` not `getattr(um, "total_tokens", 0)`.

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
| **LLM in production (Phase 4+)** | Llama 3.1:8b | **Gemini 2.5 Flash Lite** | `gemini-2.0-flash` has `limit:0` on this API key; Flash Lite works and is fast. Llama via Ollama available for RQ4 comparison |
| Graph context injection | Full graph per LLM call | Progressive Context Injection (one node) | Eliminates context bloat; mathematically constrains tool hallucination |
| GraphClient lifecycle | Context manager (`with`) | Singleton with `atexit` | One driver + connection pool per process; context manager closes shared resource |
| Health checks | `grpc_health_probe` | `service_started` + HTTP check for frontend | v0.10.5 uses K8s-native gRPC probes; binary not bundled |
| **Sandbox privilege scoping** | Single uniform security profile | **risk_level-driven: LOW (no socket) / MEDIUM (socket + root)** | Container restarts require Docker socket access; non-root sopuser cannot use it. Low-risk SOPs (cache flush) don't need it |
| **Neo4j risk_level for restart SOPs** | LOW (initial value) | **MEDIUM (corrected Phase 5.6)** | LOW prevented socket mount → `docker restart` failed with "connection refused to daemon" |
| **network_partition remediation** | Plain `docker restart` | **Reconnect to boutique-sim BEFORE restart** | `docker restart` preserves existing connections but does NOT restore ones removed by `network disconnect`. Container would be "running" but unreachable — hollow RESOLVED |
| **executor.py risk derivation** | `"MEDIUM" if "restart" in skill.lower()` | `state.get("current_risk_level", "LOW")` | Name-based hack brittle and bypasses graph-as-authority. risk_level is in the Neo4j Skill node; retriever.py reads and propagates it |
| **Token measurement** | N/A | `usage_metadata` dict-access (not getattr) | `langchain-google-genai` returns `usage_metadata` as a plain `dict`, not a Python object. `getattr(um, "total_tokens", 0)` returns 0 silently |

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
├── .env                              ✅  (gitignored — GOOGLE_API_KEY, NEO4J_PASSWORD)
├── .env.example                      ✅
├── .gitignore                        ✅
├── pyproject.toml                    ✅  (all deps pinned)
├── docker-compose.yml                ✅  (Neo4j + DinD)
├── core/
│   ├── config.py                     ✅  (Settings singleton — llm_provider, neo4j_*, docker_host)
│   ├── schemas.py                    ✅  (AlertPayload, SkillNode, ExecutionResult, RCAReport)
│   ├── exceptions.py                 ✅
│   ├── logging_config.py             ✅  (structlog, JSON prod / coloured dev)
│   └── __init__.py                   ✅
├── graph/
│   ├── graph_client.py               ✅  (Q1–Q6, singleton, never use context manager)
│   ├── graph_populator.py            ✅
│   ├── schema_definitions.py         ✅
│   ├── cypher/
│   │   ├── service_topology.cypher   ✅  (Online Boutique DEPENDS_ON + Skill nodes)
│   │   └── remediation_queries.cypher ✅
│   └── scripts/
│       └── init_graph.py             ✅  (populate + validate)
├── agent/
│   ├── graph.py                      ✅  (LangGraph StateGraph, two conditional edges)
│   ├── state.py                      ✅  (AgentState TypedDict, t_alert + tokens_used)
│   ├── main.py                       ✅  (FastAPI :8888 — /alert, /health, /status)
│   └── nodes/
│       ├── ingest.py                 ✅  (stamps t_alert, inits tokens_used=0)
│       ├── retriever.py              ✅  (Q1 first iter, Q2 every iter, populates current_risk_level)
│       ├── reasoner.py               ✅  (Gemini call, usage_metadata dict, accumulates tokens)
│       ├── executor.py               ✅  (resolves neo4j path → host path, calls sandbox_tools)
│       └── evaluator.py              ✅  (Q5, graph-sync, mttr_seconds + tokens_used → RCAReport)
├── agent/tools/
│   └── sandbox_tools.py              ✅  (Docker SDK, per-SOP privilege scoping by risk_level)
├── sop-executor/
│   └── Dockerfile                    ✅  (python:3.11-slim + redis-tools + docker-cli 25.0.3)
├── sops/
│   ├── redis/
│   │   ├── restart.sh                ✅  (docker restart + redis-cli ping verify, MEDIUM)
│   │   └── cache_flush.sh            ✅  (FLUSHALL ASYNC + 0-keys verify, MEDIUM)
│   └── container/
│       └── restart.sh                ✅  (reconnect boutique-sim + restart + network verify, MEDIUM)
├── simulation/
│   ├── docker-compose.yml            ✅  (Online Boutique v0.10.5, boutique-sim network)
│   └── fault_injector.py             ✅  (redis_oom, service_crash, network_partition, high_latency*)
│                                         * high_latency no-ops on distroless images
├── eval/
│   ├── scenarios.json                ✅  (3 verified scenarios + unsupported_faults)
│   ├── benchmark.py                  ✅  (--dry-run, --scenario, reads audit/ for agent tokens)
│   ├── baselines/
│   │   ├── zero_shot.py              ✅  (ZeroShotBaseline — single LLM call, no graph)
│   │   └── vector_rag.py             ✅  (VectorRAGBaseline — FAISS/all-MiniLM-L6-v2, 12 SOPs)
│   └── results/
│       ├── benchmark_all.json        ✅  (canonical combined 3-scenario results)
│       ├── benchmark.json            ✅  (most recent run)
│       ├── benchmark.txt             ✅  (ASCII comparison table)
│       └── EVALUATION_SUMMARY.md     ✅  (paper-ready results section)
├── audit/                            ✅  (gitignored — RCA audit JSONs per incident)
└── docs/
    └── ENGINEERING_REFERENCE-first update.md  ✅  (this file)
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

---

## Critical Operating Rules (Do Not Lose These)

These rules caused bugs when violated — keep them here as the authoritative reference:

| Rule | Why |
|---|---|
| Never use `with GraphClient() as gc:` | Context manager closes the shared singleton driver. Use `gc = GraphClient()` directly |
| Neo4j password is `supersecretpassword` | Set at volume creation — cannot change without `docker compose down -v` (wipes all graph data) |
| Never commit `.env` | Contains `GOOGLE_API_KEY` and `NEO4J_PASSWORD` |
| `CLAUDE-INSTRUCTIONS.md` and `claude.md` are gitignored | Session-to-session context docs — local only |
| `gemini-2.0-flash` is rate-limited to 0 on this project | Use `gemini-2.5-flash-lite` only |
| After any `.env` change, fully restart the agent server | `settings` is cached via `@lru_cache` — hot-reload does not pick up new values |
| `sop-executor:latest` must be rebuilt if Dockerfile changes | `docker build -t sop-executor:latest sop-executor/` |
| `boutique-sim` network must be up before sandbox runs | If simulation stack is down, all sandbox executions fail with network errors |
| `usage_metadata` from Gemini is a plain `dict` | Use `um.get("total_tokens", 0)` not `getattr(um, "total_tokens", 0)` |
| All 7 container-restart Skill nodes need `risk_level=MEDIUM` | LOW = no Docker socket = `docker restart` fails |

---

*Department of Computer Engineering & Technology, MIT World Peace University*
*Developer: Raghav Nimbalkar (PRN: 1262251354) | Guide: Dr. Bhavana Tiple*
*Last updated: 2026-06-23 — Phase 7 complete (RQ1+RQ2 evaluated). Pending: Phase 6.5 dashboard, Phase 7 RQ3/RQ4 LLM sensitivity, Phase 8 report.*
