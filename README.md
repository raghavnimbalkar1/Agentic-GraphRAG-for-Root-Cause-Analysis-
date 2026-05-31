# Agentic GraphRAG for Autonomous Root Cause Analysis in Cloud-Native Microservices

![AIOps](https://img.shields.io/badge/Domain-AIOps-blueviolet?style=flat-square)
![LangGraph](https://img.shields.io/badge/Framework-LangGraph-orange?style=flat-square)
![Neo4j](https://img.shields.io/badge/Database-Neo4j-008CC1?style=flat-square)
![Docker](https://img.shields.io/badge/Security-Docker_Sandbox-2496ED?style=flat-square)
![Status](https://img.shields.io/badge/Status-In_Development-yellow?style=flat-square)

---

## Overview

Allowing Large Language Models to execute automation scripts directly on live production infrastructure introduces serious security vulnerabilities and hallucination risks. **Agentic GraphRAG** addresses this problem by coupling the structural precision of graph databases with the dynamic reasoning capabilities of multi-agent systems — enabling secure, autonomous self-healing for cloud-native applications without exposing production hosts to unverified code execution.

---

## Architectural Highlights

| Component | Role |
|---|---|
| **Semantic Skill Graph (Neo4j)** | Replaces flat vector storage by mapping dynamic infrastructure topologies directly to operational remediation scripts |
| **Agentic ReAct Loop (LangGraph)** | Multi-agent framework that traverses the system graph to trace cascading failures, bypassing the hallucinations inherent in text-only RAG |
| **Secure Tool Execution (OpenClaw)** | Decouples natural language reasoning from raw system execution, translating intent into verified tool interactions |
| **Isolated Remediation Sandbox (Docker)** | Runs all generated remediation code inside temporary, resource-constrained containers rather than natively on production hosts |

---

## System Architecture

The system operates across four primary modules:

```
[Target Environment]
        |
        | (Telemetry Stream)
        v
[LangGraph Agentic Brain] <-----> [Neo4j Skill Graph]
        |                               |
        | (Secure Tool Call)            | (GraphRAG Query)
        v                               |
[Docker Sandbox Engine] <--------------+
        |
        | (State Feedback)
        v
[Target Environment]
```

**Module Descriptions:**

1. **Target Cloud Environment** — A dynamic microservices benchmark (Google Online Boutique) continuously streaming stdout/stderr telemetry into the pipeline.

2. **Neo4j Semantic Skill Graph** — A highly indexed graph database mapping service dependency relationships and system-specific Standard Operating Procedures (SOPs) to executable remediation scripts.

3. **LangGraph Agentic Brain** — A stateful multi-agent model executing continuous Reason + Act (ReAct) loops, dynamically deciding which graph nodes and tools to invoke based on observed system state.

4. **Docker Execution Sandbox** — A hardened execution layer that spawns resource-limited, network-isolated micro-containers on demand to safely test and run remediation scripts before any production-side application.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | LangGraph / LangChain Core |
| Knowledge Engine | Neo4j (Graph Database) + Cypher Query Engine |
| Reasoning Model | Qwen 2.5 Coder (local deployment via vLLM / Ollama) |
| Tool Interface Abstraction | OpenClaw |
| Execution Containment | Docker Engine API + Linux `cgroups` |
| Simulation & Fault Injection | Google Online Boutique + Chaos Mesh |

---

## Implementation Roadmap

### Phase 1 — Simulation Cluster and Telemetry *(Current)*

- [ ] Deploy the microservice benchmark architecture across localized containers
- [ ] Configure logging streams to route container stdout/stderr directly into Python data buffers
- [ ] Author baseline fault injection scripts for network latency and compute stress scenarios

### Phase 2 — Graph Representation and SOP Construction

- [ ] Stand up a local Neo4j instance with strict index constraints
- [ ] Build Python data mappings to sync container operational state into Neo4j graph nodes
- [ ] Compile a comprehensive schema of 15–20 individual executable remediation SOPs

### Phase 3 — LangGraph ReAct Logic Integration

- [ ] Implement the core LangGraph state dictionary schema to track system updates across agent turns
- [ ] Configure local inference endpoint pipelines optimized for code-centric LLMs
- [ ] Define Pydantic structural schemas to enforce valid tool payloads entering the model graph

### Phase 4 — Sandboxed Containment and Evaluation

- [ ] Interface with the Docker Engine SDK to spawn micro-sandbox containers on demand
- [ ] Enforce read-only root filesystems, strict memory ceilings, and isolated network bridges on all sandboxes
- [ ] Benchmark recovery rates and Mean Time to Resolution (MTTR) against traditional text-based RAG baselines

---

## Research Motivation

Standard Retrieval-Augmented Generation pipelines retrieve semantically similar text chunks and pass them to a language model. In AIOps contexts, this approach fails in two critical ways: retrieved text does not encode the *structural* relationships between services (which service depends on which, which script remediates which failure mode), and unconstrained code execution against live infrastructure is an unacceptable security surface.

This project proposes that a **graph-native knowledge representation** paired with **sandboxed execution** closes both gaps simultaneously — preserving the contextual reasoning of LLMs while eliminating direct infrastructure access.

---

## Project Status

This project is currently in active development under **Phase 1**. Contributions, issue reports, and architectural feedback are welcome.

---


