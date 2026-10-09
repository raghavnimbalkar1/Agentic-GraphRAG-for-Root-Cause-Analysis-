# Agentic GraphRAG For Root Cause Analysis

MTech Final Year Project, Raghav Nimbalkar, MIT-WPU Pune. Guide: Dr. Bhavana Tiple.

An AIOps prototype for **failure -> investigation -> graph diagnosis -> potential
blast radius -> approved remediation -> fresh verification -> RCA report**.
This is not a chatbot. The current correctness pass is implemented and tested
offline; fresh live experiments and final submission remain pending.

[![tests](https://github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-/actions/workflows/tests.yml/badge.svg)](https://github.com/raghavnimbalkar1/Agentic-GraphRAG-for-Root-Cause-Analysis-/actions/workflows/tests.yml)

## Architecture

Online Boutique -> shared telemetry probes -> Neo4j dual graph -> LangGraph agent
-> non-root SOP container / restricted controller -> fresh health checks -> atomic
incident report -> Streamlit. The LLM chooses from graph-vetted SOP candidates;
it does not invent scripts. Diagnosis is dependency traversal, not causal discovery.

- `simulation/`: 12-container Boutique stack, physical fault injection, collector,
  stable alert delivery and collector-driven chaos campaign.
- `graph/`: authoritative Cypher catalog and typed Neo4j queries.
- `agent/`: bounded workflow, serialized/idempotent API and execution boundary.
- `core/`: settings, data contracts, shared observations and audit storage.
- `sops/`, `sop-executor/`: eight curated repair scripts, controller client and image.
- `dashboard/`: live health, incident evidence, isolated comparisons and results.
- `eval/`: isolated fixtures, failure-aware localization scoring and paired ablation.
- `tests/`: offline regression suite; live graph checks are explicitly opt-in.

See [current architecture](docs/CURRENT_ARCHITECTURE.md), [completion progress](docs/COMPLETION_PROGRESS.md)
and [research evidence matrix](docs/RESEARCH_EVIDENCE_MATRIX.md). Earlier documents
are marked historical; [the completion plan](docs/PROJECT_COMPLETION_PLAN.md) records
the original review and remaining acceptance gates.

## Local Setup

Use Python 3.11+, a running Docker engine with Compose v2, and a configured LLM
provider. The recorded environment is Python 3.13 on macOS arm64. The constraints
snapshot captures installed versions, not a verified universal lockfile.

```bash
bash scripts/setup_dev_env.sh
source .venv/bin/activate
```

Configure `.env` from its template: Neo4j password, selected provider/model/key,
and `DOCKER_HOST`. The setup script does not overwrite an existing `.env` or start
containers unless `--start` is supplied. Check the active engine endpoint with
`docker context inspect --format '{{.Endpoints.docker.Host}}'`; configure that
endpoint explicitly, as the SDK does not select the CLI context automatically.
Never commit `.env` or credentials.

```bash
docker compose up -d --wait --wait-timeout 180 neo4j
python -m graph.scripts.init_graph
docker build -t sop-executor:latest sop-executor/
docker compose -f simulation/docker-compose.yml pull
docker compose -f simulation/docker-compose.yml up -d --wait --wait-timeout 300
```

Run these in separate terminals from the project root:

```bash
python -m agent.main
python -m simulation.telemetry_collector
streamlit run dashboard/app.py --server.address 127.0.0.1
```

The API must use **one worker**; keep it loopback-only because alerts can trigger
real simulation changes. The optional root Dockerfile is an API packaging image,
not a validated containerized remediation deployment; the supported controller
and SOP host paths run locally. Do not expose an unauthenticated API to a network.

```bash
python -m scripts.check_environment --live
python -m graph.scripts.init_graph --verify-only
curl -f http://localhost:8080/
```

The preflight verifies health, ownership, graph/collector readiness and that a
SOP-network request with an invalid token reaches the controller and is denied.
It performs no remediation or paid model call. Default controller binding is
loopback; host forwarding differs by runtime. A failed connectivity check is a
stop condition, not permission to mount a Docker socket. On Linux, explicitly
configure a simulation-reachable, firewall-restricted controller address.

An existing stack must be applied from the updated Compose definition to obtain
ownership labels. Never remove Neo4j volumes to upgrade the application.

Local surfaces: [storefront](http://localhost:8080), [Neo4j](http://localhost:7474),
[API](http://localhost:8888/docs), [dashboard](http://localhost:8501).

## Validate And Demonstrate

```bash
python -m pytest tests/ -q
python -m ruff check agent core graph simulation dashboard eval scripts tests --select F
python -m eval.research_benchmark --dry-run
python -m simulation.fault_injector list
```

After the live preflight passes, inject one fault and let the collector raise its
incident. Do not manually plant graph states or select the newest unrelated report.

```bash
python -m simulation.fault_injector inject config_drift
# Inspect that collector incident and its verification evidence in the dashboard.
python -m simulation.fault_injector reset config_drift
```

The injector never updates Neo4j health or sends alerts. Redis-cap/keyspace/client
and writable-layer thresholds are controlled resource-anomaly scenarios, not
proof of arbitrary production OOM, stale data, pool exhaustion or disk-full events.
`high_latency` using `tc` is unsupported on these images; use `dependency_timeout`.

## Evaluation

```bash
# Neo4j required; isolated graph-only control, no model calls or live state changes:
python -m eval.research_benchmark --reps 3
# Explicit model calls; the vector option may download an embedding model:
python -m eval.research_benchmark --with-llm --with-vector --reps 3
python -m eval.context_ablation --with-llm --reps 3
# Destructive simulation faults: run only after live preflight and baseline checks:
python -m simulation.chaos_daemon --duration 600 --min-incidents 15
```

New runs have versioned filenames and metadata. Localization latency, agent
handling duration and injection-to-independently-confirmed recovery are different
measurements. Failures remain in denominators; absent usage/timings are not zeros.
Historical artifacts under `eval/results/` are preserved but not revalidated.
Do not use their old 100%/16-of-16 headlines as final research evidence.

Current blockers and the remaining experiment/submission steps are tracked in
[completion progress](docs/COMPLETION_PROGRESS.md). No claim of universal RCA
accuracy, no host exposure, or complete project/submission readiness is made.
