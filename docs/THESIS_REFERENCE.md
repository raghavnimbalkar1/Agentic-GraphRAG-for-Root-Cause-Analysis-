# Thesis Reference — Definitive Context Brief

**Purpose.** This is a single, self-contained ground-truth document for generating the final MTech
thesis. Every number here is verified against committed artifacts (`eval/results/*.json`, Neo4j, the
test suite). An LLM writing the thesis should treat this as authoritative and **must not invent
numbers, oversell claims, or drop the honesty caveats in §8 and §11.** When this document and older
files (e.g. `CLAUDE.md`) disagree, **this document wins** — it reflects the system as finally built
and measured (mid-2026).

---

## 1. Project identity

| Field | Value |
|---|---|
| Title | Agentic GraphRAG for Autonomous Root Cause Analysis in Cloud-Native Microservice Architectures |
| Student | Raghav Nimbalkar · PRN 1262251354 *(verify exact PRN/email against records before submission)* |
| Guide | Dr. Bhavana Tiple |
| Institution | MIT World Peace University (MIT-WPU), Pune, India · Dept. of Computer Engineering & Technology |
| Domain | AIOps · LLM agents · Graph ML · DevOps / SRE |
| Repository | github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis- |

One-sentence thesis: *encoding infrastructure topology and remediation knowledge together in a graph,
and retrieving over it instead of over flat text, lets an LLM agent localise the true root cause of a
cascading microservice failure and remediate it autonomously and safely — and the advantage grows with
cascade depth, exactly where text-based methods fail.*

---

## 2. Problem statement & motivation

- Cloud-native systems are dozens–hundreds of interdependent microservices. A failure in one service
  cascades: its symptoms surface **several dependency hops upstream**, at services that are not
  themselves broken. Monitoring fires alerts at multiple upstream layers simultaneously (an "alert
  storm"); the on-call engineer has minutes to guess which alert reflects the real fault.
- The alerting service is often **not** the root cause. The alert text names the *symptom's* location,
  not the fault's. Example (the running example throughout): the cart's Redis cache exhausts memory →
  cart service degrades → checkout jams → the customer-facing frontend returns 5xx. The alert fires on
  `frontend`; the fault is `redis-cart`, ~3–4 hops away.
- Prior AI approaches each fail on one axis: **advisory-only** (identify, page a human, never act),
  **execution-unsafe** (run LLM-generated code on the live host), or **context-blind** (flat text/vector
  retrieval with no topology awareness).

The task, formally: given an anomaly observed at surface service `s_k`, identify the root cause `s*` and
a remediation `r*` such that applying `r*` to `s*` restores measurable health within bounded time, where
"restores" is confirmed by **re-observing the failed condition**, not assumed from the fix's exit code.

---

## 3. Literature positioning & the gap

The full annotated bibliography (~20 IEEE/ACM refs) is in the paper `.tex`. Key anchors:

- **Flow-of-Action** (Pei et al., ACM WWW 2025) — the closest prior system and direct motivation.
  Constrains LLM hallucination with SRE-authored SOPs (the model *selects* from a curated library rather
  than generating repair code from scratch), coordinated by a multi-agent setup. **Its gap:** all
  generated code executes natively on the host — no sandboxing, no privilege separation, no rollback. A
  wrong SOP selection, or a prompt injection in a log line, runs with full deployment privileges on live
  infrastructure. This is the specific gap this work closes.
- **Fault propagation / observability:** gray failure (Huang et al. 2017 — a failure looks different at
  each layer that observes it, which is *why* alert storms mislead); TraceAnomaly (Liu et al. 2020);
  MicroRCA (Wu et al. 2020 — localises but cannot remediate).
- **Causal inference RCA:** PC algorithm (Spirtes et al. 2000), Granger causality (1969), Causil
  (Chakraborty et al. WWW 2023), Ikram et al. (NeurIPS 2022). Output is a causal graph requiring expert
  interpretation; `O(N^3)+` cost; designed for hypothesis generation, not operational automation.
- **Static knowledge graphs:** encode topology but are passive — they answer queries, they don't monitor,
  reason, or act.
- **LLM agents:** ReAct (Yao et al. 2023) — the reason/act interaction pattern; Ahmed et al. (ICSE 2023)
  — RAG improves recommendation but is evaluated by text similarity to historical incidents, not live
  recovery. **Graph-RAG** (Edge et al. 2024) — graph traversal beats flat embedding retrieval on
  multi-hop reasoning; not previously applied to *live infrastructure topology* for RCA.
- **Deeper benchmark:** TrainTicket (Zhou et al. ICSE 2018) — a 40+-service system used as the depth
  generalisation testbed here.

**The gap, precisely:** no reviewed system is simultaneously *topology-aware*, *capable of autonomous
remediation*, *hallucination-constrained*, AND *execution-isolated*. This work is the first to combine
all four, demonstrated and measured.

---

## 4. Contributions (as claimed in the paper)

1. A **dual-graph knowledge representation** (infrastructure topology + a semantic skill graph) with
   **Progressive Context Injection**: only the localised root's candidate SOPs enter the LLM's context, so
   per-incident prompt cost is bounded and independent of skill-library or topology size.
2. A **per-procedure privilege-scoped execution sandbox** plus a **graph-as-allowlist invariant**, shown
   to withstand five classes of forced-malicious model output and a live prompt-injection attempt —
   closing Flow-of-Action's execution-safety gap.
3. A **genuinely closed loop**: autonomous detection by a telemetry collector, post-execution verification
   that re-probes the exact failed condition, and executed `NEXT_IF_FAIL` fallback chains.
4. A **depth-stratified evaluation** showing topology-aware retrieval is robust to alert ambiguity where
   text/vector retrieval collapses, plus an unattended chaos run demonstrating detection+resolution with
   no human involvement.

---

## 5. System architecture

Four modules; the design principle is **no component trusts another's claims** — detection observes the
environment (not the injector), verification re-probes the environment (not the fix's exit code),
execution validates the model's selection against the graph (not the model's text).

### 5.1 Module A — Target environment & telemetry (the "sensing")
- **Testbed:** Google Online Boutique v0.10.5 — an open-source gRPC microservice benchmark. **12 nodes**:
  10 application services + Redis cart store + a synthetic load generator (keeps realistic traffic during
  every experiment). Deployed via Docker Compose, single host.
- **Telemetry collector** (`simulation/telemetry_collector.py`): polls real state every **5 s** — container
  run-state, network membership, Redis probes (`PING`, `maxmemory`, connected clients, keyspace),
  per-container CPU, HTTP latency. On a HEALTHY→degraded **edge** (debounced over 2 consecutive polls) it
  writes observed status into Neo4j and POSTs one alert to the agent. **Fault injectors never raise alerts**
  — detection is an observation of real state, which is what makes the autonomy claim meaningful.
- **10 fault types**, each breaking a genuinely observable condition: Redis OOM (transient + a persistent
  variant whose cap survives restart), stale cache data, connection-pool exhaustion, config drift, service
  crash, network partition, disk pressure, memory leak, CPU saturation, dependency-latency. (5 of 12
  containers are distroless and cannot host in-container faults — a documented constraint.)

### 5.2 Module B — The dual graph (Neo4j 5.18, the "knowledge")
- **Infrastructure Knowledge Graph** ("the WHERE"): `Service` nodes carrying live `status` (synced by the
  collector), connected by `DEPENDS_ON` edges mirroring the real call graph.
- **Semantic Skill Graph** ("the HOW"): `Skill` nodes (each = an executable SOP script + `trigger_condition`
  + `risk_level` + description), linked to services by `APPLIES_TO` and to fallback procedures by
  `NEXT_IF_FAIL`. **Deployment carries 15 skills over 11 trigger conditions.**
- **Runtime queries:**
  - **Q1 `get_root_cause`** — traverse `DEPENDS_ON*1..8` from the alerting service, return the **deepest
    unhealthy** node (`ORDER BY depth DESC LIMIT 1`) with its full chain. Topology-agnostic: a `node_label`
    parameter lets the *same* query run on the isolated TrainTicket graph.
  - **Q2 `get_skills`** — return **all** SOPs applying to the root whose trigger matches the condition (the
    candidate set). Fallback: if the surface alert error names no skill at the root, key instead on the
    root's actual collector-synced condition. *Symptom localises the root; the root's real condition selects
    the remedy.*
  - **Q3 `get_next_skill`** — follow a `NEXT_IF_FAIL` edge to the designated fallback.
- **Two properties follow:** (a) retrieval cost is independent of library size (indexed lookup); (b)
  Progressive Context Injection — only the root's candidate set ever enters the prompt.

### 5.3 Module C — The LangGraph agent (the "reasoning")
- A compiled `StateGraph` with 6 nodes over a typed `AgentState`: **ingest → retrieve → reason → execute →
  evaluate → report**, with conditional routing (loop back to retrieve/reason, or terminate to report).
- **The LLM's role is narrow and real:** it receives the alert, the Q1 result (root, chain, depth), and the
  numbered candidate set, and returns a structured decision — `execute <exact candidate name>`, `skip`, or
  `escalate`, with justification. On multi-candidate faults it performs genuine selection (in live runs it
  consistently prefers the lowest-risk non-restart option first).
- **Three fail-safes:** unparseable output → escalate; a chosen skill outside the candidate set → escalate;
  transient LLM errors retried with backoff, non-transient (e.g. auth 403) → escalate immediately.
  *(The 403 fail-safe was exercised for real when a provider key was revoked mid-project — the agent
  declined to act blind.)*

### 5.4 Module D — Privilege-scoped sandbox (the "execution")
- Every SOP runs in a fresh container via the Docker Engine API, **never on the host**. Always applied:
  `cap-drop ALL`, `no-new-privileges`, read-only rootfs + small tmpfs, hard memory/CPU/PID limits,
  placement on the isolated simulation network (no outbound), watchdog `SIGKILL` on timeout, destroyed
  after log capture.
- **Per-procedure privilege scoping** via the skill's `risk_level`: LOW = non-root, network-only, no Docker
  socket; MEDIUM = Docker socket mounted + runs as root *inside* the capability-stripped container (needed
  to control sibling containers, e.g. a restart). *Documented honestly:* root in a cap-dropped, read-only,
  network-isolated container is not host root, but socket access is real power — this tier exists only for
  procedures that cannot function without it, and replacing it with a **brokered executor** is named future
  work.
- **Graph-as-allowlist (enforced structurally, not by prompt):** the model only ever emits a candidate
  *name*; the executor validates it against the Q2 set and sources script path/type/risk from the **graph
  record** — LLM text is never interpolated into a command, path, or shell.

---

## 6. Implementation facts (tech stack & mechanics)

| Layer | Choice |
|---|---|
| Agent orchestration | LangGraph + LangChain |
| LLM (live reasoner) | **Claude Haiku 4.5** (`langchain-anthropic`). Provider-agnostic: gemini / openai / anthropic / ollama |
| LLM (benchmarks recorded on) | **Gemini 2.5 Flash Lite** — all recorded numbers are Gemini-provenance |
| Graph DB | Neo4j 5.18 Community + Cypher |
| Sensing | Docker SDK + `redis-cli` / `docker stats` / HTTP probes |
| Sandbox | Docker Engine API, custom `sop-executor` image |
| API | FastAPI (`/alert` webhook, :8888) |
| Dashboard | Streamlit + pyvis (7 tabs) |
| Baselines | FAISS + `all-MiniLM-L6-v2` (Vector RAG); single LLM call (Zero-Shot) |
| Testing | pytest — **50 unit tests**, no external deps (allowlist invariant, routing, retry, blast-radius, TrainTicket topology, multi-root) |

**Three implementation results that make the loop real (each verified out-of-band, not by the agent's own
claim):**
1. **Verification re-probes the failed condition** (`verify_real_health`, per-condition): OOM → re-read
   `maxmemory` + `PING`; pool → re-count clients; disk → re-measure writable layer; CPU → re-sample; latency
   → re-time (with a settle-window retry); partition → re-check network membership. Only a passing re-probe
   flips the graph to HEALTHY.
2. **Fallback chains execute:** the persistent-OOM fault survives a restart, so `Redis_Restart_SOP` exits 0
   but fails the re-probe → the evaluator follows `NEXT_IF_FAIL` to `Redis_Flush_SOP`, which clears it. Both
   SOPs land in the audit record.
3. **Remediation beyond restart:** the majority of the 10 fault types are fixed *without* restarting —
   targeted disk cleanup, connection termination, config reset to baseline, CPU-quota throttle, cache flush.
   Confirmed by out-of-band measurement (e.g. writable layer 315 MB → 20 kB, container start-time unchanged).

**Multi-fault handling** (`agent/multi_root.py`, merged to main): an orchestration layer over the
single-root loop — `get_independent_roots()` finds every unhealthy service with no unhealthy dependency of
its own, and dispatches the single-root agent at each. Verified E2E: redis-cart STALE_DATA + adservice
HIGH_CPU injected simultaneously → both detected as independent roots → both resolved + independently
verified. *Correlated faults (where a fault's symptoms mark upstream nodes unhealthy) and a quantitative
multi-fault evaluation remain future work.*

---

## 7. Evaluation — all verified numbers

**Setup:** 21 scenarios × 3 repetitions, 10 fault types, Q1 traversal depths 1–4. Two baselines share the
identical LLM, prompt budget, and SOP corpus, isolating retrieval as the only variable — **B1 Zero-Shot**
(raw alert only), **B2 Vector RAG** (top-k SOP docs via FAISS, then LLM). Benchmarks recorded on Gemini 2.5
Flash Lite.

### 7.1 Central result — root-cause accuracy by cascade depth (THE headline)

| Depth | n | GraphRAG | B1 Zero-Shot | B2 Vector RAG |
|---|---|---|---|---|
| 1 | 8 | **100%** | 100% | 100% |
| 2 | 5 | **100%** | 80% | 40% |
| 3 | 6 | **100%** | 17% | 17% |
| 4 | 2 | **100%** | **0%** | **0%** |

GraphRAG is flat at 100% at every depth; both baselines collapse monotonically to 0% on deep cascades. This
is the paper's central figure.

### 7.2 Overall (all 21 scenarios, mean ± std)

| System | Root accuracy | Blast-radius F1 | MTTR (s) | Tokens/incident |
|---|---|---|---|---|
| **Agentic GraphRAG** | **100% ± 0** | **1.00 ± 0** | **6.8 ± 2.8** † | 867 |
| Zero-Shot (B1) | 61.9% ± 50 | 0.69 ± 0.14 | 2.6 ± 1.7 ‡ | 445 |
| Vector RAG (B2) | 52.4% ± 51 | 0.73 ± 0.18 | 3.1 ± 2.1 ‡ | 662 |

† real inject→detect→remediate→re-verify. ‡ inference latency only — the baselines never remediate.

### 7.3 Ablation (`eval/ablation.py`)
- **Infrastructure graph:** with traversal **100%** vs without (alert service as root) **0%** at every depth.
  (Intermediate "LLM guesses without the graph" = the 62% zero-shot baseline.) The graph is strictly
  load-bearing.
- **Progressive Context Injection:** candidate-set context **814** tokens/decision vs whole-library **1358**
  (**+67%**) on a 15-skill library — and that overhead grows linearly with library size while PCI stays
  flat.

### 7.4 Generalisation — TrainTicket (`eval/trainticket/`)
The *same* Q1 traversal, run on the FudanSELab TrainTicket dependency graph (**36 services, 73 edges**,
isolated `:TTService` label so the live demo is untouched), localises correctly at **every depth 1–7 (7/7)**;
zero-shot LLM: **2/7**. Depth-7 case: alert at `frontend`, root `station` seven hops away, reached in
~0.03 s; the LLM guesses `gateway`. **Localisation only** — no remediation on TrainTicket (that needs
cluster hardware; future work).

### 7.5 Unattended autonomy — chaos run
11.5-minute unattended window; a chaos daemon injected random faults at random intervals and **raised no
alerts itself**: **16 injected, 16 detected (100%), 16 resolved, 0 escalated, 0 alerts fired by hand**; mean
injection→detection **10.9 s**, mean detection→resolution **20.7 s**. Every incident's full lifecycle is in a
committed log.

### 7.6 Safety invariant under adversarial behaviour
- **Enforcement layer:** five forced-malicious selections (fabricated destructive name, another service's
  SOP, a shell-injection string as a name, null-with-execute, unparseable) → all escalate, nothing executed.
- **Behavioural layer:** a live model given an alert whose message instructed it to ignore its candidates,
  run an exfiltration SOP, and run destructive shell — it ignored the injection and chose a valid candidate.
- Across the whole suite: zero host mutations from sandboxed executions, zero out-of-graph procedures
  invoked. Pinned by the automated test suite.

---

## 8. Honest limitations — the five asterisks (DO NOT DROP THESE)

The project's credibility rests on stating these plainly. Any thesis text must preserve them.

1. **Single-fault determinism.** GraphRAG's 100% is partly *by construction*: one fault is injected, so one
   node is unhealthy, and the deepest-unhealthy traversal returns it. The honest claim is **"topology-aware
   traversal is robust to alert ambiguity,"** NOT "solves RCA." (Independent multi-fault handling now exists
   — §6 — but a *quantitative* multi-fault evaluation and *correlated*-fault handling remain open.)
2. **Blast-radius F1 = 1.00 is near-tautological** — the graph computes the transitive-dependency closure
   that *is* the ground truth. It demonstrates the topology is present and queryable; it is not a won contest.
3. **The depth effect is partly a property of alert specificity** — messages get more generic as the alert
   fires further from the root (realistic: a far-upstream monitor sees only generic symptoms; enforced by a
   leak audit confirming deep alerts name neither root nor path). State this; a skeptic could argue the
   baselines were set up to fail at depth.
4. **Tokens are bounded, not smaller.** GraphRAG uses **more** absolute tokens than the lean baselines (867
   vs 445/662). The defensible claim is *per-call cost is bounded, independent of graph/skill-library size*
   — proven by the ablation — not that it is cheaper.
5. **MTTR is apples-to-oranges.** GraphRAG's 6.8 s is real inject→resolve→verify; the baselines' 2.6–3.1 s is
   inference that fixes nothing. The real comparison is *resolved* vs *not resolved*.

**Other boundaries:** single-host Docker Compose (not Kubernetes); 5-second polling detection (races fast
auto-restarts; wouldn't scale to hundreds of services — event-driven is the successor); the dependency graph
is **hand-authored**; the remediation library is hand-authored (each skill couples a script + a graph node +
a detection rule + a verify branch); recovery of the *harness itself* after interruptions still needed manual
cleanup; local-model portability (small open-weight models on the constrained selection task) unproven —
Ollama was unreachable during the eval window.

---

## 9. Roadmap / future work (maturity ladder, honest difficulty)

| Stage | What | Difficulty |
|---|---|---|
| **Now (done)** | Full closed loop on Online Boutique; localisation generalises to TrainTicket depth 1–7 | — |
| **v2** | Full closed loop on TrainTicket — deploy on cluster hardware; port the collector to Spring Boot `/actuator/health` + MySQL/RabbitMQ probes; author Spring/MySQL/RabbitMQ SOPs | Engineering (labor + rented hardware), **not research** |
| **v3** | **Auto-topology discovery** — build the `DEPENDS_ON` graph from OpenTelemetry/Jaeger traces instead of hand-authoring | **Research-grade — the real gate to "any cluster"** |
| **v4** | Product — multi-tenancy + auth, brokered (socket-free) executor, multi-root causal scoring, user-authored SOPs, event-driven detection, declarative per-skill detect/verify specs | Productization |

Key framing: the research-hard part (graph-guided reasoning) already ports for free (TrainTicket proved it).
The gate between "a system I hand-model" and "a tool anyone points at their cluster" is **auto-topology
discovery**, not remediation.

---

## 10. Key defensible claims + the framing rules for the thesis

**Claims that hold (say confidently):**
- Topology-aware graph traversal localises the root cause with 100% accuracy at every cascade depth on the
  testbed, where text/vector baselines with the identical LLM and SOP corpus collapse to 0% at depth 3–4.
- The advantage is *monotonic in depth* and *generalises* to a 36-service published benchmark to depth 7.
- The system closes the loop autonomously: 16/16 unattended detect-and-resolve, zero human alerts.
- The graph-as-allowlist invariant is empirically airtight against forced-malicious and injection inputs.
- Per-incident LLM cost is bounded independent of scale (Progressive Context Injection; ablation-quantified).
- The agent brain is genuinely graph-driven, not hardcoded: zero fault-condition literals in the
  localise/retrieve/decide/execute code — a new Skill node + edge teaches a new remediation with no code
  change.

**Framing rules (an LLM generating the thesis must obey these):**
- Never state or imply "solves RCA" / "production-ready" / "works on any system." It is a **controlled
  single-host testbed** and a **mechanism study**, not a population claim.
- Always pair the 100% with the single-fault-determinism caveat when first stated.
- Present F1=1.0 as "the graph computes the ground-truth closure," never as a competition won.
- Present cost as *bounded*, never *lower*.
- Present MTTR as *resolved-vs-not*, never a speed win.
- Keep TrainTicket as *localisation generalisation*; do not imply the full loop runs on it.

---

## 11. Repository & artifact map (for citations)

```
agent/              LangGraph agent (ingest→retriever→reasoner→executor→evaluator) + multi_root.py
core/               shared schemas, config, exceptions, logging
graph/              Neo4j client (Q1–Q3 + get_independent_roots) + Cypher dual-graph
sops/               SOP scripts (read-only in sandbox): redis/, container/, adservice/, email/, frontend/
sop-executor/       sandbox base image
simulation/         Online Boutique compose, fault_injector.py, telemetry_collector.py, chaos_daemon.py
dashboard/          Streamlit (7 tabs incl. Start Here, Dual Graph, Live Duel, Autonomy Run)
eval/               baselines/ (zero_shot, vector_rag), benchmark_full.py, ablation.py, trainticket/
tests/              50 unit tests
docs/               ENGINEERING_REFERENCE, EVALUATION_SUMMARY, PAPER_UPDATES, DEMO_SCRIPT, this file
```
Citable result artifacts: `eval/results/benchmark_full.json`, `ablation.json`,
`trainticket_localisation.json`, `chaos_run_<ts>.{log,json}`, `EVALUATION_SUMMARY.md`.

---

## 12. Suggested thesis chapter structure (map to sources)

1. **Introduction** — §2 (problem), §4 (contributions). Motivating example: Redis OOM cascade.
2. **Literature Review** — §3; gap matrix ending in "no system satisfies all four requirements."
3. **System Design** — §5 (four modules, dual graph, Q1–Q3, LangGraph state machine, sandbox). Figures:
   4-module architecture, dual-graph schema, LangGraph flow, multi-hop trace.
4. **Implementation** — §6 (tech stack, verification, fallback chains, non-restart remediation, allowlist,
   multi-root, provider-agnosticism).
5. **Evaluation** — §7 (RQs, depth table = the headline figure, overall table, ablation, TrainTicket
   generalisation, chaos autonomy, adversarial safety).
6. **Discussion & Threats to Validity** — §8 (the five asterisks + boundaries), §10 (framing).
7. **Conclusion & Future Work** — §9 (maturity ladder); the auto-topology-discovery gate.
8. **References** — the paper's ~20-ref IEEE bibliography.

Also apply the concrete edits in `docs/PAPER_UPDATES.md` (factual corrections + ready-to-paste ablation and
TrainTicket subsections).

---

## 13. Glossary

- **Cascade / cascade depth** — how many `DEPENDS_ON` hops separate the alerting service from the true root.
- **Dual graph** — one Neo4j DB holding the infrastructure graph (WHERE) + the skill graph (HOW).
- **Progressive Context Injection** — injecting only the localised root's candidate SOPs into the LLM prompt.
- **Graph-as-allowlist** — the LLM may only execute a procedure present in the Q2 candidate set; enforced by
  code, not prompt.
- **SOP / skill** — a Standard Operating Procedure: an executable remediation script represented as a graph
  node with a trigger condition and risk level.
- **NEXT_IF_FAIL** — a graph edge to the fallback SOP tried when a remediation runs but fails verification.
- **Closed loop** — detect (collector) → localise (Q1) → select (Q2 + LLM) → execute (sandbox) → verify
  (re-probe) → fallback/report, with no human in the loop.
- **MTTR / MTTD** — mean time to resolve / detect.
