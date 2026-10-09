# Current Architecture

## Scope

An AIOps research prototype, not a chatbot: independently observed faults lead to
investigation, graph analysis, constrained remediation, verification and an audit.
The supported deployment is local Python plus two Docker Compose stacks.
OpenClaw, Docker-in-Docker, PostgreSQL/custom services and automatic dependency
discovery in the original roadmap are not the implemented architecture.

```text
Online Boutique (10 applications + Redis + loadgenerator)
  -> shared Docker / Redis / gRPC / HTTP health observations
  -> collector: synchronize observed Service statuses in Neo4j
  -> debounced incident episode, durable ID and acknowledged delivery
  -> single-worker FastAPI incident manager, SQLite replay protection
  -> LangGraph: ingest -> retrieve -> reason -> execute -> evaluate
  -> bounded same-target fallback / terminal escalation / failure / partial recovery
  -> atomic incident-addressed report -> Streamlit history and evidence
```

## Graphs And Diagnosis

`graph/cypher/service_topology.cypher` is the executable catalog: 12 `Service`
nodes, 15 `Skill` nodes, 16 `DEPENDS_ON`, 19 `APPLIES_TO` and one `NEXT_IF_FAIL`
edge (Redis restart to Redis flush). Reseeding preserves existing health.
Service and skill names have uniqueness constraints. Initialization validation
and its smoke test are read-only when `--verify-only` is supplied.

`A -DEPENDS_ON-> B` means A depends on B. Q1 walks from the alert toward dependencies
and returns unhealthy dependency leaves, deterministically ordered by longest
path then name. Multiple independent candidates are reported, not automatically
remediated as a certain single diagnosis. This is a topology-aware heuristic,
not learned causal inference. Runtime retrieval requires a complete, recent
supported-service snapshot; unknown or stale observations prevent execution.

Q2 obtains all applicable skills matching the root's observed condition. The LLM
can select one exact candidate name, skip or escalate. Script paths, target and
execution policy come from trusted state/catalog data, not model-generated code.
PCI means the **relevant candidate set**, not necessarily one skill, enters each
decision prompt. Prompt size also depends on message, descriptions and bounded
history; no universal constant-token claim is made.

Reverse dependency reachability is a **potential blast radius**, excluding the
root. It is not measured application impact and need not imply every reachable
dependent fails. The collector currently observes health and resource signals;
it is not a general distributed log/trace ingestion pipeline.

## Incident And Recovery Contracts

- Two matching observations create an episode. Healthy re-arms; unknown does not.
- Episode identity and collector creation events survive restart/recovery.
- Delivery checks HTTP status, matching ID and terminal acknowledgment. Errors
  retry the same ID; collisions/interrupted execution require review.
- One API worker serializes simulation changes. Multiple Uvicorn processes are
  unsupported: the in-process execution lock is not a distributed lock.
- Before each execution, the root's original condition is independently re-probed.
- Results are attempt-bound. Explicit escalation cannot re-enter execution.
- Recovery requires two fresh healthy observations of root, path and potential
  dependents within a settling window. Missing data cannot count as healthy.
- `RESOLVED` requires full verification; `PARTIAL` means root recovered but some
  affected services did not. Uncertain control operations/cleanup failures stop.
- Reports retain all decisions, actual executions, evidence, condition and scope.
- `handling_seconds` starts at agent receipt. Legacy `mttr_seconds` is an alias,
  **not** injection-to-recovery timing. The chaos campaign records that separately.

Resource labels retain earlier names for compatibility. A Redis memory cap is
not a demonstrated kernel OOM kill; expiring-key count is not proof of stale cart
content; client-count anomaly is not proof a production connection pool exhausted;
writable-layer size is not proof a disk became full. Cite the measured signal.
Application transaction failure needs separate request-level evidence.

## Execution Boundary

SOP containers run non-root, read-only, capability-dropped, with no-new-privileges,
memory/CPU/PID/time/output limits and cleanup in `finally`. No SOP gets the Docker
socket. Three curated Redis scripts have simulation-network access. Five curated
control wrappers request one fixed operation via a short-lived token bound to an
owned container ID. Ownership requires `rca.managed=online-boutique`.

The host-side Python controller is trusted and has Docker-daemon authority.
This is **not** proof of no host exposure, no lateral access, internet isolation
or protection from Docker/kernel vulnerabilities. The bridge is not internal;
Redis network scripts can reach simulation peers. HIGH policy is rejected.
Control connectivity must pass the denied-operation preflight on the actual
Docker host before any campaign. No unrestricted-socket fallback is provided.

## Evaluation Isolation

Controlled runs own UUID-scoped `EvalService` fixtures and clean up only that
scope. They never reset `Service` health or pause the collector. Graph traversal
is scored as a **no-LLM control**, alongside opt-in baselines that receive the
same alert and health snapshot; topology/runbook access is an explicit treatment.
All attempted predictions are retained, missing measurements are null, and
potential-blast F1 uses predicted reachability against hand-reviewed expectations.

PCI ablation pairs the same configured model and case, changing candidate
breadth only, alternating order and retaining failures and usage metadata. It
does not yet establish scaling across arbitrary library sizes or model families.
TrainTicket is a supplied graph localization fixture, not a deployed environment.

Historical result JSON files remain untouched. Their scoring, evidence parity,
correlation and recovery limitations are documented; they are not final evidence.
