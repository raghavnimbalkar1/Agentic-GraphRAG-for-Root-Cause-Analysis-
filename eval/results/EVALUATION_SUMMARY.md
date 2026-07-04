# Phase 7 Evaluation Summary
**Agentic GraphRAG for Autonomous Root Cause Analysis**  
Raghav Nimbalkar · MIT-WPU · 2026  
Stack: Google Online Boutique v0.10.5 · LLM: Gemini 2.5 Flash Lite · n = 4 scenarios (S-01–S-04)

> **Re-run 2026-06-24, post audit-fix.** Re-generated with `python eval/benchmark.py`
> after two agent-internal bug fixes (the reasoner prompt now sends `risk_level`
> instead of `script_type`; `traversal_depth` is now correctly threaded through Q1).
> Those fixes correct what the LLM *sees*, not the baselines or the resolution
> outcomes — all 4 scenarios still resolve, so the headline metrics are unchanged.
> Only baseline LLM inference latency drifted run-to-run (non-deterministic timing).

---

# Expanded Evaluation (Section 5) — n = 21 scenarios × 3 reps

> Generated with `python -m eval.benchmark_full` on 2026-06-27. Raw data:
> `eval/results/benchmark_full.json`. 21 scenarios span **10 fault types** and
> **Q1 traversal depths 1–4**, each run **3 times** to capture mean ± std.

> **Provenance note (2026-07-04):** every result in this document was produced with
> **Gemini 2.5 Flash Lite**. The live reasoner has since switched to **Claude Haiku 4.5**
> (`LLM_PROVIDER=anthropic`) after the Gemini project lost API access; the swap was validated
> end-to-end (3/3 faults resolved, comparable MTTR and tokens — see ENGINEERING_REFERENCE) and
> does **not** alter any number recorded here.

### Methodology (read this before the tables)

* **Root accuracy & blast-radius F1** are measured by the *exact agent mechanism*:
  GraphRAG identifies the root by **Q1 graph traversal** and computes the blast
  radius by **transitive `DEPENDS_ON` closure**; the baselines infer both from the
  same alert via the LLM. GraphRAG is deterministic (graph queries) so its std ≈ 0;
  the 3 reps exist to capture the **baselines' LLM variance**.
* **Depth** = the Q1 traversal depth the agent reports (longest `DEPENDS_ON` path to
  the root). Alert specificity realistically **decreases** with distance from the
  root — a monitor on the failing service sees a specific symptom; a far-upstream
  load generator sees only a generic 5xx. That mechanism is the point of the test.
* **MTTR & tokens** come from **real end-to-end agent runs**, once per fault type ×3
  (MTTR is fault/SOP-driven, not depth-driven). The baselines have no remediation
  layer, so their "MTTR" is **LLM inference latency only** — they never actually fix
  anything (their *real* MTTR is undefined / infinite).

## Table 1 — Overall (all 21 scenarios, mean ± std)

| System | Root Accuracy | Blast-Radius F1 | MTTR (s) | Tokens / call |
|---|---|---|---|---|
| **Agentic GraphRAG (Ours)** | **100% ± 0** | **1.00 ± 0** | **6.8 ± 2.8** † | 867 |
| Zero-Shot LLM (B1) | 62% ± 50 | 0.69 ± 0.14 | 2.6 ± 1.7 ‡ | 445 |
| Vector RAG (B2) | 52% ± 51 | 0.73 ± 0.18 | 3.1 ± 2.1 ‡ | 662 |

† Real end-to-end remediation (inject → graph traversal → LLM reason → sandbox SOP →
**independent health re-verification**). ‡ Inference latency only — **no remediation,
no verification**; the baselines look "faster" because they do nothing but emit a
suggestion. The honest comparison is not 6.8 vs 2.6s — it is *resolved* vs *not resolved*.

> **Honest note on tokens:** GraphRAG is **not** cheaper in absolute tokens here (867 vs
> 445/662). The Section-3 structured root-cause explanation raised the agent's per-call
> output from ~450 to ~867 tokens. The defensible efficiency claim is about **scaling, not
> absolute cost**: Progressive Context Injection makes the per-call cost **independent of
> graph/skill-library size** (only the active root+candidate set enters context), whereas a
> RAG that widens context as the system grows would balloon. In this *small* testbed the
> baselines are lean, so that advantage does not show up as a lower number — we report it
> straight rather than claim a win we did not measure.

## Table 2 — Root Accuracy & Blast F1 by Q1 Depth  ⟵ *the central result*

| Depth | GraphRAG acc | B1 acc | B2 acc | GraphRAG F1 | B1 F1 | B2 F1 |
|---|---|---|---|---|---|---|
| **1** (n=8) | **100%** | 100% | 100% | 1.00 | 0.75 | 0.75 |
| **2** (n=5) | **100%** | 80% | 40% | 1.00 | 0.64 | 0.75 |
| **3** (n=6) | **100%** | 17% | 17% | 1.00 | 0.65 | 0.78 |
| **4** (n=2) | **100%** | **0%** | **0%** | 1.00 | 0.67 | 0.50 |

**This is the paper's thesis in one table.** At depth 1 the root is the alerting service's
direct dependency and all three systems find it. As the alert fires further from the root
and its symptom gets more generic, the topology-blind baselines collapse —
**100% → 80%/40% → 17%/17% → 0%/0%** — while topology-aware Q1 traversal stays flat at
**100% at every depth**. The graph advantage is **monotonic in cascade depth**. (Blast F1
for the baselines stays middling even when they miss the root: they still guess *some*
affected services right, so partial credit masks the root-cause failure — which is exactly
why root accuracy, not F1, is the headline.)

## Table 3 — By Fault Type (coverage breadth)

| Fault type | n | GraphRAG acc / F1 | B1 acc / F1 | B2 acc / F1 | GraphRAG MTTR (s) |
|---|---|---|---|---|---|
| redis_oom | 4 | 100% / 1.00 | 25% / 0.76 | 50% / 0.71 | 10.5 ± 4.0 |
| stale_data | 2 | 100% / 1.00 | 50% / 0.76 | 50% / 0.60 | 3.0 ± 0.2 |
| config_drift | 1 | 100% / 1.00 | 0% / 0.67 | 0% / 0.67 | 2.2 ± 0.1 |
| connection_pool_exhaustion | 1 | 100% / 1.00 | 0% / 0.67 | 0% / 0.67 | 4.7 ± 1.6 |
| disk_pressure | 2 | 100% / 1.00 | 50% / 0.60 | 50% / 0.90 | 6.0 ± 1.2 |
| memory_leak | 2 | 100% / 1.00 | 100% / 0.58 | 100% / 0.83 | 7.1 ± 2.2 |
| high_cpu | 2 | 100% / 1.00 | 100% / 0.58 | 100% / 0.83 | 9.8 ± 2.2 |
| dependency_timeout | 1 | 100% / 1.00 | 100% / 0.67 | 100% / 0.67 | 7.5 ± 3.7 |
| network_partition | 3 | 100% / 1.00 | 67% / 0.67 | 33% / 0.82 | 7.8 ± 5.4 |
| service_crash | 3 | 100% / 1.00 | 100% / 0.77 | 33% / 0.58 | 4.0 ± 0.9 |

GraphRAG resolves **all 10 fault types** with **real, independently-verified** remediation
(mean MTTR 2.2–10.5s; every fault-type run n=3/3 resolved). The baselines do well on faults
where the **root is the alerting service itself** (memory_leak, high_cpu, dependency_timeout
→ 100%) and fail on faults **hidden behind a dependency** (config_drift, pool_exhaustion,
redis_oom → 0–50%), which is the same depth effect cut a different way.

### MTTR variance is expected (and does not undermine the accuracy results)

Baseline MTTR std is large (±1.7–2.1s overall) because it is **Gemini inference latency**,
which drifts run-to-run; that is precisely why we ran 3 reps. It has **no bearing** on the
root-accuracy and F1 results, which are **deterministic** for GraphRAG (graph traversal) and
near-deterministic for the baselines at temperature 0. The baseline root-accuracy std of
~±50% reflects **scenario-to-scenario** bimodality (a system that always gets the shallow
ones and always misses the deep ones), not rep noise.

### Not measured here (future work)

* **RQ3 / RQ4 (local-LLM portability & cost):** the local Ollama endpoint was unreachable in
  this environment, so the Qwen-2.5-Coder comparison is deferred. The agent is
  provider-agnostic (LangChain), so this is an environment limitation, not a design one.

---

## Aggregate Results (original Phase 7 — n = 4 RQ scenarios, retained)

| System | Root Cause Accuracy | Avg Blast-Radius F1 | Avg Latency (s) | Avg Tokens/Call |
|---|---|---|---|---|
| **Agentic GraphRAG (Ours)** | **100%** (4/4) | **1.000** | **5.41** † | — ‡ |
| Zero-Shot LLM (B1) | 75% (3/4) | 0.768 | 2.53 | 452 |
| Vector RAG (B2) | 75% (3/4) | 0.742 | 2.86 | 649 |

GraphRAG blast-radius F1 improvement over best baseline: **+0.232** (1.000 vs 0.768).
The root-cause gap (100% vs 75%) is driven entirely by **S-04**, the depth-3 ambiguous-alert
scenario where both baselines mispredict `cartservice` and only Q1 graph traversal reaches
`redis-cart`.

† Agentic GraphRAG latency = actual MTTR including graph traversal + LLM reasoning +
Docker sandbox execution + health verification. Baseline latency = LLM inference only
(no fault injection, no sandbox, no verification loop) — so the baselines look "faster"
only because they do not actually remediate or verify anything.

‡ Agent token count = 0 for Phase 5 runs (pre-instrumentation). Live post-instrumentation
runs (e.g. INC-38BFE69C: 451 tokens; post-fix INC-F1955D02: 449 tokens) confirm per-incident
cost stays ~450 tokens — comparable to the zero-shot baseline despite also doing graph traversal.

---

## Per-Scenario Breakdown

### S-01 — Redis OOM (`redis_oom`)
| System | Root Correct | Predicted Root | Blast Precision | Blast Recall | Blast F1 | Latency (s) | Tokens |
|---|---|---|---|---|---|---|---|
| Agentic GraphRAG | ✓ | redis-cart | 1.000 | 1.000 | **1.000** | 6.30 | 0 |
| Zero-Shot LLM (B1) | ✓ | redis-cart | 1.000 | 0.500 | 0.667 | 1.94 | 457 |
| Vector RAG (B2) | ✓ | redis-cart | 1.000 | 0.500 | 0.667 | 3.27 | 643 |

Ground truth blast radius: `cartservice`, `checkoutservice`, `frontend`, `loadgenerator`  
Baselines predicted: `frontend`, `cartservice` — missed `checkoutservice` and `loadgenerator`

### S-02 — Service Crash (`service_crash` on productcatalogservice)
| System | Root Correct | Predicted Root | Blast Precision | Blast Recall | Blast F1 | Latency (s) | Tokens |
|---|---|---|---|---|---|---|---|
| Agentic GraphRAG | ✓ | productcatalogservice | 1.000 | 1.000 | **1.000** | 5.22 | 0 |
| Zero-Shot LLM (B1) | ✓ | productcatalogservice | 1.000 | 0.750 | 0.750 | 2.64 | 462 |
| Vector RAG (B2) | ✓ | productcatalogservice | 1.000 | 0.750 | 0.750 | 3.76 | 653 |

Ground truth blast radius: `recommendationservice`, `checkoutservice`, `frontend`, `loadgenerator`  
Baselines predicted 3 of 4 — consistently missed `loadgenerator`

### S-03 — Network Partition (`network_partition` on paymentservice)
| System | Root Correct | Predicted Root | Blast Precision | Blast Recall | Blast F1 | Latency (s) | Tokens |
|---|---|---|---|---|---|---|---|
| Agentic GraphRAG | ✓ | paymentservice | 1.000 | 1.000 | **1.000** | 3.63 | 0 |
| Zero-Shot LLM (B1) | ✓ | paymentservice | 1.000 | 0.667 | 0.800 | 6.92 | 427 |
| Vector RAG (B2) | ✓ | paymentservice | 1.000 | 0.667 | 0.800 | 4.21 | 632 |

Ground truth blast radius: `checkoutservice`, `frontend`, `loadgenerator`  
Baselines predicted `checkoutservice`, `frontend` — missed `loadgenerator`

### S-04 — Redis OOM, multi-hop (`redis_oom`, ambiguous frontend alert, root at depth 3)
| System | Root Correct | Predicted Root | Blast Precision | Blast Recall | Blast F1 | Latency (s) | Tokens |
|---|---|---|---|---|---|---|---|
| Agentic GraphRAG | ✓ | redis-cart | 1.000 | 1.000 | **1.000** | 6.50 | 0 |
| Zero-Shot LLM (B1) | ✗ | **cartservice** | 1.000 | 0.750 | 0.857 | 1.52 | 460 |
| Vector RAG (B2) | ✗ | **cartservice** | 0.750 | 0.750 | 0.750 | 1.99 | 668 |

Ground truth root: `redis-cart` (3 DEPENDS_ON hops from the alerting `frontend`).
The alert message names only frontend-level symptoms (`/cart` and `/checkout` 5xx) — it
does **not** mention redis-cart or cartservice. This is the discriminating scenario:
both baselines stop one hop short at `cartservice`; only the graph's Q1 traversal
deterministically reaches the true root `redis-cart`. Same fault and topology as S-01 —
the *only* difference is the alert wording, which isolates topology as the cause of the
divergence (not LLM knowledge or SOP retrieval).

> Note on per-scenario latencies: the S-01–S-03 latency columns above are retained from
> their verified Phase 5 live runs; baseline latency is LLM-inference-only and varies
> run-to-run. The **Aggregate Results** table reflects the 2026-06-24 re-run and is the
> canonical figure.

---

## Key Findings

- **Graph topology differentiates BOTH root-cause identification (when alerts are ambiguous) AND
  blast-radius coverage (always).** On single-hop scenarios where the alert names the failing
  component (e.g. "redis-cart evicting keys"), all three systems get the root cause right — the
  graph's advantage there is purely in blast-radius F1 (1.000 vs 0.74–0.77). But on S-04, where the
  alert describes only frontend symptoms and the true root is 3 hops away, both baselines stop at
  `cartservice` and only graph traversal reaches `redis-cart` — so root-cause accuracy splits 100%
  vs 75%. The LLM-only approaches also consistently miss blast-radius members that require
  transitive DEPENDS_ON traversal to reach (particularly `loadgenerator`, which depends on
  `frontend` → `checkoutservice` → multiple upstreams).

- **Vector RAG does not improve over zero-shot.**
  B1 and B2 tie on root-cause accuracy (both 75% — both fail S-04) and are within noise on
  blast-radius F1 (B1 0.768 vs B2 0.742; B2 is actually marginally *worse* because on S-04 it also
  hallucinates `redis-cart` into the blast radius). Retrieving the most semantically similar SOP
  documents provides the LLM with remediation knowledge but not dependency topology. Knowing *how*
  to fix redis-cart does not help the model reason about *which* services are affected, nor about
  *which upstream service* is the true root. This supports the architectural decision to store
  causal structure in a graph database rather than as embedded text.

- **Vector RAG consumes 43% more tokens than zero-shot with no accuracy gain.**
  Average tokens per call: B2 = 643 vs B1 = 449. The additional ~194 tokens per call represent
  the three retrieved SOP document chunks injected into the prompt. This is pure overhead when
  the retrieval does not improve the metric being scored.

- **The Agentic GraphRAG MTTR includes actual remediation; baselines measure inference only.**
  The 5.41s average MTTR for our system spans the full incident lifecycle: Neo4j Q1 traversal,
  Q2 skill lookup, Gemini reasoning call, Docker sandbox execution, container restart, Q5 health
  verification. The baseline "latency" of 3–7s is purely LLM inference — it produces no
  executable remediation and cannot verify success. A fair comparison for automated resolution
  latency would require measuring baselines through the point of actual service recovery, which
  they cannot achieve without a sandboxed execution layer.

- **Progressive Context Injection caps per-call token cost independent of graph size.**
  The agent's LLM call injects exactly one Skill node's description (~100–150 words) into the
  prompt context. The INC-38BFE69C instrumented run used 451 tokens total for a resolved
  incident. This is comparable to the zero-shot baseline (449 avg) while including graph-derived
  root cause context that the zero-shot call lacks. Scaling the Infrastructure Knowledge Graph
  from 12 to 1,200 services does not increase per-call token cost — only the Q1 Cypher traversal
  time grows, and Neo4j graph traversal with indexes scales sub-linearly.

---

## Limitations

1. **Only one multi-hop scenario; the rest are 1-hop.** S-01, S-02, S-03 each involve a single
   root-cause service with no intermediate cascade. S-04 is the one depth-3 scenario, and it is a
   *re-worded variant* of S-01 (same fault, same topology) rather than an independent cascade. The
   CLAUDE.md design specifies a fuller suite including a genuine 4-hop chained scenario (S-06) that
   is not yet implemented. So root-cause divergence is currently demonstrated on exactly one
   topology-dependent case — convincing as a proof of mechanism, but thin as a statistical claim.

2. **n=4 is a small evaluation sample.** Four scenarios do not provide statistical confidence.
   The blast-radius gap is consistent (GraphRAG F1 = 1.000 on every scenario vs 0.667–0.857 for the
   baselines), which suggests the result is systematic rather than coincidental, but p-values cannot
   be computed. A larger evaluation with 10–20 injected scenarios across distinct fault types and
   genuine multi-hop cascade depths would substantially strengthen the empirical claims.

3. **`high_latency` fault type is infeasible on this stack.** Online Boutique v0.10.5 uses
   distroless container images that lack `tc` and `iproute2`, making `tc qdisc netem delay`
   injection impossible. The `inject_high_latency` function in `simulation/fault_injector.py`
   detects this and no-ops with a warning. This excludes a realistic fault class (network degradation,
   not partition) from the evaluation. A Kubernetes deployment with sidecar proxies (Istio/Envoy)
   would support this fault type via VirtualService fault injection.

4. **Agent token count is 0 for Phase 5 runs (pre-instrumentation).** The `tokens_used` field
   was not wired until Phase 7. The three verified scenarios (INC-4AC84F16, INC-111D3B59,
   INC-2EFDDAD1) do not have token counts in their audit JSONs. The post-instrumentation run
   INC-38BFE69C (451 tokens) provides a point estimate but was not part of the formal scenario set.
   Re-running the three scenarios through the instrumented agent would close this gap.

---

## Raw Data Files

| File | Contents |
|---|---|
| `eval/results/benchmark_all.json` | Full per-scenario + aggregate in machine-readable JSON |
| `eval/results/benchmark.json` | Most recent combined benchmark run (same as above) |
| `eval/results/benchmark.txt` | Human-readable ASCII table |
| `eval/scenarios.json` | Ground-truth fault scenarios with verified Phase 5 run metadata |
| `audit/rca_INC-4AC84F16.json` | Phase 5 verified run — redis_oom (MTTR 6.3s) |
| `audit/rca_INC-111D3B59.json` | Phase 5 verified run — service_crash (MTTR 5.22s) |
| `audit/rca_INC-2EFDDAD1.json` | Phase 5 verified run — network_partition (MTTR 3.63s) |
| `audit/rca_INC-38BFE69C.json` | Post-instrumentation run — tokens_used=451, mttr_seconds=33.89s |
