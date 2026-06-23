# Agentic GraphRAG for Autonomous Root Cause Analysis in Cloud Native Microservices

![AIOps](https://img.shields.io/badge/Domain-AIOps-blueviolet?style=flat-square)
![LangGraph](https://img.shields.io/badge/Framework-LangGraph-orange?style=flat-square)
![Neo4j](https://img.shields.io/badge/Database-Neo4j-008CC1?style=flat-square)
![Docker](https://img.shields.io/badge/Security-Docker_Sandbox-2496ED?style=flat-square)
![Status](https://img.shields.io/badge/Status-Phase_4_Complete-green?style=flat-square)

## Overview

Allowing language models to execute automation scripts directly on live production infrastructure introduces serious security vulnerabilities and hallucination risk. Agentic GraphRAG addresses this by coupling the structural precision of a graph database with the reasoning capabilities of a stateful multi agent loop, enabling autonomous root cause analysis and remediation without exposing production hosts to unverified code execution.

The system receives a failure alert, traverses a dependency graph to identify the true root cause rather than the symptomatic surface error, retrieves a matching remediation procedure from a separate skill graph, and executes that procedure inside an isolated sandbox. The result is reported back as a structured root cause analysis document.

## Architectural Highlights

| Component | Role |
|---|---|
| Infrastructure Knowledge Graph (Neo4j) | Maps live microservice topology and dependency relationships, queried to trace cascading failures back to their root cause |
| Semantic Skill Graph (Neo4j) | Maps remediation scripts to the services and failure conditions they resolve, avoiding the need to load every possible remediation into context |
| LangGraph Agentic Brain | A stateful multi agent framework that traverses the dual graph, reasons over a single retrieved skill at a time, and routes execution based on live health checks |
| Isolated Remediation Sandbox (Docker) | Runs all remediation scripts inside temporary, resource constrained, network isolated containers rather than natively on the target environment |

## System Architecture

The system operates across four primary modules.

```
Target Environment (Online Boutique)
        |
        | Alert Payload (HTTP POST)
        v
LangGraph Agentic Brain <-----> Neo4j Dual Graph
        |                       (Infrastructure + Skill)
        | Sandboxed Execution
        v
Docker Sandbox Engine
        |
        | Health Verification
        v
RCA Report
```

**Module descriptions**

Target Cloud Environment. Google Online Boutique, a twelve service microservice benchmark, deployed via Docker Compose. A fault injection module breaks individual services on demand and writes the resulting state into the graph.

Neo4j Dual Graph. Two logically separate graphs stored in one database. The Infrastructure Knowledge Graph encodes which services depend on which others and their live health status. The Semantic Skill Graph encodes which remediation script resolves which failure condition on which service, including fallback procedures if the first attempt does not succeed.

LangGraph Agentic Brain. A stateful agent that ingests the alert, traverses the infrastructure graph to find the actual root cause, retrieves only the one relevant skill from the skill graph, asks a language model to decide whether to execute it, and loops based on verified health state rather than assumption.

Docker Execution Sandbox. A hardened execution layer that runs remediation scripts inside resource limited, network isolated, ephemeral containers, with no access to the host system.

## Technology Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | LangGraph, LangChain |
| Knowledge Engine | Neo4j, Cypher |
| Reasoning Model | Llama 3.1 8B via Ollama, remote inference over a local network or Tailscale, with GPT-4o and Claude 3.5 Sonnet evaluated for comparison |
| API Layer | FastAPI |
| Validation | Pydantic |
| Execution Containment | Docker Engine API |
| Simulation | Google Online Boutique v0.10.5 |
| Fault Injection | Docker SDK based chaos engineering |

## Implementation Roadmap

### Phase 0, Foundation. Complete

Python 3.11 virtual environment, pinned dependency manifest, typed configuration and schema layer, structured logging.

### Phase 1, Docker Environment. Complete

Neo4j and supporting infrastructure running under Docker Compose, verified connectivity and authentication.

### Phase 2, Neo4j Dual Graph. Complete

Infrastructure Knowledge Graph and Semantic Skill Graph populated against the real Online Boutique topology. Multi hop root cause traversal verified.

### Phase 3, Simulation Environment. Complete

Online Boutique deployed as twelve running containers. Fault injection module verified across multiple failure types, each updating the graph with ground truth state and dispatching an alert.

### Phase 4, LangGraph Agent Core. Complete

Full agent loop implemented and verified end to end. An injected fault is correctly traced through the dependency graph to its true root cause, the matching remediation skill is retrieved, a remotely hosted language model decides whether to execute it, and the loop continues or escalates based on live health verification. Results are written as a structured report.

### Phase 5, Sandboxed Execution. In progress

Replacing the current execution stub with real remediation scripts run inside isolated Docker containers, and expanding the skill graph with fallback procedures for multi step remediation chains.

### Phase 6, Integration and Chaos Testing

Full end to end verification across all fault scenarios with the real sandbox in place.

### Phase 7, Evaluation

Comparison against a zero shot language model baseline and a vector retrieval baseline, across root cause accuracy, blast radius estimation, and sensitivity to choice of language model.

## Research Motivation

Standard retrieval augmented generation retrieves semantically similar text and passes it to a language model. In an AIOps context this fails in two specific ways. Retrieved text does not encode the structural relationships between services, meaning the model cannot reliably reason about which dependency actually caused a downstream symptom. And unconstrained code execution against live infrastructure is an unacceptable security surface regardless of how well reasoned the generated script appears to be.

This project argues that a graph native knowledge representation, paired with sandboxed execution and a retrieval mechanism that only exposes one relevant remediation procedure at a time, closes both gaps. The graph topology itself constrains what the agent can act on, which removes a significant class of tool hallucination by construction rather than by prompting.

## Project Status

Phase 4 is complete and verified. The full pipeline from fault injection through graph traversal, language model reasoning, and report generation has been demonstrated end to end. Work is ongoing on Phase 5, replacing the execution stub with real sandboxed remediation.
