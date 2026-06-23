# Agentic GraphRAG for Autonomous Root Cause Analysis in Cloud-Native Microservices

![AIOps](https://img.shields.io/badge/Domain-AIOps-blueviolet?style=flat-square)
![LangGraph](https://img.shields.io/badge/Framework-LangGraph-orange?style=flat-square)
![Neo4j](https://img.shields.io/badge/Database-Neo4j-008CC1?style=flat-square)
![Docker](https://img.shields.io/badge/Security-Docker_Sandbox-2496ED?style=flat-square)
![Status](https://img.shields.io/badge/Status-Phase_7_Complete-green?style=flat-square)

**MTech Final Year Project · Raghav Nimbalkar · MIT-WPU Pune · Supervisor: Dr. Bhavana Tiple**

---

## What This Does

Receiving a failure alert, the system:

1. Traverses a Neo4j dependency graph to find the **real root cause** — not the alerting service, but the upstream node whose failure is cascading
2. Retrieves exactly one matching remediation script from a separate **Skill Graph** (no full SOP dump into the LLM context)
3. Has a remote LLM decide whether to execute it
4. Runs the script inside an **isolated Docker sandbox** — no host exposure
5. Verifies the fix via a live health check, then loops or reports

The result is a structured RCA report with MTTR, dependency chain, and skills executed.

---

## Evaluation Results (Phase 7)

Benchmarked on Google Online Boutique v0.10.5 · LLM: Gemini 2.5 Flash Lite · n=4 scenarios

| System | Root Cause Accuracy | Avg Blast-Radius F1 | Avg Tokens/Call |
|---|---|---|---|
| **Agentic GraphRAG (Ours)** | **100%** | **1.000** | ~451 |
| Zero-Shot LLM (B1) | 75% | 0.768 | 452 |
| Vector RAG (B2) | 75% | 0.742 | 649 |

**Key result:** Scenario S-04 (redis OOM, alert from frontend, root cause 3 hops deep, message names only frontend symptoms) — both baselines predicted the wrong service (one hop short). GraphRAG correctly identified `redis-cart` via graph traversal. This is the case that topology knowledge is specifically designed to solve.

Vector RAG uses 43% more tokens than zero-shot with no accuracy gain, confirming that SOP text retrieval adds remediation knowledge but not structural dependency knowledge.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│     Google Online Boutique v0.10.5 (12 services)    │
│     boutique-sim Docker network                     │
│     fault_injector.py — redis_oom, service_crash,   │
│                          network_partition          │
└──────────────────────┬──────────────────────────────┘
                       │  AlertPayload (HTTP POST :8888)
                       ▼
┌─────────────────────────────────────────────────────┐
│           LangGraph Agentic Brain                   │
│                                                     │
│  ingest → retriever → reasoner → executor           │
│              ↑                      ↓               │
│           evaluator ←───────────────┘               │
│              ↓                                      │
│           report (RCAReport JSON to audit/)         │
│                                                     │
│  LLM: Gemini 2.5 Flash Lite                         │
│  Progressive Context Injection:                     │
│    only ONE Skill node per LLM call                 │
└───────────┬──────────────────────┬──────────────────┘
            │ Cypher queries       │ Docker SDK
            ▼                      ▼
┌─────────────────────┐  ┌─────────────────────────────┐
│   Neo4j Dual Graph  │  │   Docker Execution Sandbox  │
│                     │  │                             │
│  Graph 1: Infra KG  │  │  sop-executor:latest        │
│  12 Service nodes   │  │  --cap-drop ALL             │
│  16 DEPENDS_ON      │  │  --read-only                │
│                     │  │  --memory=256m              │
│  Graph 2: Skill     │  │  --network boutique-sim     │
│  9 Skill nodes      │  │  per-SOP privilege scoping  │
│  12 APPLIES_TO      │  │  (LOW / MEDIUM by risk)     │
│  4 NEXT_IF_FAIL     │  │                             │
└─────────────────────┘  └─────────────────────────────┘
```

---

## Core Design Decisions

**Progressive Context Injection** — The Skill Graph acts as a filter. The LLM receives exactly one skill node's description per call, not a dump of all SOPs. This caps per-call token cost at ~450 tokens regardless of how large the graph grows, and eliminates a class of tool hallucination by construction: the agent can only act on what the graph explicitly surfaces.

**Per-SOP privilege scoping** — Sandbox privilege level is driven by the `risk_level` property on the Neo4j Skill node, not by heuristics. LOW-risk SOPs (cache flush) run as non-root with no Docker socket. MEDIUM-risk SOPs (container restarts) get the Docker socket and root access. All tiers enforce `--cap-drop ALL --read-only --memory=256m --pids-limit=50 --rm`.

**Graph-native retrieval over vector retrieval** — Dependency chains are structural, not semantic. A vector similarity search over SOP text can retrieve the right remediation script, but cannot tell the model *which services are downstream of the root cause*. The evaluation confirms this: vector RAG matches zero-shot on every metric despite 43% higher token cost.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Agent orchestration | LangGraph 0.2.x + LangChain 0.2.x |
| LLM (primary) | Gemini 2.5 Flash Lite via `langchain-google-genai` |
| LLM (local, RQ4) | Llama 3.1 8B via Ollama (remote GPU over Tailscale) |
| Graph database | Neo4j 5.18 Community + Cypher |
| Execution sandbox | Docker Engine API (DinD), custom `sop-executor` image |
| Alert ingestion | FastAPI :8888 |
| Simulation | Google Online Boutique v0.10.5 (Docker Compose) |
| Evaluation baselines | FAISS + `all-MiniLM-L6-v2` (Vector RAG), single Gemini call (Zero-Shot) |
| Schema validation | Pydantic v2 |

---

## Verified End-to-End Runs

| Alert ID | Fault | Root Found | MTTR | Status |
|---|---|---|---|---|
| INC-4AC84F16 | redis_oom on redis-cart | redis-cart | 6.3s | ✅ RESOLVED |
| INC-111D3B59 | service_crash on productcatalogservice | productcatalogservice | 5.22s | ✅ RESOLVED |
| INC-2EFDDAD1 | network_partition on paymentservice | paymentservice | 3.63s | ✅ RESOLVED |
| INC-38BFE69C | redis_oom (post-instrumentation) | redis-cart | 33.89s | ✅ RESOLVED — 451 tokens |

---

## Project Status

| Phase | Status |
|---|---|
| 0 — Foundation | ✅ Complete |
| 1 — Docker env + Neo4j | ✅ Complete |
| 2 — Neo4j dual-graph | ✅ Complete |
| 3 — Online Boutique simulation + fault injection | ✅ Complete |
| 4 — LangGraph ReAct agent | ✅ Complete |
| 5 — Docker sandbox + SOP scripts | ✅ Complete |
| 6 — Chaos integration + ground-truth scenarios | ✅ Complete (4 scenarios) |
| 7 — Evaluation: baselines + benchmark | ✅ Complete |
| 8 — Report + presentation | ⬜ In progress |

---

## Repository Structure

```
agent/          LangGraph agent (ingest → retriever → reasoner → executor → evaluator)
core/           Shared schemas, config, exceptions, logging
graph/          Neo4j client + Cypher scripts + graph populator
sops/           SOP shell/Python scripts mounted read-only into sandbox
sop-executor/   Dockerfile for the sandbox base image
simulation/     Online Boutique docker-compose + fault_injector.py
eval/           Baselines (zero_shot.py, vector_rag.py), benchmark.py, scenarios.json
docs/           Engineering reference + architecture decisions
```
