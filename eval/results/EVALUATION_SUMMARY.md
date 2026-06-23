# Phase 7 Evaluation Summary
**Agentic GraphRAG for Autonomous Root Cause Analysis**  
Raghav Nimbalkar · MIT-WPU · 2026  
Stack: Google Online Boutique v0.10.5 · LLM: Gemini 2.5 Flash Lite

---

## Aggregate Results

| System | Root Cause Accuracy | Avg Blast-Radius F1 | Avg Latency (s) | Avg Tokens/Call |
|---|---|---|---|---|
| **Agentic GraphRAG (Ours)** | **100%** | **1.000** | **5.05** † | — ‡ |
| Zero-Shot LLM (B1) | 100% | 0.739 | 3.83 | 449 |
| Vector RAG (B2) | 100% | 0.739 | 3.75 | 643 |

† Agentic GraphRAG latency = actual MTTR including graph traversal + LLM reasoning +
Docker sandbox execution + health verification. Baseline latency = LLM inference only
(no fault injection, no sandbox, no verification loop).

‡ Agent token count = 0 for Phase 5 runs (pre-instrumentation). Live post-instrumentation
run INC-38BFE69C confirms 451 tokens / 33.89s MTTR (includes full sandbox restart cycle).

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

---

## Key Findings

- **Graph topology is the differentiator for blast-radius coverage, not root-cause identification.**
  On single-hop scenarios where the alerting service directly names the failing component (e.g.,
  "redis-cart evicting keys"), all three systems correctly identify the root cause. The advantage
  of the dual-graph architecture manifests in blast-radius F1: 1.000 vs 0.739 for both baselines,
  a 35% relative improvement. The LLM-only approaches consistently miss services that require
  transitive DEPENDS_ON traversal to reach (particularly `loadgenerator`, which depends on
  `frontend` → `checkoutservice` → multiple upstreams).

- **Vector RAG does not improve blast-radius coverage over zero-shot.**
  Both B1 and B2 achieve identical root-cause accuracy and blast-radius F1 across all three
  scenarios. Retrieving the most semantically similar SOP documents provides the LLM with
  remediation knowledge but not dependency topology. Knowing *how* to fix redis-cart does not
  help the model reason about *which* downstream services are affected. This supports the
  architectural decision to store causal structure in a graph database rather than as embedded
  text.

- **Vector RAG consumes 43% more tokens than zero-shot with no accuracy gain.**
  Average tokens per call: B2 = 643 vs B1 = 449. The additional ~194 tokens per call represent
  the three retrieved SOP document chunks injected into the prompt. This is pure overhead when
  the retrieval does not improve the metric being scored.

- **The Agentic GraphRAG MTTR includes actual remediation; baselines measure inference only.**
  The 5.05s average MTTR for our system spans the full incident lifecycle: Neo4j Q1 traversal,
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

1. **Only 1-hop direct fault scenarios tested.** All three verified scenarios (S-01, S-02, S-03)
   involve a single root-cause service with no multi-hop cascades. The CLAUDE.md design specifies
   a 6-scenario evaluation suite including a 4-hop cascading scenario (S-06). The current results
   only validate the system on the simplest fault class. Root-cause accuracy on multi-hop scenarios
   where the alerting service is 3–4 DEPENDS_ON hops from the actual root may diverge significantly
   between systems — this is where graph topology is expected to show its largest advantage.

2. **n=3 is a small evaluation sample.** Three scenarios do not provide statistical confidence.
   The consistency of the blast-radius gap (F1 difference of 0.261–0.333 across all three scenarios)
   suggests the result is systematic rather than coincidental, but p-values cannot be computed.
   A larger evaluation with 10–20 injected scenarios across different fault types and cascade depths
   would substantially strengthen the paper's empirical claims.

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
