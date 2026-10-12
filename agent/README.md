# Agent

Incident orchestration, constrained model decisions and remediation execution.

## Workflow

```text
ingest -> retrieve -> reason -> execute -> evaluate -> report
                       |                   |
                       +-> skip/escalate   +-> bounded retry or same-target fallback
```

Retrieval requires a complete, fresh health snapshot. Neo4j provides candidate
roots, dependency paths, potential blast radius and applicable SOPs. The model
chooses an exact candidate name, skips or escalates; trusted catalog data supplies
the script path and target. Escalation is terminal.

The evaluator re-probes the original condition and checks affected services in
two fresh healthy rounds. It records resolved, partial, failed or escalated
outcomes with all decisions, executions and verification evidence.

## Components

| File | Responsibility |
| --- | --- |
| `main.py` | Loopback FastAPI server and incident endpoints |
| `graph.py` | Compiled LangGraph state machine and conditional routing |
| `state.py` | Workflow state contract |
| `incident_manager.py` | SQLite incident ledger, replay protection and serialization |
| `nodes/ingest.py` | Alert validation and state initialization |
| `nodes/retriever.py` | Freshness checks and graph context |
| `nodes/reasoner.py` | Provider adapter, bounded inference and candidate validation |
| `nodes/executor.py` | Catalog-path resolution and attempt-bound execution |
| `nodes/evaluator.py` | Recovery checks, fallback routing and report generation |
| `tools/sandbox_tools.py` | Non-root, resource-limited SOP containers |
| `tools/control_gateway.py` | Fixed-operation authorization for owned simulation targets |
| `multi_root.py` | Limited manual handling of independent roots |

## API

```bash
python -m agent.main
```

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process liveness |
| `GET /status` | Neo4j connectivity and configured model; no inference probe |
| `POST /alert` | Submit a validated incident and run the workflow |
| `GET /incidents/{alert_id}` | Retrieve the matching report or in-progress state |

Use one API worker. Alerts can trigger real Docker operations, so the
unauthenticated API must remain loopback-only. Runtime ledgers and reports belong
in ignored `audit/`, not the source tree.

See [Architecture](../docs/ARCHITECTURE.md) for the execution trust boundary and
[Operations](../docs/OPERATIONS.md) for startup and controlled fault testing.
