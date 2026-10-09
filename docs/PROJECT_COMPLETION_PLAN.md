# Project Completion Plan

Project: Agentic GraphRAG for Root Cause Analysis  
Review date: 2026-10-09  
Status: Proposed; implementation has not started  
Purpose: Finish a reproducible, defensible MTech research prototype and its submission deliverables.

## 1. Completion Target

The existing implementation provides most of the intended pipeline. The remaining work is to make its decisions, recovery claims, experiments, and setup instructions dependable.

The final supported workflow is:

```text
Real fault or resource anomaly in Online Boutique
  -> independent telemetry observation
  -> acknowledged incident with a stable ID
  -> graph-based root localization and potential blast radius
  -> graph-vetted SOP candidates
  -> constrained LLM selection
  -> controlled execution against an authorized simulation target
  -> fresh verification of the original fault and affected services
  -> bounded retry/fallback, or terminal escalation
  -> complete audit report and dashboard evidence
```

This remains an operational RCA system. Conversational features are outside the completion scope.

### Scope To Finish

- Online Boutique on local Docker Compose: 10 application services, Redis, and loadgenerator, totaling 12 containers.
- The current hand-authored infrastructure and skill graphs, with their consistency problems corrected.
- The 10 supported fault families, the persistent Redis OOM fallback variant, and an external pause test.
- Single-incident remediation, with serialized handling and safe behavior when duplicate or overlapping incidents arrive.
- The existing independent-root orchestration feature, tested as a limited robustness extension.
- Root localization, potential blast-radius analysis, bounded SOP selection, execution, verification, and reports.
- A usable Streamlit demonstration, corrected baseline comparisons, a PCI ablation, and reproducible evidence.
- Updated implementation documentation and the thesis, paper, slides, and poster required for submission.

### Future Work

Automatic dependency discovery from traces, Kubernetes deployment, production observability integrations, arbitrary generated scripts, general causal discovery, correlated multi-fault inference, multi-cluster operation, and live TrainTicket remediation are future work. The existing TrainTicket study remains a localization-only experiment on a supplied graph.

The earlier roadmap describes PostgreSQL/custom services, OpenClaw, and Docker-in-Docker. Those are historical design proposals; adopting them now is unnecessary for the current implementation unless the university explicitly requires them.

## 2. What Exists And What Is Proven

| Area | Existing work | Remaining completion gap |
| --- | --- | --- |
| Research framing | Roadmap, thesis reference, paper notes | Verify literature, narrow unsupported novelty/security claims, finalize research questions |
| Deployment | Neo4j Compose, Boutique Compose, executor image | Fresh setup reproducibility, dependency/image versions, Apple Silicon executor compatibility |
| Dual graph | Root traversal, candidate retrieval, fallback edges, health updates | Seed/registry drift, unsafe verification, fallback targeting, missing/stale health semantics |
| Simulation | Fault injector, collector, chaos runner | Independent observations, reliable delivery, actual readiness and fault verification |
| Agent | LangGraph pipeline and constrained candidate selection | Terminal escalation, per-attempt state, complete failure reports and bounded recovery |
| Execution | Curated scripts and resource-limited containers | Docker control boundary, cleanup, path/target validation, configured timeouts |
| Reports/UI | JSON audit records and seven dashboard tabs | Complete decision/evidence history, blast radius, accurate timing, incident correlation |
| Evaluation | 21-scenario benchmark, baselines, ablation, TrainTicket and chaos artifacts | Correct scoring, fair comparisons, raw run records, independent recovery confirmation |
| Submission | Supporting Markdown documents | Final thesis/paper/slides/poster status outside this repository is not yet known |

Evidence checked during this review:

- The existing unit suite passes: **50 tests, 3.86 seconds**. These tests largely use mocks and do not establish live system correctness.
- Local audit records contain **138 reports: 109 RESOLVED and 29 ESCALATED**. These are mixed development runs, not a controlled success-rate experiment.
- The saved chaos artifact reports 16 detected and 16 resolved incidents. Its outcomes depend on the existing verification and report-correlation logic.
- The saved expanded benchmark records Gemini 2.5 Flash Lite; the ablation records Claude Haiku 4.5. These must not be described as one uniform-model experiment.
- The working tree was clean before this planning document was created.
- Docker was unavailable at the configured local socket. No current live deployment, live remediation, image build, or paid model experiment was run for this plan.

## 3. Bug And Gap Register

P1 items block trustworthy execution, recovery, or central research claims. P2 items block reproducibility, usability, or completion of the advertised feature. Priorities describe project completion risk, not a claim of a remotely exploitable production vulnerability.

### B01: Escalation Can Continue Into Execution [P1]

Evidence: `agent/graph.py:70` and `agent/nodes/evaluator.py:315`.

The evaluator can construct an ESCALATED report while retaining `current_skill`. The router then selects `retrieve` because it does not treat escalation as terminal. An isolated reproduction returned `retrieve` for an explicitly escalated state. The previous review also exercised the complete mocked loop and observed a subsequent execution.

Required outcome: an explicit escalation or terminal result ends the incident; later candidates cannot overwrite it. Test the compiled workflow, not just individual routing functions.

### B02: Missing Or Bad Health Evidence Can Count As Recovery [P1]

Evidence: `agent/nodes/evaluator.py:117`, `:147`, `:158`, `:173`, `:193`, and `:218`; `simulation/telemetry_collector.py:112` and `:195`.

Examples: STALE_DATA checks Redis responsiveness/capacity instead of the stale-key anomaly; a missing disk observation becomes zero bytes; missing CPU or memory data can pass; a fast HTTP 500 passes the latency check; some unknown-probe failures trust script exit status. Mocked checks reproduced successful verification for a missing disk container and HTTP 500.

Required outcome: distinguish healthy, unhealthy, and unknown observations. Unknown cannot resolve an incident or overwrite a known fault as healthy. Verify the incident condition and application readiness, with bounded settling time.

### B03: Fallback State Can Verify Or Operate On The Wrong Thing [P1]

Evidence: `agent/nodes/evaluator.py:262`, `:307`, and `:401`; `graph/cypher/service_topology.cypher:370`; `agent/nodes/executor.py:65`.

Nonzero script failures do not follow NEXT_IF_FAIL. The evaluator can reuse an earlier execution when the current decision did not execute. A fallback replaces `current_trigger`, so the original incident condition is not preserved independently. Two seed fallback edges cross services, while execution continues targeting the original root: Cart restart to Redis flush, and Checkout restart to Cart restart.

Required outcome: bind each result to its own attempt; preserve the diagnosed incident condition; validate fallback applicability to the authorized target. Initially support same-target fallback only, retaining the Redis restart-to-flush case. Reject unsupported cross-service edges rather than silently executing them on the old target. A later cross-service remediation feature would require explicit target transitions and separate verification.

### B04: A Fresh Graph Does Not Reproduce The Working Setup [P1]

Evidence: `graph/scripts/init_graph.py:42`, `graph/cypher/service_topology.cypher:243`, and `graph/schema_definitions.py`.

The current seed defines 12 Service nodes, 15 Skill nodes, 16 dependency edges, 19 applicability edges, and 3 fallback edges. Validation still expects 9 skills, 12 applicability edges, and 4 fallback edges. Six restart skills are LOW although their scripts require the Docker control access currently granted only to MEDIUM/HIGH. The Python registry describes an older catalog. Reseeding can overwrite manual repairs in a live database.

Required outcome: one authoritative catalog and matching validation; explicit execution capabilities; migration of known obsolete edges; no dependence on manual Neo4j edits. Counts may change when invalid fallback edges are removed, so derive expected counts from the final catalog.

### B05: Verification And Comparison Tools Mutate Live Health [P1]

Evidence: `graph/scripts/init_graph.py:156` and `:202`; `dashboard/components/comparison.py:72`; `eval/benchmark_full.py:138` and `:163`.

`--verify-only` writes an OOM state and restores HEALTHY rather than preserving the previous state. The dashboard comparison similarly writes a known root into the live graph. The benchmark resets live Service statuses and pauses the collector without guaranteed restoration on failure. Unscoped relationship counts also include TrainTicket edges.

Required outcome: read-only verification really performs no writes; experiments use an isolated graph/database; simulated states cannot alter live incidents. Scope counts and cleanup to the intended dataset.

### B06: Failed Alert Delivery Can Lose An Incident [P1]

Evidence: `simulation/telemetry_collector.py:352` and `:443`; `agent/main.py:113`.

The collector marks an episode alerted immediately after starting a thread. HTTP errors are not checked and failures do not re-arm delivery. Retrying without API deduplication would risk duplicate remediations. Simultaneous requests currently have no explicit coordination over shared simulation state.

Required outcome: stable episode IDs, acknowledgment-aware retry, bounded backoff, idempotent handling, and serialized remediation for the local prototype. Delivery failure and an acknowledged ESCALATED incident are different states.

### B07: Input Validation Does Not Match The Advertised Workflow [P1]

Evidence: `core/schemas.py:56`, `agent/nodes/executor.py:30`, and `agent/nodes/evaluator.py:444`.

The API restricts alert symptoms to ServiceStatus, rejecting upstream symptoms such as HTTP_503 and HIGH_ERROR_RATE that appear in the controlled benchmark, while accepting HEALTHY as an incident error. Script paths can resolve outside `sops/`; user-supplied incident IDs can produce audit paths outside the audit directory. Pure path/schema checks reproduced these cases without creating files.

Required outcome: separate alert symptoms from measured service state; validate service identity; reject healthy-as-fault input; unsupported symptoms produce an explicit non-executing outcome. Resolve scripts under the approved directory and use safe internal audit filenames.

### B08: Execution Isolation Is Overstated [P1]

Evidence: `agent/tools/sandbox_tools.py:123`, `:130`, and `:147`; `simulation/docker-compose.yml:23`.

MEDIUM/HIGH scripts receive an unrestricted Docker socket. Container resource/capability restrictions do not restrict operations requested through that daemon. The simulation bridge also has no configured internet isolation. Accordingly, the current system cannot support the roadmap's blanket no-host-exposure claim. Docker's own documentation describes daemon access as privileged control: [Docker Engine security](https://docs.docker.com/engine/security/).

Required outcome: document the trusted control plane and replace direct daemon access from SOP sandboxes with narrowly authorized operations, as specified in work package W5. Even then, avoid absolute security claims beyond the controls tested.

### B09: Executor Configuration And Cleanup Are Incomplete [P2]

Evidence: `agent/nodes/executor.py:77`, `agent/tools/sandbox_tools.py:67`, `:75`, and `:176`; `sop-executor/Dockerfile:29`.

Per-skill timeout is dropped in favor of 30 seconds; configured Docker host/image settings are not used consistently; some failures can bypass cleanup. The executor image downloads an x86_64 Docker client into an otherwise architecture-dependent base image. The latter is a portability risk requiring a real Apple Silicon build/run test, not a confirmed current runtime failure.

Required outcome: consistent configuration, bounded operations, cleanup in all failure paths, supported image architecture, and accurate structured error results.

### B10: The Injector Can Supply The Answer To The Live Graph [P1]

Evidence: `simulation/fault_injector.py:75`, `:452`, `:483`, `:512`, `:546`, and `:572`.

Several injectors and reset functions write the expected root status into Neo4j. Automatic alerts are still issued by the collector, but graph state is not exclusively an independent observation. Some injection commands also ignore unsuccessful command results.

Required outcome: injectors change the simulation and record experiment ground truth outside the operational graph. Only observed probes update live service health. Failed injections are recorded as invalid trials, not successful faults.

### B11: Benchmark Scoring Can Produce Misleading Results [P1]

Evidence: `eval/benchmark_full.py:168`, `:211`, `:223`, and `:253`; `eval/benchmark.py`.

Blast F1 compares the known root's blast set with itself instead of using the predicted root. Timed runs send manual alerts while the collector is paused, so the reported time excludes automatic detection. Failed/unresolved trials are dropped from timing samples; empty samples become zero; aggregation mixes per-fault averages with scenario weighting. Legacy evaluation also relies on expected or status-derived answers.

Required outcome: predictions independent of ground truth, explicit timing boundaries, every trial retained, missing values represented as missing, and aggregates computed from identified raw repetitions. Do not reuse old headline numbers as validated final results.

### B12: The Existing Comparison Does Not Isolate The Research Contribution [P1]

Evidence: `eval/benchmark_full.py:12`, `:45`, and `:163`; recorded benchmark and ablation metadata.

Graph traversal receives the planted unhealthy node while the baselines receive alert text. Alert ambiguity changes with depth. Localization accuracy, representation benefit, LLM selection benefit, and closed-loop recovery are therefore not separately established. A single fixed-size skill library does not establish constant token usage as libraries grow.

Required outcome: explicit experimental treatments and evidence parity, a graph-only control, matched alert ambiguity, and a scoped PCI ablation. Claim only what each experiment measures.

### B13: Reports And UI Can Misattribute Evidence [P2]

Evidence: `core/schemas.py:113`, `agent/nodes/evaluator.py:369`, `simulation/chaos_daemon.py:189`, `dashboard/app.py:287`, and `:415`.

`skills_executed` includes visited/skipped skills; only the final candidate decision is retained. Reports lack the full potential blast set and per-service verification evidence. Dashboard/chaos/evaluation paths select the newest report rather than the matching incident. Some success headlines are hardcoded and connectivity chips overstate what they test.

Required outcome: a complete per-attempt audit trail, explicit blast/verification scope, stable incident correlation, and display values derived from versioned results.

### B14: Maintenance Paths And Dependency Recovery Have Drifted [P2]

Evidence: `graph/graph_client.py:69` and `:91`; `graph/graph_populator.py:16`; `graph/scripts/load_sops.py:18`; root `Dockerfile:17`; `scripts/setup_dev_env.sh:57`.

A failed first Neo4j connection can leave a non-null driver that bypasses later initialization; closing does not reset it. Old population/loading modules import removed models and packages. The root Dockerfile/setup script reference nonexistent modules. Dependencies have minimum bounds rather than a reproducible lock. Several documents describe obsolete architecture.

Required outcome: recoverable client lifecycle, one supported startup path, an explicit disposition for obsolete scaffolding, a tested dependency snapshot, and consistent documentation.

## 4. Implementation Work Packages

Each package should be a small reviewable change set, with its own meaningful checks. Start subsequent packages only after their prerequisite behavior is established. Preserve historical results and unrelated user changes.

### W0. Freeze Scope And Capture A Reproducible Baseline

Estimate: 0.5-1 focused day. Dependencies: none.

- Record the current commit, runtime/dependency versions, topology/catalog version, current architecture, and supported entry points without recording credentials.
- Preserve existing experiment artifacts as historical evidence; new experiments get new run IDs and schema versions.
- Write a requirements-to-evidence matrix covering each project objective and proposed research claim.
- Confirm submission deadline, university format, existing external thesis/paper/slides, and available model budget before scheduling the final experiment campaign.
- Define the distinction between a simulated resource anomaly, a demonstrated application failure, and a predicted cascade. The current Redis cap/large-keyspace/client-count and disk thresholds are controlled heuristics, not proof of arbitrary production OOM, stale content, exhausted pools, or full disks.

Exit gate: agreed completion scope and a reproducible baseline manifest. Existing artifacts remain intact.

### W1. Repair Graph Initialization And Data Contracts

Estimate: 1-2 focused days. Dependencies: W0. Bugs: B04, B05, B07, B14.

Primary files: `graph/cypher/service_topology.cypher`, `graph/schema_definitions.py`, `graph/scripts/init_graph.py`, `graph/graph_client.py`, `core/schemas.py`.

- Retain Cypher as the executable seed source. Stop maintaining an independently divergent Python skill catalog; retain reusable schema definitions and migrate any catalog consumers to the authoritative source.
- Align skill capability/risk metadata, service ports, paths, triggers, applicability, and supported fallback edges.
- Make normal reseeding idempotent and preserve observed status on existing services. Separate schema/catalog migrations from health observation.
- Validate names, script existence, enum values, unique identifiers, fallback target applicability, unsupported cycles, and duplicate edges. Check traversal bounds and deterministic tie handling; ambiguous roots must be reported as ambiguous or explicitly dispatched, not presented as certain causal proof.
- Remove known obsolete edges through a scoped migration. Do not wipe the database or unrelated TrainTicket nodes.
- Make verify-only read-only; run seeded traversal tests against an isolated fixture. Scope counts to Boutique labels.
- Fix driver initialization/close/reconnect behavior and distinguish nonexistent/missing-status nodes from healthy ones.
- Separate external alert symptom codes from internal health states and constrain identifiers used for files and targets.

Exit gate: initialize an empty test database, reinitialize it, and obtain the same valid catalog; health and unrelated data survive reseeding; verify-only performs zero writes; missing nodes and invalid inputs cannot be diagnosed as healthy.

### W2. Make Agent Termination And Attempts Correct

Estimate: 1-2 focused days. Dependencies: W1. Bugs: B01, B03, B07, B13.

Primary files: `agent/graph.py`, `agent/state.py`, `agent/nodes/ingest.py`, `retriever.py`, `reasoner.py`, `executor.py`, `evaluator.py`, `core/schemas.py`.

- Define explicit terminal outcomes and route them to one report-finalization path.
- Persist the original observed root condition separately from the current SOP's trigger. A fallback may address a different mechanism but must still clear the original incident.
- Represent each attempt's candidates, decision, authorized target, execution result, verification evidence, and timestamps together.
- Verify only the execution associated with the current attempt. A skip or escalation must not reuse the previous execution.
- Support validated NEXT_IF_FAIL transitions after a failed execution or failed recovery check; stop on an exhausted/visited chain. Preserve a clear policy for skip: visit once, then consider remaining valid candidates within the attempt budget.
- End malformed decisions, retrieval failures, unknown targets, unsupported symptoms, missing scripts, and exhausted budgets with an explicit non-executing or failed/escalated result.
- Preserve exact-name candidate selection and graph-sourced script metadata. Record rejected model choices separately from executed choices.
- Keep dependencies, potential blast radius, and actual execution history distinct in the report.

Exit gate: full mocked workflows prove escalation is terminal, invalid selections execute nothing, fallbacks target the right service, prior results cannot be reused, and every valid incident has exactly one terminal outcome within its limits.

### W3. Unify Detection And Recovery Evidence

Estimate: 2-3 focused days. Dependencies: W1-W2. Bugs: B02, B10, B13.

Primary files: `simulation/telemetry_collector.py`, `simulation/fault_injector.py`, `agent/nodes/evaluator.py`, `core/schemas.py`; add one small shared probe module if needed.

- Share probe parsing, thresholds, and result types between sensing and verification. A result includes health/unknown state, measured value, observation time, and failure detail.
- Treat a valid zero CPU sample as zero; missing CPU/memory/disk/Redis data is unknown. Do not confuse probe infrastructure failure with a diagnosed application fault.
- Check HTTP response status as well as elapsed time. For gRPC services, use a probe capable of running from the simulation network; do not assume a health-probe executable exists in distroless images.
- Verify Redis stale-key anomalies independently from memory capacity, with explicit simulation semantics and normal traffic tolerance.
- Introduce bounded stabilization checks and intentional detection/recovery hysteresis. Repeated unknown observations lead to an actionable monitoring/dependency error, not false recovery or indefinite retry.
- Compute potential blast radius from reverse dependency reachability. Keep the observed affected set separate; a dependency edge alone does not prove an outage.
- Require fresh root health plus readiness/fault evidence for the incident's affected services before reporting recovery. Unrelated failures must not silently disappear or force one incident to claim global recovery.
- Remove operational Neo4j writes from injection/reset paths. Record fault ground truth in the experiment harness and check that the physical injection succeeded.
- Verify that CPU throttling mitigates resource pressure without making the application unusable; restoration should survive the required settling/restart checks. Persistent Redis settings deserve an explicit recovery-versus-permanent-repair distinction.

Exit gate: every supported condition has passing, failing, and unknown probe tests; no deliberately failing probe is accepted as recovery; independently observed faults still trigger correctly when the injector has no graph access.

Readiness rationale: Compose dependency order alone does not establish application readiness. See [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).

### W4. Make Incident Delivery And Coordination Reliable

Estimate: 1-2 focused days. Dependencies: W2-W3. Bugs: B06, B13.

Primary files: `simulation/telemetry_collector.py`, `agent/main.py`, `agent/multi_root.py`, report storage helpers, and affected tests.

- Generate one incident ID per unhealthy episode before dispatch and keep it across retries.
- Track pending, in-flight, acknowledged, and terminal states explicitly. Check HTTP status and response identity; retry transport/temporary server failures with bounded backoff.
- Deduplicate repeated IDs at the agent, including an in-flight request and a retry after a completed audit report exists. A client timeout must not launch the same remediation again.
- Use a single remediation queue/lock for the local one-worker prototype; re-observe health before a delayed incident starts. Avoid a new message broker or distributed coordination system.
- Handle collector restart, agent restart, startup with an already-unhealthy service, graph synchronization failure, and a symptom change during an incident deliberately.
- Ensure only one dispatch path owns an episode when the collector and multi-root runner coexist.
- Expose collector freshness, queue/active incident, graph readiness, and provider configuration/error state. Do not make paid LLM calls on every dashboard refresh.
- Store audit reports atomically so readers cannot consume partial JSON.

Exit gate: transient API outage recovers without losing the incident; duplicate delivery produces at most one active remediation; overlapping incidents cannot execute conflicting SOPs; all consumers match the exact incident ID.

### W5. Enforce The Execution Boundary And Repeatable Setup

Estimate: 2-4 focused days. Dependencies: W1-W2; must finish before final live validation. Bugs: B07-B09, B14.

Primary files: `agent/tools/sandbox_tools.py`, `agent/nodes/executor.py`, affected `sops/` scripts, `sop-executor/Dockerfile`, configuration, Compose, setup instructions.

Recommended security design:

- Keep Docker daemon access in a trusted controller; SOP sandboxes do not receive the raw socket.
- Provide a small set of typed, authorized operations used by the existing SOPs: inspect/restart/unpause an approved service, reconnect it to the simulation network, apply bounded CPU settings, and perform the one fixed cleanup operation.
- Bind each operation to the current incident, selected skill, approved target container ID, and a short-lived per-attempt authorization. Check Compose ownership, not just a caller-supplied container name.
- Deny arbitrary container creation, image selection, shell commands, filesystem mounts, unrelated target access, and unrestricted Docker API forwarding. Fixed cleanup must have a fixed path/command and must not become a general exec endpoint.
- Keep direct Redis remediation inside the restricted sandbox/network, with approved host/port targets. Separate execution capability from operational risk: flushing data is consequential even though it needs no Docker socket.
- Verify the controller cannot be used by another simulation container or a stale attempt. The trusted controller remains privileged; this design does not justify an absolute guarantee against host compromise.

Supporting fixes:

- Resolve and validate script paths under the approved SOP directory, including symlinks. Validate script type, target, parameters, risk/capability, and timeout before execution.
- Apply configured image/daemon/network/timeouts consistently; enforce bounded logs and cleanup through failure/timeout paths.
- Remove unnecessary container capabilities and preserve read-only filesystem, temporary storage, memory/CPU/PID limits.
- Bind development-facing services to loopback where required. Establish and test the necessary sandbox network access, including any restricted controller path; do not claim egress isolation merely from a bridge name.
- Make the executor image architecture explicit and test it on the M2 host. Keep only the tools the final scripts need.
- Establish a reproducible Python dependency snapshot and version/digest record for images. Replace floating Redis usage in the release setup with a tested version.
- Repair the supported bootstrap path; retire or clearly mark obsolete Dockerfiles/loaders/setup scripts instead of rebuilding unused architectures.

Exit gate: existing SOPs still work through approved operations; a sandbox cannot request unrelated targets or general daemon actions; timeouts/errors leave no executor containers; fresh documented setup succeeds without manual graph edits.

Scope tradeoff: retaining raw-socket SOPs would be a documented trusted-script demo limitation, not completion of the original strong isolation objective. That reduced boundary would require an explicit scope decision; this plan budgets for the restricted controller approach.

### W6. Correct Evaluation Before Spending On Final Runs

Estimate: 2-3 focused days for harness changes. Dependencies: W2-W4; final runs also require W5. Bugs: B05, B10-B13.

Primary files: `eval/benchmark_full.py`, `eval/benchmark.py`, `eval/ablation.py`, baselines, scenario definitions, `simulation/chaos_daemon.py`, TrainTicket runner.

- Split controlled graph localization, live autonomous recovery, and SOP-selection/context ablation into separately labeled experiments.
- Put controlled graph states in an isolated dataset. Do not suspend or rewrite the live collector/graph for localization comparisons.
- Compute predicted blast radius from the predicted root. Establish expected potential-impact sets in reviewed fixtures before running predictions; separately measure observed impact where probes support it.
- Retain every trial with its ID, scenario, repetition, prediction, outcome, error, timing, token usage, and reset/verification result. Report invalid injections separately, without silently excluding them.
- Use exact incident IDs for manual API tests. For autonomous tests, correlate collector-generated episodes with target, observation time, run window, and episode identity; do not feed the expected root to the agent as a trigger.
- Compute detection latency, diagnosis/selection time, execution/verification time, and total recovery time separately. Baselines that only infer a cause have diagnosis latency, not MTTR.
- Record all failures/escalations and the resolution denominator. Successful-run timing must be explicitly conditional; timeouts remain unsuccessful/censored trials, not zero-duration successes.
- Aggregate raw repetitions. Identify whether the reported average is per trial, per scenario, or equally weighted by fault family; do not imply that a spread across different scenario means measures repeatability.
- Record commit, graph/catalog digest, model/provider and parameters, embedding model, dependency/image versions, hardware, seed, experiment mode, and schema version.
- Guarantee cleanup in finally paths, and stop the campaign when the simulation cannot be restored. Never accept cleanup resetting a graph label as proof that a service recovered.

Exit gate: fixture tests deliberately produce a wrong root, wrong blast, timeout, invalid injection, partial success, and unrelated audit report; the harness scores and correlates all of them correctly without Docker or paid LLM calls.

### W7. Complete Reports And The Demonstration UI

Estimate: 1-2 focused days. Dependencies: W2-W4 and the W6 result schema. Bugs: B05, B13.

Primary files: `core/schemas.py`, report generation, `dashboard/app.py`, `dashboard/components/rca_report.py`, `comparison.py`, `explainer.py`.

- Show the incident timeline, diagnosed condition, dependency chain, potential blast radius, observed affected services, selected/ignored candidates, actions actually executed, and verification evidence.
- Distinguish terminal escalation, failed infrastructure, queued work, externally recovered incidents, and verified remediation. Show partial/unknown evidence explicitly.
- Preserve backward compatibility when viewing historical reports; mark unavailable fields rather than inventing evidence.
- Drive benchmark values from the selected result artifact, show its date/model/commit, and label historical results. Remove hardcoded success rates.
- Move live comparison to isolated data and make every demo/report lookup incident-specific.
- Show genuine component readiness/freshness. A configured LLM provider is not proof of current API connectivity.
- Test existing tabs, history filtering, empty history, unavailable Neo4j, long explanations, errors, and desktop/narrow layouts. Keep the current interface rather than redesigning it.

Exit gate: an injected incident can be followed through to its own complete report; another incident cannot appear as its result; comparisons do not alter live health; all displayed claims come from identified evidence.

### W8. Run Release Validation And Final Experiments

Estimate: 2-3 focused days, plus external service/API delays. Dependencies: W1-W7.

- Start from a fresh isolated project deployment and test the documented installation sequence. Preserve the developer's existing data and unrelated containers.
- Verify all 12 containers' intended roles. Application services require real HTTP/gRPC/Redis readiness; loadgenerator requires process/traffic evidence, not an invented health endpoint.
- Run a healthy baseline observation period, each supported fault, persistent OOM fallback, external pause, negative-control cases, and the limited independent-root scenario.
- Confirm recovery independently from agent reports. Capture application behavior and relevant probe values before injection, during the fault, after remediation, and after stabilization.
- Check repeat execution/reset behavior and that no injected resource cap, detached network, background stress process, temporary file, or executor container remains.
- Run the corrected benchmark/ablation matrix in Section 5, preserving all outcomes and metadata.
- Rehearse the complete dashboard demonstration and a deterministic replay of recorded evidence for presentation backup.

Exit gate: the release validation matrix passes, remaining limitations are documented, and every final chart/number is traceable to the corrected raw runs. A model not outperforming a baseline is a research result to report, not a reason to alter scoring.

### W9. Finish The Submission Package

Estimate: 3-6 focused days if substantial drafts already exist; longer for a thesis written from scratch. Dependencies: W8 for final results; structural drafting can start earlier.

- Reconcile README, architecture/schema/sandbox documentation, engineering reference, thesis reference, demo script, and paper notes with the final implementation.
- Verify cited papers and the literature gap using original publications. Replace universal novelty statements with precise, supportable contribution claims.
- Write the final design, implementation, evaluation, discussion, limitations, and future-work sections. Distinguish deterministic graph localization from LLM-based SOP selection.
- Produce architecture, graph schema, state-machine, actual incident/fallback trace, timing, accuracy, blast-radius, and PCI figures from the implemented system and identified datasets.
- Finish the paper in the required template, the seminar slide deck, and poster if required. Use Online Boutique examples rather than the obsolete PostgreSQL walkthrough.
- Include a reproducibility appendix, experiment manifest, scripts, curated sanitized audit examples, and a clean-start/reset/demo guide.
- Prepare a final demo recording, backup evidence, and answers about graph assumptions, baseline fairness, missing telemetry, security scope, supported fault semantics, and negative results.

Exit gate: submitted artifacts describe the same implementation and results as the release repository; references and figures are verified; the demo is reproducible from the guide.

## 5. Final Experiment Design

### Research Questions

| Question | Experiment | What it can support |
| --- | --- | --- |
| Does dependency knowledge improve localization under ambiguous alerts? | Graph retrieval and baselines on matched scenarios and observed health evidence; vary topology access explicitly | Benefit under stated graph/observation assumptions |
| Does the LLM improve SOP choice beyond deterministic graph retrieval? | Same candidates and evidence; compare LLM choice with a fixed risk-ranked policy, especially the multi-candidate CPU case | Incremental selection benefit, cost, and failure behavior |
| Does PCI reduce context compared with a full SOP library? | Same model/alert/SOP library, filtered versus full context; add irrelevant SOPs while keeping valid choices fixed | Effect of filtering on tokens, choice accuracy, and invalid choices |
| Does the complete loop recover faults autonomously? | Real injections, active collector, no manual alerts, independent recovery checks | Detection coverage, verified resolution rate, timing, and fallback behavior |
| Does traversal work on a deeper supplied topology? | Isolated TrainTicket localization with provenance and assumptions | Graph traversal generality, not live TrainTicket remediation |

Evidence fairness rules:

- Give competing systems the same observed alerts and health evidence. State explicitly which knowledge/retrieval representation is the treatment.
- Preserve alert-only historical baselines only as a separately labeled information-limited comparison.
- To claim a graph representation advantage beyond access to topology, include a baseline with the same topology serialized as text. If omitted, limit the claim to the implemented systems and information access.
- Separate depth and alert specificity: reuse matched symptom templates or cross the two factors. Do not equate increasingly vague wording with the isolated effect of extra graph hops.
- Include a graph-only control. Q1 already localizes deterministically; equal localization accuracy without an LLM is expected and should be reported.
- Use at least one no-fault case, missing/stale observation case, unreachable root, and ambiguous/multiple-unhealthy case. Restrict strong conclusions to validated scenarios.

### Proposed Run Budget

| Study | Minimum release evidence | Optional strengthening |
| --- | --- | --- |
| Supported fault coverage | 10 fault families x 3 independently reset runs = 30 trials | 5 repetitions per family |
| Fallback/external intervention | Persistent OOM and external pause x 3 runs each | Restart-persistence and relapsing-fault checks |
| Healthy behavior | 30-minute observed healthy period, report every false alert | Repeat with a second traffic level |
| Unattended operation | 30 scheduled/randomized single-fault incidents, include all supported families | A second seeded run and longer soak |
| Localization | Reviewed replacement for the 21 scenarios; 3 repetitions for each stochastic system | 5 repetitions and matched depth/ambiguity blocks |
| PCI/selection ablation | Same provider/model across paired cases, including multi-candidate and invalid-choice cases | Several irrelevant-library sizes and 5 repetitions |
| Independent roots | Two verified independent faults; demonstrate sequential handling and correct reports | More combinations; no general correlated-RCA claim |
| TrainTicket | Reproduce the existing seven depth cases in isolation with model/topology provenance | Additional ambiguous and multiple-unhealthy fixtures |

At 21 scenarios, three repetitions and two LLM baselines require 126 baseline calls before any additional controls, ablations, or live agent calls. Fix the protocol and calculate the complete call budget before running it. Small repetition counts support descriptive results; avoid exaggerated significance claims.

### Metric Definitions

- Root accuracy: correct predicted root / valid localization trials; report how ambiguity and no-root cases are scored.
- Potential blast F1: predicted potential affected set versus the reviewed topology fixture. Observed-impact F1 is a separate metric requiring independent service observations.
- Detection rate: faults independently confirmed to have occurred that receive a matching collector-generated incident within the observation window.
- Verified resolution rate: matching incidents independently confirmed recovered within the deadline / confirmed injected faults. Report escalations, misses, invalid injections, and infrastructure failures separately.
- Detection latency: first matching detected observation minus recorded injection onset; also record injection completion where an injection takes time.
- Agent handling time: verified outcome minus agent receipt. Full recovery time: verified outcome minus fault onset. Use monotonic clocks for local durations and UTC timestamps for audit correlation.
- Token/cost accounting: include selection retries and fallback calls; record input/output usage where available. Missing usage is not zero.
- Invalid-choice rate: model attempts to select a non-candidate tool / model decisions. Executed non-candidate rate must remain zero; that does not establish zero hallucination in explanations.
- False recovery: any RESOLVED outcome contradicted by independent fault/readiness checks. Release target: zero in the validation matrix.

## 6. Optimization Plan

Optimize only after the behavior is correct, and preserve a before/after measurement under the same traffic and fault schedule.

| Area | Proposed change | Measurement and constraint |
| --- | --- | --- |
| Collector cost | Batch container/disk inspection, reuse HTTP clients, sample expensive stats only where needed; use modest bounded concurrency if justified | Poll duration, observation age, CPU/RAM; retain fresh timestamps even when status is unchanged |
| Graph queries | Reuse the healthy driver, use unique/indexed names, deterministic ordering, bounded traversal, and batched status updates | Query count and latency; avoid querying all services repeatedly when one root is needed |
| Verification | Reuse shared probes with deadlines and settling checks | False recovery must remain zero; do not obtain faster timings by weakening checks |
| LLM context | Relevant candidate descriptions plus compact recent evidence and bounded response size | Tokens and valid-choice rate; do not silently discard valid candidates to manufacture savings |
| Baseline retrieval | Cache embeddings/index by catalog and embedding-model version | Index-build time excluded or reported separately from query latency; invalidate on catalog changes |
| Sandbox | Reuse a tested image, bound logs, clean up reliably | Launch overhead, execution time, resource use; do not loosen isolation for speed |
| Dashboard | Cache stable topology/catalog/result data, refresh live observations separately | Page refresh time and backend calls; never cache live health long enough to mask faults |

Keep one model/provider for the final comparison unless provider differences are themselves an experiment. Prefer a modest local workload compatible with the M2/8 GB environment. No new graph database, orchestration platform, tracing stack, or LLM framework is needed for these optimizations.

## 7. Verification Matrix

| Layer | Required cases |
| --- | --- |
| Graph/schema | Fresh seed, idempotent reseed, old-edge migration, TrainTicket isolation, absent service, null/stale status, valid/invalid fallback target, bounded/tied traversal |
| Complete agent workflow | Execute, skip, explicit escalation, malformed response, off-candidate selection, model unavailable, retrieval error, no skill, nonzero SOP, timeout, fallback, max attempts |
| Health | Each fault present/cleared/unknown; stale cache still present; HTTP 500; missing stats; valid zero CPU; container running but application unready; settling/relapse |
| Delivery | Agent unavailable then restored, HTTP 500/422 handling, timeout after completion, repeated ID, concurrent alerts, collector restart, overlapping multi-root dispatch |
| Execution | Approved script and target, path traversal/symlink rejection, unauthorized operation/target, expired authorization, missing image/daemon, timeout, log failure, guaranteed cleanup |
| Evaluation | Wrong prediction reduces score, failures retained, absent values stay absent, no ground-truth leakage, exact report correlation, interruption cleanup, reproducible raw aggregation |
| UI/report | Empty/historical/new reports, skipped versus executed skills, failure/unknown states, complete evidence, no live-state mutation from comparison, correct selected incident |
| Live release | Clean start, all supported faults, autonomous sensing, genuine fallback, external pause, limited independent roots, healthy soak, reset, restart, complete demo |

Unit/contract tests remain fast and service-free. Neo4j integration tests use an isolated fixture. Docker and model-dependent tests are explicitly separated so ordinary CI cannot inject faults into a developer's running stack or incur API charges.

## 8. Sequence, Effort, And Dependencies

```text
W0 baseline -> W1 graph/contracts -> W2 agent behavior -> W3 health -> W4 delivery
                                      |                              |
                                      +-> W5 execution/setup --------+
                                                                     |
                                               W6 evaluation harness + W7 reports/UI
                                                                     |
                                                          W8 live validation/results
                                                                     |
                                                          W9 submission package
```

W5 can overlap independent parts of W3-W4. W6 harness fixtures can be drafted before live readiness; final runs must wait for corrected verification and execution. W9 structure/literature work can start early; results sections must wait for W8.

Estimated engineering and validation effort is **roughly 11-20 focused working days**, followed by **3-6 days of submission work if substantial drafts already exist**. This is a planning range, not a delivery promise; fresh-machine issues, controller implementation, model access, and the state of the thesis can extend it. The submission deadline and external deliverables are still unconfirmed.

Immediate proposed implementation batch after plan acceptance: W1 graph/data-contract corrections and the B01 terminal-escalation regression/fix. Then complete W2 before live fault testing or final benchmarking. This provides an early reviewable improvement without mixing evaluation/UI changes into the initial fixes.

## 9. Definition Of Done

- [ ] A clean supported environment starts using the documented steps without manual Neo4j corrections.
- [ ] The supported graph/catalog has one source of truth and passes read-only validation.
- [ ] Explicit escalation, invalid selection, and missing evidence cannot lead to an unintended execution or false RESOLVED outcome.
- [ ] Every supported fault has independent detection, correct targeting, fresh recovery evidence, and repeatable reset behavior.
- [ ] Duplicate/failed delivery is handled; reports and UI correlate to the correct incident.
- [ ] SOP execution enforces the documented control boundary, validates paths/targets, and cleans up after failures.
- [ ] Reports include the dependency path, potential blast radius, observed impact, complete attempts, verification, and accurately defined timing.
- [ ] Corrected experiments retain failures, disclose assumptions, and produce reproducible figures from raw runs.
- [ ] Security, autonomy, accuracy, timing, model, and PCI claims match the implementation and evidence.
- [ ] Documentation, thesis/paper, slides, poster if required, demo guide, and reproducibility appendix are consistent and complete.

Finishing means these checks pass and limitations are explicit. It does not mean achieving predetermined accuracy/latency numbers or adding every future-work feature from the original roadmap.
