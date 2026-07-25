# Agentic GraphRAG for Autonomous Root Cause Analysis in Cloud-Native Microservices

[![tests](https://github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-/actions/workflows/tests.yml/badge.svg)](https://github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-/actions/workflows/tests.yml)
![AIOps](https://img.shields.io/badge/Domain-AIOps-blueviolet?style=flat-square)
![LangGraph](https://img.shields.io/badge/Framework-LangGraph-orange?style=flat-square)
![Neo4j](https://img.shields.io/badge/Database-Neo4j-008CC1?style=flat-square)
![Docker](https://img.shields.io/badge/Security-Docker_Sandbox-2496ED?style=flat-square)
![Status](https://img.shields.io/badge/Status-Closed--Loop_Operational-success?style=flat-square)

**MTech Final Year Project · Raghav Nimbalkar · MIT-WPU Pune · Supervisor: Dr. Bhavana Tiple**

A self-contained AIOps research system that **detects, diagnoses, remediates, and verifies**
failures in a running microservice stack — autonomously, with no human in the loop and no
hard-coded fault-to-fix mapping.

---

## The Closed Loop

```
                  observes real state            raises incident
  ┌─────────────────────┐   every 5s    ┌──────────────────────┐
  │ Online Boutique      │ ───────────▶ │ telemetry_collector  │
  │ (12 live containers) │              │ docker/redis probes  │
  └─────────────────────┘ ◀─────────── └──────────┬───────────┘
            ▲   real remediation                   │ POST /alert
            │                                       ▼
  ┌─────────┴───────────────────────────────────────────────────┐
  │  LangGraph agent:  ingest → retrieve → reason → execute       │
  │                          ▲                          │         │
  │                      evaluate ◀──────────────────────┘         │
  │   - Neo4j graph traversal finds the true root cause            │
  │   - Progressive Context Injection: only the root's candidates  │
  │   - Docker sandbox runs the SOP (privilege-scoped, --rm)       │
  │   - Real post-execution verification (re-probe, not exit code) │
  │   - NEXT_IF_FAIL fallback chain when a SOP does not fix it     │
  └──────────────────────────────────────────────────────────────┘
```

Every stage operates on **observed reality**, not a script:

| Stage | How it works |
|---|---|
| **Detection** | `simulation/telemetry_collector.py` polls real container state (Docker SDK) and redis health (`redis-cli`) every 5s, writes ground-truth status into Neo4j, and raises the alert on a genuine `HEALTHY → unhealthy` transition (debounced). The fault injector only breaks things — it does **not** signal the agent. |
| **Localisation** | Neo4j multi-hop `DEPENDS_ON` traversal returns the deepest unhealthy node — the real root cause, not the alerting service. |
| **Retrieval** | A separate Skill Graph surfaces every SOP that applies to the root cause and its condition. Only that candidate set enters the prompt (Progressive Context Injection), and it is also the allowlist the model must choose from. |
| **Reasoning** | The LLM picks one SOP **by exact name from the candidate set**, or escalates. A choice outside the set, or an unparseable response, **fails safe to escalate** — it never executes blind. |
| **Execution** | The SOP runs in an isolated, capability-stripped, `--rm` Docker sandbox with per-SOP privilege scoping. |
| **Verification** | After execution the evaluator **re-probes the real service** (redis maxmemory, container state, cgroup cap). `RESOLVED` means the service genuinely recovered — not that the script exited 0. |
| **Fallback** | If real verification fails, the agent follows the `NEXT_IF_FAIL` edge to the next SOP and retries — a true multi-step remediation. |

---

## Live Demo Dashboard

A Streamlit dashboard (`dashboard/app.py`, seven tabs) is the primary showcase surface. A header
status strip shows the whole loop is up (agent · collector · Neo4j · LLM) at a glance.

- **Start Here** — a plain-language intro for non-experts: the *"the alarm rings at the front door,
  the fire is in the basement"* analogy, an animated dependency-trace (a normal AI blames the frontend;
  GraphRAG traces the edges to the real root), the four-step loop in everyday words, and the headline
  proof numbers. It opens by default so the "what and why" lands before any technical tab.
- **Live RCA Console** — inject a fault and watch the dependency graph go red → green in real time
  as the agent resolves it. Because the collector continuously syncs real container state into Neo4j,
  the graph reflects reality — e.g. `docker pause frontend` turns the node red within seconds and the
  agent autonomously restarts it back to green.
- **Dual Graph & Architecture** — both halves of the dual graph side by side: the *infrastructure*
  graph (services + `DEPENDS_ON`, the WHERE) and the *skill* graph (SOP nodes coloured by risk,
  `APPLIES_TO` edges, dashed `NEXT_IF_FAIL` fallback chains, the HOW), plus the 5-layer loop narration.
  Every edge is data in Neo4j, not code.
- **Live Duel vs Baselines** — the depth-stratified result made *live*: one ambiguous alert is run
  through GraphRAG, zero-shot, and vector-RAG at once. At depth 1 all three find the root; at depth 4
  both baselines guess `frontend` (the surface) while graph traversal follows the dependency edges to
  `redis-cart` four hops away. The baselines even *explain* their wrong answer — the topology blindness
  made visible.
- **Incident History** — every audit report, filterable, each with an **Agent Decision** panel
  showing the graph-vetted SOPs the LLM *considered*, which it *chose*, and *why* — the auditable
  proof that remediation is a decision over a candidate set, not a hardcoded fault→fix lookup.
- **Evaluation Results** — leads with the depth-stratified headline (root accuracy flat at 100%
  vs baselines collapsing to 0%), plus the 10-fault coverage table.
- **Autonomy Run** — the unattended chaos run: 16/16 detected and resolved, 0 manually fired alerts.

---

## Evaluation Results

Benchmarked on Google Online Boutique v0.10.5 · LLM: Gemini 2.5 Flash Lite ·
**21 scenarios × 3 reps · 10 fault types · cascade depths 1–4**
(`eval/benchmark_full.py` → `eval/results/benchmark_full.json`)

> **Provider portability:** all recorded benchmarks ran on Gemini 2.5 Flash Lite. The live
> reasoner has since been switched to **Claude Haiku 4.5** (`LLM_PROVIDER=anthropic`) after the
> Gemini project lost API access — a two-line `.env` change, no code churn, validated end-to-end
> (3/3 faults resolved, comparable MTTR ~6–10s and ~820–860 tokens/incident). The agent and both
> baselines are provider-agnostic via LangChain (gemini · openai · anthropic · ollama).

### The central result — root-cause accuracy by cascade depth

The deeper the true root is from the alerting service (and the more generic the symptom),
the harder localisation gets. The topology-blind baselines collapse monotonically;
graph traversal stays flat:

| Q1 depth | **Agentic GraphRAG (Ours)** | Zero-Shot LLM (B1) | Vector RAG (B2) |
|---|---|---|---|
| 1 (n=8) | **100%** | 100% | 100% |
| 2 (n=5) | **100%** | 80% | 40% |
| 3 (n=6) | **100%** | 17% | 17% |
| 4 (n=2) | **100%** | **0%** | **0%** |

Overall: GraphRAG **100%** root accuracy / **1.00** blast-radius F1 / **6.8 ± 2.8s** real MTTR
(inject → detect → remediate → re-verify), vs B1 62% / 0.69 and B2 52% / 0.73 (inference only —
the baselines never actually fix anything).

### Generalisation to a deeper topology — TrainTicket (depth 1→7)

To show the depth result is not a Boutique artifact, the **same** `get_root_cause` traversal was
run on the [FudanSELab TrainTicket](https://github.com/FudanSELab/train-ticket) dependency graph
(**36 services, 73 edges**, transcribed from its architecture diagram, loaded as an isolated
`:TTService` graph — the live demo is untouched). This is a **localisation study** (traversal vs a
topology-blind zero-shot LLM; remediation on TrainTicket is future work):

| | depth 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| **GraphRAG traversal** | yes | yes | yes | yes | yes | yes | **yes** |
| Zero-shot LLM | yes | no | yes | no | no | no | **no** |

**GraphRAG 7/7, zero-shot 2/7.** The depth-7 case: an alert at `frontend` ("site-wide 5xx spike")
whose root is `station` seven hops away — GraphRAG traverses
`frontend → gateway → preserve → seat → travel2 → basic → route → station` in ~0.03s; the LLM guesses
`gateway`. Reproduce: `python -m eval.trainticket.benchmark_localisation`
(`eval/results/trainticket_localisation.json`). The depth axis nearly doubles vs Online Boutique and
traversal still holds — because localisation is a graph property, not a model capability.

### Unattended autonomy run (chaos daemon)

11.5 minutes, faults injected at random with **no alert ever fired manually**
(`eval/results/chaos_run_20260626_140629.log`):

| Metric | Value |
|---|---|
| Faults injected | 16 (all 8 chaos fault types) |
| **Detected autonomously by the collector** | **16 / 16 (100%)** |
| Resolved / escalated | **16 / 0** |
| Mean detection latency · mean MTTR | 10.9s · 20.7s |

**Honest caveats** (stated, not hidden): one fault at a time — with a single unhealthy node,
deterministic traversal *will* find it, so the claim is robustness to alert ambiguity, not solved
multi-fault RCA; blast-radius F1=1.0 follows from the graph encoding the topology; per-call tokens
(~867) are *bounded by design*, not lower than the lean baselines in absolute terms.

> The RQ benchmark deliberately uses ambiguous *upstream* alerts to stress root-cause
> localisation. In live operation the telemetry collector detects faults *at the source* —
> the two are separate, complementary evaluation modes.

### Closed-loop scenarios (autonomous detect → remediate → verify)

| ID | Fault | Detection | Remediation | Result |
|---|---|---|---|---|
| CL-01 | Persistent redis OOM (cap survives restart) | collector: maxmemory capped | Redis_Restart_SOP fails real verify → **NEXT_IF_FAIL** → Redis_Flush_SOP | yes 2-SOP chain |
| CL-02 | Stale cache data | collector: large volatile keyspace | Redis_Flush_SOP | yes |
| CL-03 | High CPU on adservice | collector: `docker stats` ≥ 80% | AdService_CPU_Throttle_SOP (**non-restart** cgroup throttle) | yes 100% → ~10% |
| CL-04 | External `docker pause frontend` | collector: container not running | Generic_Restart_SOP (unpause + restart) | yes injector never involved |

---

## Core Design Decisions

**Progressive Context Injection** — The Skill Graph filters context: the LLM receives exactly one
skill node per call, not a dump of all SOPs. Per-call token cost stays ~450 tokens regardless of
graph size, and a class of tool hallucination is eliminated by construction — the agent can only
act on what the graph explicitly surfaces.

**Real verification over optimistic resolution** — `RESOLVED` requires a live re-probe of the
remediated service (redis-cli / docker inspect / cgroup state). A SOP that exits 0 without actually
recovering the service does not close the incident; the agent escalates or falls back instead.

**Per-SOP privilege scoping** — Sandbox privilege is driven by the `risk_level` property on the
Neo4j Skill node. LOW-risk SOPs run as non-root with no Docker socket; MEDIUM-risk SOPs get the
socket and root. All tiers enforce `--cap-drop ALL --read-only --memory=256m --pids-limit=50 --rm`.

**Graph-native retrieval over vector retrieval** — Dependency chains are structural, not semantic.
The evaluation confirms vector RAG cannot recover topology that the graph makes explicit.

---

## Running It

### First-time setup

```bash
python -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -e ".[dev,dashboard,eval]"

cp .env.example .env                                  # then edit: set NEO4J_PASSWORD
                                                      # and the API key for your LLM_PROVIDER
docker build -t sop-executor:latest sop-executor/     # the remediation sandbox image
```

### Start the system (four processes)

```bash
# 1. Infrastructure
docker compose up -d                                  # Neo4j (dual-graph store)
docker compose -f simulation/docker-compose.yml up -d # Online Boutique (12 services)

# 2. Load the dual graph (first run only, or after a Neo4j wipe)
python -m graph.scripts.init_graph                    # services, skills, edges, indexes

# 3. Agent + sensing + UI - each in its own terminal
python -m agent.main                                  # FastAPI agent on :8888
python -m simulation.telemetry_collector              # health observation loop (5s)
streamlit run dashboard/app.py                        # dashboard on :8501
```

Open <http://localhost:8501>. The four chips in the header must all be green before you
demo anything - they show the agent, collector, Neo4j and LLM are all live. Neo4j's own
browser is at <http://localhost:7474> if you want to query the graph directly.

### Use it

```bash
# Break something. The injector never raises an alert - the collector detects it.
python -m simulation.fault_injector list                          # all 10 fault types
python -m simulation.fault_injector inject redis_oom_persistent   # two-SOP fallback chain
python -m simulation.fault_injector inject high_cpu               # non-restart CPU throttle
docker pause frontend                                             # external fault, still caught

python -m simulation.fault_injector reset high_cpu                # undo a fault

# Multiple simultaneous faults, each remediated independently
python -m agent.multi_root --dry-run                              # list independent roots
python -m agent.multi_root                                        # resolve them all
```

Or drive the whole thing from the dashboard's **Live RCA Console** tab, which injects,
shows the graph going red, and renders the RCA report when the agent resolves it.

### Reproduce the results

```bash
python -m eval.benchmark_full                    # 21 scenarios x 3 reps, depth-stratified
python -m eval.ablation                          # component ablation
python -m eval.trainticket.benchmark_localisation# TrainTicket depth 1-7 generalisation
python -m simulation.chaos_daemon --duration 600 --min-incidents 15   # unattended autonomy
python -m pytest tests/ -q                       # 50 unit tests, no Docker/Neo4j/LLM needed
```

Results are written to `eval/results/`. The test suite pins the system's safety contract:
the **graph-as-allowlist invariant** (a hallucinated, injected or null SOP choice must
escalate, never execute), fail-safe on unparseable LLM output, loop-termination routing,
transient-LLM-error retry, multi-root orchestration, and the blast-radius helpers.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Dashboard shows "Neo4j unreachable" | Neo4j is not up: `docker compose up -d`. The Start Here, Evaluation and Autonomy tabs still work without it. |
| Live scenarios hang and time out | The telemetry collector is not running - nothing detects the fault. Start it: `python -m simulation.telemetry_collector`. |
| Agent escalates every incident | The LLM provider is rejecting calls (bad or expired key). Check `LLM_PROVIDER` and the matching key in `.env`; the agent fails safe to escalate rather than acting blind. |
| `docker compose up -d` says redis-cart name conflict | Expected after `redis_oom_persistent`, which recreates redis outside Compose to bake in the cap. Run `docker rm -f redis-cart` first, or ignore it - the container is healthy either way. |
| A service is unhealthy and never recovers | Reset the fault explicitly: `python -m simulation.fault_injector reset <fault>`. `high_cpu` in particular leaves a CPU cap that only its reset clears. |

---

## Tech Stack

| Layer | Choice |
|---|---|
| Agent orchestration | LangGraph 1.2.x + LangChain 1.3.x |
| LLM (live reasoner) | Claude Haiku 4.5 via `langchain-anthropic` (benchmarks recorded on Gemini 2.5 Flash Lite; provider-agnostic: gemini/openai/anthropic/ollama) |
| Graph database | Neo4j 5.18 Community (driver 6.x) + Cypher |
| Sensing | Docker SDK + `redis-cli` / `docker stats` probes |
| Execution sandbox | Docker Engine API, custom `sop-executor` image |
| Alert ingestion | FastAPI :8888 |
| Dashboard | Streamlit + pyvis |
| Simulation | Google Online Boutique v0.10.5 (Docker Compose) |
| Evaluation | FAISS + `all-MiniLM-L6-v2` (Vector RAG), single Gemini call (Zero-Shot) |
| Schema validation | Pydantic v2 |

---

## Project Status

| Phase | Status |
|---|---|
| 0–5 — Foundation, Neo4j dual-graph, simulation, agent, sandbox | yes Complete |
| 6 — Chaos integration + ground-truth scenarios | yes Complete |
| 6.5 — Streamlit dashboard (6 tabs, incl. Dual Graph, Live Duel, Autonomy Run) | yes Complete |
| 7 — Evaluation: baselines + expanded benchmark (21 scenarios × 3 reps, depth 1–4) | yes Complete |
| 9 — Closed-loop upgrade: real detection, verification, fallback chains | yes Complete |
| Scope expansion — 10 fault types, unattended chaos autonomy (16/16), decision-trail audit | yes Complete |
| Generalisation — TrainTicket topology, localisation to depth 7 (36 services) | yes Complete |
| Hardening — 46 unit tests, provider-agnostic (Gemini→Claude Haiku 4.5), thread-safe client | yes Complete |
| 8 — Thesis report + presentation | in progress |

---

## Honest Limitations

This is a research prototype on a controlled testbed, not production AIOps. We state the boundaries
plainly — each maps to a concrete step on the roadmap below.

- **Single-host Docker Compose**, not Kubernetes. No multi-node scheduling, service mesh, or real
  network fabric. Running a stack larger than Online Boutique (e.g. TrainTicket's ~41 Spring Boot
  services + MySQL + RabbitMQ + NACOS, which wants 16 GB+ for itself) needs dedicated/rented
  hardware and is out of scope for this thesis cycle — hence TrainTicket is used for
  **localisation only** (topology in Neo4j), not the live closed loop.
- **Single-fault injection.** One fault per scenario, so exactly one node is unhealthy and the
  deepest-unhealthy traversal is deterministic. The claim is *robustness to alert ambiguity*, not
  general multi-fault RCA; simultaneous correlated faults are untested and would need multi-root
  causal scoring.
- **Detection is 5 s polling** via the Docker SDK, not an event-driven observability pipeline
  (no Prometheus/OpenTelemetry). `service_crash` detection is racy because `restart: unless-stopped`
  can revive a container faster than the poll.
- **The dependency graph is hand-authored** (Boutique drawn by hand, TrainTicket transcribed from
  its diagram). A general "point it at any cluster" product would need to *auto-discover* topology
  from traces — the real gate to a multi-tenant tool (see roadmap v3).
- **Remediation is Boutique-only.** TrainTicket has topology but no Skill nodes, so the agent
  localises on it but cannot remediate it. Blast-radius F1 = 1.0 is near-tautological (the graph
  computes the closure that is ground truth), and per-incident tokens (~867) are *bounded by design*,
  not smaller than the lean baselines in absolute terms.
- **Per-procedure sandbox has a documented privilege tier:** MEDIUM-risk SOPs get the Docker socket
  and run as root inside the capability-dropped container — real power, scoped to procedures that
  cannot function without it, and flagged as the security item to replace with a brokered executor.

---

## Roadmap — from thesis testbed to product

Each rung is a bounded, fundable step; the difficulty is labelled honestly (engineering vs research).

| Stage | What | Difficulty |
|---|---|---|
| **Now (this repo)** | Full closed loop on Online Boutique (detect → localise → decide → sandbox-fix → verify → fallback); localisation *generalises* to TrainTicket at depth 1–7 | Done |
| **v2 — full loop on TrainTicket** | Deploy TrainTicket on real hardware; port the collector to Spring Boot `/actuator/health` + MySQL/RabbitMQ probes; author Spring/MySQL/RabbitMQ SOPs as `Skill` nodes | **Engineering, not research** — bounded labor + a rented box |
| **v3 — auto-topology discovery** | Build the `DEPENDS_ON` graph automatically from distributed traces (OpenTelemetry/Jaeger) instead of hand-authoring it | **Research-grade** — the real gate to "any cluster" |
| **v4 — product** | Multi-tenancy + auth, brokered (socket-free) execution, multi-root causal scoring, user-authored SOPs, event-driven detection | Productization |

The **research-hard part — graph-guided reasoning — already ports for free** (TrainTicket proved the
brain is topology-agnostic: a 3× larger architecture with zero code change). What remains between
here and a general tool is deployment labor (v2) and one genuine research problem, auto-topology
discovery (v3) — not the remediation logic, which is done.

---

## Repository Structure

```
agent/          LangGraph agent (ingest → retriever → reasoner → executor → evaluator)
core/           Shared schemas, config, exceptions, logging
graph/          Neo4j client + Cypher dual-graph + populator
sops/           SOP scripts mounted read-only into the sandbox (redis/, container/, adservice/)
sop-executor/   Dockerfile for the sandbox base image
simulation/     Online Boutique compose, fault_injector.py, telemetry_collector.py
dashboard/      Streamlit live demo (6 tabs: live console, dual-graph viewer,
                live duel vs baselines, incident history + agent-decision panel,
                eval charts, autonomy run)
eval/           Baselines, benchmark.py + benchmark_full.py (21×3), scenarios.json,
                trainticket/ (isolated topology + depth-1→7 localisation benchmark)
tests/          46 unit tests — allowlist invariant, routing, retry, TrainTicket topology
docs/           Engineering reference + architecture decisions
```
