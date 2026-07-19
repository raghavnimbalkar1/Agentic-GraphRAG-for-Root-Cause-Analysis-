# Agentic GraphRAG for Autonomous Root Cause Analysis in Cloud-Native Microservices

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
  │   • Neo4j graph traversal finds the true root cause           │
  │   • Progressive Context Injection: one Skill node per LLM call│
  │   • Docker sandbox runs the SOP (privilege-scoped, --rm)      │
  │   • REAL post-execution verification (re-probe, not exit-code)│
  │   • NEXT_IF_FAIL fallback chain when a SOP doesn't fix it     │
  └──────────────────────────────────────────────────────────────┘
```

Every stage operates on **observed reality**, not a script:

| Stage | How it works |
|---|---|
| **Detection** | `simulation/telemetry_collector.py` polls real container state (Docker SDK) and redis health (`redis-cli`) every 5s, writes ground-truth status into Neo4j, and raises the alert on a genuine `HEALTHY → unhealthy` transition (debounced). The fault injector only breaks things — it does **not** signal the agent. |
| **Localisation** | Neo4j multi-hop `DEPENDS_ON` traversal returns the deepest unhealthy node — the real root cause, not the alerting service. |
| **Retrieval** | A separate Skill Graph surfaces exactly **one** remediation SOP for the root cause + error type (Progressive Context Injection). |
| **Reasoning** | The LLM sees only that one SOP and decides execute / skip / escalate. On an unparseable response it **fails safe to escalate**, never executes blind. |
| **Execution** | The SOP runs in an isolated, capability-stripped, `--rm` Docker sandbox with per-SOP privilege scoping. |
| **Verification** | After execution the evaluator **re-probes the real service** (redis maxmemory, container state, cgroup cap). `RESOLVED` means the service genuinely recovered — not that the script exited 0. |
| **Fallback** | If real verification fails, the agent follows the `NEXT_IF_FAIL` edge to the next SOP and retries — a true multi-step remediation. |

---

## Live Demo Dashboard

A Streamlit dashboard (`dashboard/app.py`, five tabs) is the primary showcase surface. A header
status strip shows the whole loop is up (agent · collector · Neo4j · LLM) at a glance.

- **🚨 Live RCA Console** — inject a fault and watch the dependency graph go red → green in real time
  as the agent resolves it. Because the collector continuously syncs real container state into Neo4j,
  the graph reflects reality — e.g. `docker pause frontend` turns the node red within seconds and the
  agent autonomously restarts it back to green.
- **🗺️ Dual Graph & Architecture** — both halves of the dual graph side by side: the *infrastructure*
  graph (services + `DEPENDS_ON`, the WHERE) and the *skill* graph (SOP nodes coloured by risk,
  `APPLIES_TO` edges, dashed `NEXT_IF_FAIL` fallback chains, the HOW), plus the 5-layer loop narration.
  Every edge is data in Neo4j, not code.
- **⚔️ Live Duel vs Baselines** — the depth-stratified result made *live*: one ambiguous alert is run
  through GraphRAG, zero-shot, and vector-RAG at once. At depth 1 all three find the root; at depth 4
  both baselines guess `frontend` (the surface) while graph traversal follows the dependency edges to
  `redis-cart` four hops away. The baselines even *explain* their wrong answer — the topology blindness
  made visible.
- **📜 Incident History** — every audit report, filterable, each with an **Agent Decision** panel
  showing the graph-vetted SOPs the LLM *considered*, which it *chose*, and *why* — the auditable
  proof that remediation is a decision over a candidate set, not a hardcoded fault→fix lookup.
- **📊 Evaluation Results** — leads with the depth-stratified headline (root accuracy flat at 100%
  vs baselines collapsing to 0%), plus the 10-fault coverage table.
- **🤖 Autonomy Run** — the unattended chaos run: 16/16 detected and resolved, 0 manually fired alerts.

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
| CL-01 | Persistent redis OOM (cap survives restart) | collector: maxmemory capped | Redis_Restart_SOP fails real verify → **NEXT_IF_FAIL** → Redis_Flush_SOP | ✅ 2-SOP chain |
| CL-02 | Stale cache data | collector: large volatile keyspace | Redis_Flush_SOP | ✅ |
| CL-03 | High CPU on adservice | collector: `docker stats` ≥ 80% | AdService_CPU_Throttle_SOP (**non-restart** cgroup throttle) | ✅ 100% → ~10% |
| CL-04 | External `docker pause frontend` | collector: container not running | Generic_Restart_SOP (unpause + restart) | ✅ injector never involved |

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

```bash
# 1. Infrastructure
docker compose up -d                                  # Neo4j (dual-graph store)
docker compose -f simulation/docker-compose.yml up -d # Online Boutique (12 services)

# 2. Agent + sensing + UI
python -m agent.main                       # FastAPI agent on :8888
python -m simulation.telemetry_collector   # real health observation loop
pip install -e ".[dashboard]" && streamlit run dashboard/app.py   # dashboard on :8501

# 3. Break something — the collector detects it and the agent resolves it, no manual alert
python -m simulation.fault_injector inject redis_oom_persistent   # two-SOP fallback chain
python -m simulation.fault_injector inject high_cpu               # non-restart throttle
docker pause frontend                                             # external fault; auto-resolved

# 4. Unit tests (no Docker/Neo4j/LLM needed — pure logic + fakes)
python -m pytest tests/ -q
```

The test suite pins the system's safety contract: the **graph-as-allowlist invariant**
(a hallucinated / injected / null SOP choice must escalate, never execute), fail-safe on
unparseable LLM output, loop-termination routing, transient-LLM-error retry, and the
blast-radius / path-resolution helpers.

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
| 0–5 — Foundation, Neo4j dual-graph, simulation, agent, sandbox | ✅ Complete |
| 6 — Chaos integration + ground-truth scenarios | ✅ Complete |
| 6.5 — Streamlit dashboard | ✅ Complete |
| 7 — Evaluation: baselines + RQ1/RQ2 benchmark | ✅ Complete |
| 9 — Closed-loop upgrade: real detection, verification, fallback chains | ✅ Complete |
| 8 — Thesis report + presentation | ⬜ In progress |

---

## Honest Limitations

This is a research prototype on a controlled testbed, not production AIOps. Known boundaries:

- **Single-host Docker Compose**, not Kubernetes. No multi-node scheduling, service mesh, or real
  network fabric. `high_latency` injection is infeasible (Online Boutique images are distroless,
  no `tc`).
- **Detection is threshold/heuristic-based**, not a full observability pipeline (no Prometheus
  metrics or distributed tracing). `service_crash` detection is racy because `restart: unless-stopped`
  can revive a container faster than the 5s poll.
- **Small evaluation sample** (n = 4 RQ scenarios); results are directional, not statistically
  powered. Only one scenario (S-04) is genuinely multi-hop.
- **Remediation vocabulary is narrow** — restart, cache flush, and CPU throttle. Two of nine skills
  (`Checkout_Restart_SOP`, `Frontend_Restart_SOP`, both triggered by DEGRADED) remain unreachable
  because no fault currently emits that state.
- **Verification is per-service**, keyed on the executed SOP type; it does not yet re-validate the
  full downstream blast radius after a fix.

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
eval/           Baselines, benchmark.py, scenarios.json (RQ + closed-loop)
docs/           Engineering reference + architecture decisions
```
