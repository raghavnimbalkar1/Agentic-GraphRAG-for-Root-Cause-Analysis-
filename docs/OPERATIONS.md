# Operations

The supported deployment runs Python processes locally and uses Docker Compose
for Neo4j and Online Boutique. Run commands from the repository root. Automatic
alert delivery can incur model charges and change simulation containers.

## Environment

Install Python 3.11+ and Docker with Compose v2, then follow the
[README setup](../README.md#quick-start). The setup script creates a virtual
environment and copies `.env.example` only if `.env` is absent. It does not start
containers unless `--start` is supplied.

Configure `NEO4J_PASSWORD`, `LLM_PROVIDER`, `LLM_MODEL`, the corresponding API key
and `DOCKER_HOST` in `.env`. Supported provider adapters are Groq, Anthropic,
OpenAI, Gemini and Ollama; provider configuration is not a connectivity test.

The environment template selects Groq-hosted `openai/gpt-oss-120b` with
`GROQ_API_KEY`, a 30-second timeout and a 2,048-token completion budget. The
`openai/` model prefix does not require an OpenAI account: requests go only to
[Groq's compatible endpoint](https://console.groq.com/docs/openai). GPT-OSS uses
low reasoning effort and JSON object mode; exact SOP allowlist validation still
runs in the application. This is a starting configuration, not a measured best
model for RCA. No other provider or model is selected silently on failure.

Verify one real inference without connecting to Neo4j, injecting faults or
executing any SOP:

```bash
python -m scripts.check_llm
```

This sends synthetic incident context and consumes one inference request with
SDK retries disabled. A pass checks provider access, JSON output and selection
of the expected synthetic candidate, not autonomous recovery. Free-tier usage
is bounded by [Groq's rate limits](https://console.groq.com/docs/rate-limits);
pace later evaluation runs against the account's actual limits. Restart local
agent/dashboard processes after changing `.env` because settings are cached.

```bash
docker context inspect --format '{{.Endpoints.docker.Host}}'
```

Set `DOCKER_HOST` to the active engine endpoint. The Python SDK does not select
the Docker CLI context automatically. Keep credentials, incident ledgers and
runtime output out of Git. Never remove Neo4j volumes to upgrade the catalog.

The recorded environment is macOS arm64 with Python 3.13. Boutique v0.10.5
application images run under amd64 emulation; the executor was built natively.
`constraints-tested.txt` records tested versions, not a universal lockfile.

## Monitoring And Automatic Delivery

Run one API worker and one collector. Use observe-only mode while preparing the
environment:

```bash
python -m agent.main
```

```bash
python -m simulation.telemetry_collector --observe-only
```

```bash
streamlit run dashboard/app.py --server.address 127.0.0.1
```

Observe-only sensing updates graph health and records incident episodes without
submitting alerts. To enable automatic remediation, stop that collector, confirm
the approved model-call budget, then start exactly one normal collector:

```bash
python -m simulation.telemetry_collector
python -m scripts.check_environment --live
```

The live preflight requires delivery enabled and deliberately fails in
observe-only mode. It checks independent health, ownership, graph freshness,
API liveness and controller reachability using a denied-token request. It makes
no paid model call and performs no remediation.

Keep the API loopback-only: its alert endpoint is unauthenticated and can trigger
real simulation changes. The root Dockerfile packages the API but is not a
validated containerized remediation deployment. The supported controller and
SOP host paths run locally. On Linux, configure a simulation-reachable,
firewall-restricted controller address; a failed controller probe is a stop
condition, not permission to mount a Docker socket.

| Surface | Address |
| --- | --- |
| Storefront | [localhost:8080](http://localhost:8080) |
| Neo4j Browser | [localhost:7474](http://localhost:7474) |
| API documentation | [localhost:8888/docs](http://localhost:8888/docs) |
| Dashboard | [localhost:8501](http://localhost:8501) |

## Readiness

```bash
python -m graph.scripts.init_graph --verify-only
RCA_LIVE_GRAPH_TESTS=1 python -m pytest tests/test_live_graph.py -q
curl -f http://localhost:8080/
```

The graph check is read-only. Catalog migration preserves observed health; apply
the supported Compose definitions to obtain current ownership labels. Stop if
signals are missing, stale or unknown. API liveness and model configuration do
not establish successful inference. Two real Groq-driven dashboard fault paths
passed on October 13 after a recovery-verification repair; broad autonomous
acceptance remains pending. See [project status](STATUS.md).

The October 14 high-CPU trial verified a 0.5-CPU mitigation, but its baseline
cleanup timed out. Do not infer present readiness from a saved RESOLVED report.
If Docker reports manually paused, resume it in Docker Desktop, wait for fresh
independent observations and recheck the baseline before another demonstration.
The throttle does not remove the busy-loop; use the approved high-CPU reset to
restore adservice only after its incident is terminal. See the
[SOP catalog](SOP_CATALOG.md) for side effects and the
[development record](../eval/results/verified/20261014/README.md) for both outcomes.

## Preparation

1. Complete the README setup in an isolated deployment, keeping existing Neo4j
   data and unrelated containers intact. Confirm ownership labels and build the
   current executor image for the host architecture.
2. Start one API worker, the independent collector and Streamlit. Wait for all
   12 intended containers, a fresh complete graph snapshot and healthy probes.
3. Run `python -m scripts.check_environment --live`,
   `python -m graph.scripts.init_graph --verify-only` and the opt-in graph tests:
   `RCA_LIVE_GRAPH_TESTS=1 python -m pytest tests/test_live_graph.py -q`.
   Stop if controller connectivity, freshness or readiness fails.
4. Verify the chosen provider/model beforehand within the approved budget; the
   configured-provider status and read-only preflight do not make an inference call.
   Save a sanitized corrected report/recording as an explicitly labeled replay.

## Demonstration

1. Open Live RCA Console. Explain that the graph is a hand-authored service dependency
   model, with health written only by observations. Show the healthy baseline.
2. Start with `python -m simulation.fault_injector inject config_drift`.
   This changes Redis policy; it does not send an alert or tell the graph the answer.
3. Follow the exact new collector incident ID. Show the measured condition,
   dependency path, potential blast, candidate SOPs, decision and approved target.
4. Show the non-root SOP result and two-round verification evidence. When the
   frontend is affected, recovery also requires two successful private-session
   cart/checkout canaries against the local mock application. Health endpoints
   and a green exit code alone are not recovery. Failed application checks keep
   the result partial even if the root recovered. Display that outcome honestly.
5. Reset with `python -m simulation.fault_injector reset config_drift`, confirm
   independent healthy observations and that the collector re-arms. Before the
   next scenario, ensure the previous episode is cleared.
6. After rehearsal, demonstrate persistent Redis OOM:
   `python -m simulation.fault_injector inject redis_oom_persistent`.
   Redis restart preserves the command-line cap; fresh verification should cause
   the same-target flush fallback. Inspect both attempts and the original condition.
7. Always reset using `python -m simulation.fault_injector reset redis_oom_persistent`
   and verify the baseline. A live memory-cap repair may not survive another restart
   unless the launch configuration is reset; explain mitigation versus permanent repair.
   Restore the exact Compose definition after the special Redis recreation:
   `docker compose -f simulation/docker-compose.yml up -d --force-recreate redis-cart`.

For a failed injection or unknown observation, stop, record the failure and reset
the physical fault. Do not overwrite graph health to force a successful demonstration.
Do not delete the incident ledger to retry an uncertain operation.

## Research Views

The comparison tab uses isolated evaluation nodes, not live service statuses.
Model comparisons require explicit calls and may download embedding assets.
Explain graph-only control, common observed health inputs and different topology
access. Do not promise that one baseline always wins/loses at a certain depth.

Show corrected versioned results with model/commit/run provenance. If none exist,
show the methodology and say the experiment is pending. Historical runs must be
labeled historical; do not cite them as corrected release accuracy or recovery.

## Questions To Prepare

| Question | Defensible answer |
| --- | --- |
| Is this a chatbot? | No. It is a fault-to-verified-action workflow with constrained tools and audit evidence. |
| Does the LLM discover the root? | Q1 selects dependency-leaf candidates from observed health; the LLM chooses SOPs and supplies a labeled narrative. |
| Does reachability prove cascade impact? | No. It is potential blast; real application impact requires separate observations. |
| What happens with missing data or multiple roots? | Freshness/unknown/ambiguity guards stop automatic execution and request review. |
| Can a model invent a script? | It cannot supply executable paths; exact candidate selection and curated path/operation policies are enforced. |
| Is the host completely isolated? | No. SOPs are socketless and restricted, but the trusted host controller and injector have daemon authority. |
| Why not Kubernetes/OpenClaw? | Local Compose and the implemented controller support the research scope; older proposals are not claimed as delivered. |
| What are the current limits? | Hand-authored topology, heuristic signals, local single-worker coordination, limited fault library and pending corrected live experiments. |

After the live gates pass, update the preparation record with the tested commit,
engine/architecture, image IDs, model, chosen incident IDs and cleanup outcome.
