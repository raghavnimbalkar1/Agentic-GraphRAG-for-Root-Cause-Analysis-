# Journal Study And Publication Plan

## Research Position

The defensible contribution is an explicit connection between infrastructure
dependencies, approved remediation procedures and independent recovery evidence.
The current system demonstrates that connection in a real containerized testbed.
It does not yet establish general RCA accuracy, production readiness or an LLM
advantage over simpler automation.

The two planned submissions are **journal manuscripts** with different research
questions. No acceptance, novelty priority or journal suitability is guaranteed.
Select a target after checking its current scope, accepted article types,
artifact policy, disclosure requirements and fees with the guide.

| Manuscript | Main contribution | Required work before submission |
| --- | --- | --- |
| Review paper | Taxonomy of dependency knowledge, procedural knowledge, agent authority and recovery verification; synthesis of open problems | Update the literature search, record the search date/queries/databases, verify every citation and make inclusion/exclusion reproducible. Retain the current narrative-review label unless a genuine systematic screening process is completed. |
| Implementation/results paper | Measured effects of topology/skill coupling, bounded decision-making and independently verified execution | Implement and freeze the intended system, run matched experiments, publish failure-aware raw results and uncertainty, explain the full path from observations to conclusions. |

Cross-reference the related manuscripts and disclose their relationship to the
editors. Do not recycle a results table as a new contribution or describe an
unpublished draft as an accepted paper. Check the chosen journals' current
overlap and concurrent-submission rules before submission.

## Show The Process Before The Headline

Structure the implementation paper in this order:

1. **Question and hypothesis.** State which architectural feature is expected to
   improve which outcome, and what result would contradict that expectation.
2. **Environment and ground truth.** Identify versions, service graph, image
   digests, hardware, workload, fault mechanism and the evidence establishing
   actual onset/impact. Keep injector truth out of agent inputs.
3. **Observed evidence.** Show timestamped signals available at the decision
   point, with missing/stale observations visible.
4. **Graph investigation.** Show the Q1 path/candidate roots, then Q2 candidates
   and excluded procedures. Separate observed symptoms from inferred attribution.
5. **Model decision and enforcement.** Preserve the structured output, candidate
   choice and rationale, validation result and provider usage. A rationale is not
   a faithful hidden reasoning trace and must not be presented as causal proof.
6. **Actual intervention.** Record target ID, catalog/script version, authorized
   operation, time, exit status and cleanup. Never publish credentials or tokens.
7. **Independent outcome.** Show before/during/after transactions and post-repair
   service measurements. Include failed attempts, recurrence and operator cleanup.
8. **Aggregate analysis.** Only now calculate metrics across the predeclared
   trial population, followed by uncertainty, ablation and limitations.

A worked incident is evidence of mechanism, not a replacement for a controlled
study. Use the [development walkthrough](../eval/results/verified/20261014/README.md)
as a concrete starting example, while preserving its development-run caveats.

## Questions And Comparisons

| Research question | Comparison | Main outcomes | Key control |
| --- | --- | --- | --- |
| RQ1: Does graph traversal improve supported root attribution? | Deterministic Q1; topology-as-text model; topology-blind model; vector SOP retrieval | Positive root accuracy, negative-case abstention, potential-impact F1, full prediction latency | Q1 and topology-text receive identical edges and health. Topology-blind/vector arms are explicitly different information treatments. |
| RQ2: Does filtering reduce context without harming decisions? | Same model with graph-filtered candidates versus complete library; varied distractor counts | Actual input/output tokens, invalid choices, valid-but-unsuitable choices, latency | Same root/evidence/applicability metadata, randomized order, paired cases |
| RQ3: Does the LLM improve action choice? | Full agent versus risk-ranked deterministic policy with the same graph, tools and verification | Verified recovery, disruption, recurrence, unnecessary interventions, cost | Include multiple plausible approved actions and real trade-offs; a single-candidate case is weak evidence |
| RQ4: Does dual-graph coupling itself help? | Coupled graph versus a flat or relational representation with equivalent knowledge | Retrieval correctness, update consistency, decision/recovery outcomes, maintenance effort if explicitly measured | Do not remove knowledge only from the baseline; hold topology, procedures and guardrails fixed |
| RQ5: Does verification prevent false recovery? | Component-only versus application-level evidence, initially through replay/shadow evaluation | False-RESOLVED count, detection of partial repair, added latency | Do not deliberately disable production-facing safeguards just to improve timing |
| RQ6: Can the system adapt beyond known demonstrations? | Held-out services/topologies/parameters, symptom-only alerts, changed-condition and unknown-fault cases | Recovery/abstention, diagnostic probe utility, coverage and failures | Freeze prompts/catalog first; do not tune on the test incident descriptions |

The code already supports controlled localization and candidate-context studies.
RQ3's physical policy comparison, RQ4's representation-parity ablation and the
adaptive diagnosis work in [AGENTIC_SCOPE.md](AGENTIC_SCOPE.md) need additional
implementation/validation. Do not describe them as completed experiments.

## Experiment Sequence

### 1. Freeze And Audit

- Assign a protocol version, source commit, dependency/image versions, model ID,
  prompt hash, budgets, dataset split, seeds and metrics before collecting results.
- Inventory every supported target/condition. Resolve destructive SOP policy,
  changed-condition handling and durable/temporary outcome labels first.
- Verify the independent collector and application canary under a healthy soak.
  Record false alerts per hour; a five-second poll setting is not measured latency.
- Run offline regression, isolated Neo4j checks and fixed-SOP physical acceptance.
  None of these is a model-benefit measurement.

### 2. Pilot Without Selecting For Success

Use a small declared pilot to test instrumentation, provider limits and cleanup.
Keep pilot results separate from the main experiment. Decide repetitions from
pilot variability and feasible precision, not a target accuracy percentage.
Three deterministic repetitions of one fixture are not three independent cases.

The [shadow replay harness](SHADOW_INVESTIGATION.md#frozen-input-replay) now
identifies sanitized input bundles and same-tool runs. The first real synthetic
shadow pilot failed and was denied; retain it as instrumentation/development
evidence, not a successful RCA case. Revised prompts must be identified separately
and tuned before the held-out protocol is frozen. A subsequent frozen three-case
shadow batch also failed diagnostic acceptance, including a wrong provisional
target and a guard fallback initially mis-scored as model correctness. Both the
failure and separate score correction are retained. No main-study result exists.

### 3. Matched Localization And Selection

The existing V3 localization set has 22 cases: 17 positive and five negative.
With three model arms and three repetitions it requests up to **198 model
attempts**. The selection study has nine cases, three library sizes, two contexts
and three repetitions: **162 attempts**. These totals are planning bounds, not
results. Obtain a total call/token budget before running them; live incident
retries and physical studies need a separate allowance.

Follow [EVALUATION_PROTOCOL.md](EVALUATION_PROTOCOL.md). Use randomized/alternated
arm order to reduce drift. Report API/parse/usage failures, not just valid answers.

### 4. Blind Physical Recovery

Use a separate experiment controller to select the fault target, mechanism,
severity, start time and seed. Keep this manifest inaccessible to the agent.
Start from a verified healthy baseline, capture failed transactions and observed
symptoms, then allow the normal collector/agent loop to act. Do not post an alert
that already contains the known root merely to make localization easy.

Compare the model agent and deterministic policy on matched reset environments,
identical workloads and the same approved operations. Retain every attempted
case, including invalid injection, missed detection, escalation, partial recovery,
unsafe proposal, cleanup failure and recurrence. Automatic detection is required
for an autonomous trial; a manually selected SOP belongs to executor acceptance.

### 5. Generalization And Failure Cases

Hold out names, graph shapes, fault magnitudes and combinations, not just random
seeds for an already-tuned case. Include unknown faults, stale telemetry,
incorrect/missing topology edges, simultaneous independent causes, changed
conditions and model/API outages. Escalation can be the correct outcome.
TrainTicket topology-only tests cannot stand in for a live second deployment.

### 6. Reproduction And Release

Freeze results before writing the abstract. Export sanitized per-trial records,
graph/catalog snapshots, hashes, scoring code, figure manifests and a failure
ledger. Re-run plots from those exports in a clean environment. A second person
should trace several figure points back to raw observations and recompute them.

## Metric Definitions

| Metric | Definition / denominator | Reporting constraint |
| --- | --- | --- |
| Root accuracy | Correct attributed roots / eligible positive incidents | Separate coverage, ambiguity and failed predictions; no ground-truth leakage |
| Negative-case correctness | Correct none/review decisions / declared negative cases | An API failure is not a correct abstention |
| Potential blast F1 | F1 of predicted dependent set against reviewed topology truth | Structural potential impact only; actual outage impact requires separate transactions/spans |
| Verified recovery | Incidents meeting all service/application/soak criteria / eligible physical incidents | Include failures in denominator; report invalid setups separately and retain them |
| False recovery | RESOLVED reports with failed independent recovery evidence | Show count, denominator and concrete failure examples |
| Detection latency | First accepted incident time minus independently measured fault onset | Dashboard-click time is only a proxy when exact onset is unavailable |
| Agent handling | Terminal decision time minus API receipt | Not end-to-end MTTR; current reports retain `mttr_seconds` as a legacy alias |
| Time to confirmed recovery | Confirmed recovery time minus measured onset | Report unresolved/time-censored cases; do not compare against inference-only latency |
| Token usage | Provider-reported input/output/total tokens, including retries where known | Missing usage is null; characters are not tokens; pricing is a separate dated calculation |
| Safety | Invalid proposals, denied requests, executed off-allowlist actions and damaging allowed actions | Distinguish all four; zero in a finite test is not proof of impossibility |
| Durability | Recurrence during a declared soak and after an appropriate restart | Do not count operator baseline reset as autonomous durable repair |

Report per-scenario distributions and raw counts alongside aggregate values.
Use paired differences for paired designs, and uncertainty that respects scenario
grouping rather than treating repeated runs of one fixture as independent cases.
For timing, show the median/spread and unresolved cases rather than only a mean
over successes. Predeclare exclusions and analysis choices before the main run.

## Figures For The Results Paper

| Figure | Data needed | Status |
| --- | --- | --- |
| Dual-graph architecture and permission boundary | Implemented modules, graph relations and trusted components | Can be drawn now; not a performance result |
| Worked incident swimlane | Onset, detection, evidence, candidates, model decision, executions, verification, report | Development examples available; exact onset/probe timing needs stronger recording |
| Recovery trace | Request success/latency and resource measurements before/during/after intervention | Individual canaries available; continuous series still needed |
| Accuracy and abstention by depth | Paired model predictions with matched evidence, raw counts and uncertainty | Deterministic controls available; corrected model comparisons pending |
| Recovery outcomes by fault family | Same verified outcome policy across agent and no-model baseline | Repeated physical comparison pending |
| Time-to-recovery distributions | Complete and censored times under the same endpoint | Pending; do not substitute inference-time bars |
| Context scaling | Tokens/choice errors versus distractors | Prompt-character control available now; provider-token experiment pending |
| Ablation and safety table | Equal-information arms, verifier ablation, denied and harmful allowed actions | Partial controls only; full study pending |

Current plots are deliberately labeled as controls or development observations.
No "our method beats prior work" chart is justified by the present evidence.

## Related Work: Fair Positioning

- [Flow-of-Action](https://arxiv.org/html/2502.08224v1) describes SOP retrieval,
  diagnostic code generation/execution and location/type evaluation. Do not
  describe it as proven unsafe production repair. Our maintained executable
  catalog and explicit repair verification are a design contrast, not a matched
  performance victory.
- [STRATUS](https://papers.nips.cc/paper_files/paper/2025/file/47a6e9e2c3019f13ad94a0f259fe4970-Paper-Conference.pdf)
  performs autonomous mitigation and studies transactional non-regression. We
  cannot claim that all earlier systems are advisory or that our allowlist is
  equivalent to its undo-based safety mechanism.
- [AIOpsLab](https://arxiv.org/abs/2501.06706) and
  [ITBench](https://proceedings.mlr.press/v267/jha25a.html) provide operational
  evaluation settings. Use a reproducible compatible subset if feasible; name
  any adapter and task changes. Published scores on their tasks are not directly
  comparable with this project's smaller hand-authored Boutique fixtures.

For a direct comparison, run a faithful implementation on the same tasks and
evidence with the same evaluation endpoint, or explicitly label the comparison
as qualitative. The dual graph remains the central design hypothesis; fair
baselines determine whether its practical advantage is real.
