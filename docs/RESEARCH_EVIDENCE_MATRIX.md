# Research Evidence Matrix

Status: offline, real Neo4j and fixed-SOP executor evidence exists; paid model and
full autonomous acceptance remain pending. Updated 2026-10-11.

## Objectives And Claims

| Objective / claim | Current mechanism and evidence | Evidence still needed / claim boundary |
| --- | --- | --- |
| Monitor a microservice system | Shared probes/parser tests; fresh observe-only heartbeat; all 12 real probes validated | Long healthy soak and production log/trace integration remain out of evidence |
| Detect actual incidents | Physical injector, independent collector, two-observation episodes, persisted ID and retry tests | Per-fault onset/signal capture; missed/false alerts and detection latency; labels describe measured anomalies |
| Localize root using a graph | Real scoped Neo4j execution: 66 positive/negative trials; matched depth/ambiguity/unknown/stale controls; reviewed truth checked with NetworkX | Small constructed fixture, hand-authored topology; heuristic localization, not causal discovery |
| Analyze dependency chains | Deterministic paths and hop counts in reports | Graph fidelity and real propagation observations; hand-authored edges are assumptions |
| Analyze blast radius | Reverse reachability, predicted-set F1, wrong-prediction regression | Potential blast is not observed outage; observed-impact scoring needs independent request/service evidence |
| Retrieve remediation SOPs | Q2 trigger/applicability candidates from authoritative catalog; exact-name allowlist tests | Live catalog checks and applicability across all conditions; not free-form tool generation |
| Execute approved repairs | Native M2 build, denied-token host forwarding, real restricted restart/reconnect/unpause/fallback checks; offline authorization tests | Remaining SOP families and actual timeout/replay/cleanup-failure checks; controller retains daemon authority |
| Verify recovery | Original-condition re-probe, two fresh rounds, real private-session cart/checkout canaries, offline missing-sample/HTTP500/partial regressions | All-family autonomous validation and relapse checks; fixed-SOP canaries are not agent success-rate evidence |
| Generate RCA reports | Atomic ID-addressed schema-v2 reports, all attempts and late-failure history; historical version preserved | Sanitized live examples and visual review; agent rationale is not a proof of causation |
| Autonomous closed-loop operation | Collector-driven serialized harness, exact-ID correlation and cleanup | Corrected campaign covering all faults and independent recovery; no manual alerts, no planted live graph states |
| PCI lowers context costs | Budgeted paired same-model harness; actual zero-call prompt-character controls at 0/50/200 synthetic distractors | Paid paired token/decision/latency effects; characters are not tokens; no general constant-cost claim |
| LLM improves repair choice | Constrained model interface; risk-ranked policy control and real multiple-SOP case implemented | Paid comparison and repair-outcome quality; policy admissibility alone is not optimality |
| Graph improves localization | No-LLM, zero-shot, topology-text and vector controls implemented; matched evidence/depth protocol | Paid comparative results; graph versus text both receive identical edges, unlike topology-blind treatments |
| Generalizes to larger systems | TrainTicket supplied graph fixture | Provenance/reproduction and uncertainty; not a live 36-service remediation deployment |

## Evidence Sources

`tests/test_completion_regressions.py`, `test_agent_routing.py`,
`test_reasoner_invariant.py`, `test_health_and_delivery.py`,
`test_execution_boundary.py`, `test_fault_command_checks.py`,
`test_api_contract.py`, `test_chaos_evidence.py` and `test_evaluation_reliability.py` are offline regression
evidence. They do not establish live Docker/Neo4j/provider correctness.

`tests/test_live_graph.py` is opt-in: it checks the catalog and UUID-scoped Q1/blast
fixtures against real Neo4j without changing live service health. It is skipped
in ordinary CI; all three live cases have passed locally.

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
