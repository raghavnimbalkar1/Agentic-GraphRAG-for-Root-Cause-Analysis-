# Research Evidence Matrix

Status: implementation evidence exists; corrected live/research measurements pending.

## Objectives And Claims

| Objective / claim | Current mechanism and evidence | Evidence still needed / claim boundary |
| --- | --- | --- |
| Monitor a microservice system | Shared Docker/Redis/gRPC/HTTP probes; unknown/zero/error parser tests; fresh collector heartbeat | Real readiness, traffic/load evidence and healthy soak; not general production log/trace integration |
| Detect actual incidents | Physical injector, independent collector, two-observation episodes, persisted ID and retry tests | Per-fault onset/signal capture; missed/false alerts and detection latency; labels describe measured anomalies |
| Localize root using a graph | Bounded Q1 dependency traversal, scoped fixtures, reviewed truth checked with NetworkX; ambiguity/freshness guards | Actual Neo4j fixture execution and matched ambiguous/no-root tests; heuristic localization, not causal discovery |
| Analyze dependency chains | Deterministic paths and hop counts in reports | Graph fidelity and real propagation observations; hand-authored edges are assumptions |
| Analyze blast radius | Reverse reachability, predicted-set F1, wrong-prediction regression | Potential blast is not observed outage; observed-impact scoring needs independent request/service evidence |
| Retrieve remediation SOPs | Q2 trigger/applicability candidates from authoritative catalog; exact-name allowlist tests | Live catalog checks and applicability across all conditions; not free-form tool generation |
| Execute approved repairs | Eight curated scripts; socketless non-root container; bounded one-operation controller; grant/label/target/cleanup tests | Actual M2 build, host forwarding, timeout/replay/cleanup checks and reset stability; trusted controller retains daemon authority |
| Verify recovery | Original-condition re-probe, two fresh healthy rounds, missing-sample/HTTP500/partial regressions | Independent before/during/after measurements and relapse checks; script exit zero is insufficient |
| Generate RCA reports | Atomic ID-addressed schema-v2 reports, all attempts and late-failure history; historical version preserved | Sanitized live examples and visual review; agent rationale is not a proof of causation |
| Autonomous closed-loop operation | Collector-driven serialized harness, exact-ID correlation and cleanup | Corrected campaign covering all faults and independent recovery; no manual alerts, no planted live graph states |
| PCI lowers context costs | Paired same-model candidate/full-library harness with raw decisions/errors/usage | Measured paired effects, repeated trials and irrelevant-library-size sweep; no constant-token claim |
| LLM improves repair choice | Model selects from graph-vetted candidates; off-list choice is rejected | Compare against fixed risk-ranked policy on multi-candidate cases; Q1 localization itself does not require an LLM |
| Graph improves localization | No-LLM control and baselines receive common health snapshot | Topology-text control for representation claim; match alert specificity/depth; current treatment changes topology access |
| Generalizes to larger systems | TrainTicket supplied graph fixture | Provenance/reproduction and uncertainty; not a live 36-service remediation deployment |

## Evidence Sources

`tests/test_completion_regressions.py`, `test_agent_routing.py`,
`test_reasoner_invariant.py`, `test_health_and_delivery.py`,
`test_execution_boundary.py`, `test_fault_command_checks.py`,
`test_api_contract.py`, `test_chaos_evidence.py` and `test_evaluation_reliability.py` are offline regression
evidence. They do not establish live Docker/Neo4j/provider correctness.

`tests/test_live_graph.py` is opt-in: it checks the catalog and UUID-scoped Q1/blast
fixtures against real Neo4j without changing live service health. It is skipped
in ordinary CI and was not run live in this repair batch.

`docs/runtime_snapshot.json` and `constraints-tested.txt` record the installed
environment. They contain package versions, not credentials or a cross-platform
resolver guarantee. `scripts/check_environment.py --live` checks dependencies
and a deliberately denied controller request, not model inference/remediation.

New localization and ablation JSON files identify the commit, dirty state, model,
budgets, inputs and raw repeated outcomes. Fixture validation alone produces no
accuracy result. Historical `eval/results/` files and audit reports are preserved,
but their 100%/16-of-16 headlines are not validated final results.

## Reporting Rules

1. Disclose hand-authored topology, measured fault semantics, completeness/age of
   health observations, supported targets and ambiguous-root policy.
2. Separate deterministic graph localization, LLM candidate selection and actual
   closed-loop recovery. Do not attribute Q1 accuracy to the LLM.
3. Separate localization latency, agent handling time, detection-to-confirmation
   and injection-to-confirmation. Report absent timings/usage as null, not zero.
4. Preserve all attempted trials and identify invalid injections/setup failures,
   misses, escalations, partial recovery and uncertain operations separately.
5. Report invalid *model choices* separately from executed off-allowlist actions.
   Blocking invented tools does not establish zero hallucination in explanations.
6. Treat security as tested controls and a trust model, not absolute host/network
   isolation. The controller and injection process are trusted daemon clients.
7. Verify literature against primary publications before claiming novelty. Earlier
   gap tables and names are research notes, not verified proof of absence.
