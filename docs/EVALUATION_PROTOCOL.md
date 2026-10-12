# Evaluation Protocol

Protocol implementation: `ad3f204`. Freeze the source revision, provider/model,
budget, repetitions and scenario IDs before a model experiment. Results must not
be selected based on whether their numbers support the proposed contribution.

## Separate Questions

1. Does dependency traversal implement the reviewed attribution policy on the
   supplied topology, including incomplete, stale and ambiguous evidence?
2. Does supplying identical topology as text versus executing Cypher change
   localization quality/cost? Graph-only Q1 is a deterministic control, not an LLM.
3. Does candidate filtering change model selection errors, context usage and
   latency as irrelevant SOPs are added?
4. Does a model select better repairs than a fixed admissible risk-ranked policy?
   Applicability alone does not establish repair optimality.
5. Does the complete collector/agent/executor workflow independently recover real
   application transactions? Neither localization fixtures nor fixed-SOP executor
   checks answer this autonomous-recovery question.

## Localization V3

There are 22 scenarios: eight earlier positive fixtures, eight matched
depth/specificity cases, one multiple-SOP CPU case, and five negative controls.
The 17 positive and five negative cases are scored separately.

- Matched blocks use Redis OOM observations at depths 1, 2, 3 and 4. Within each
  generic/specific block, message, symptom code and complete health snapshot stay
  fixed. Only the alert service/dependency distance changes.
- Negative cases cover healthy evidence, an unreachable unrelated fault, unknown
  health, stale health and multiple independent unhealthy dependencies.
- Every system receives the same alert, complete statuses and observation ages.
  GraphTraversalNoLLM and TopologyText receive the identical full dependency edges.
  ZeroShot has no topology; VectorRAG has semantic top-k SOP documents but no edges.
  These are explicit knowledge treatments, not interchangeable representations.
- A valid result names one known root or returns null with `none`/`review`.
  Unsupported names, inconsistent outputs, API failures and parse failures remain
  errors. A failed request returning null is not a correct negative decision.
- Potential blast is reverse dependency reachability excluding root. It does not
  prove actual propagation. F1 uses the predicted set, not the known root's set.
- Timing covers each complete prediction path, not detection or recovery. Graph
  fixture construction and vector index build are outside prediction timing.

```bash
# Validate, then measure deterministic controls with zero model calls.
python -m eval.research_benchmark --dry-run --max-calls 0
python -m eval.research_benchmark --reps 3 --max-calls 0

# Eight model attempts: two matched depths and two negative cases, one repetition.
python -m eval.research_benchmark --with-llm --reps 1 --max-calls 8 \
  --scenario matched-generic-d1 --scenario matched-generic-d4 \
  --scenario negative-healthy --scenario negative-ambiguous

# Full study including semantic retrieval: 22 * 3 * 3 = 198 model attempts.
python -m eval.research_benchmark --with-llm --with-vector --reps 3 --max-calls 198
```

Optional vector dependencies/model weights must be installed before freezing the
runtime. All failures and raw responses are retained; missing usage is null.
The small controlled fixture is not a representative production accuracy dataset.
Identified zero-call and live-executor records are indexed in
[`eval/results/verified/20261011`](../eval/results/verified/20261011/README.md).

## Candidate Selection V3

Nine positive cases include a real HIGH_CPU condition with two approved SOPs.
Library breadth varies by adding 0, 50 or 200 explicitly synthetic, unrelated
skills. Synthetic skills have no executable path and are never written to Neo4j.

Both prompt contexts state identical applicability and trigger metadata for their
shared SOPs. Seeded ordering varies position; the filtered context preserves the
valid candidates' relative order. Pair order alternates by repetition. All other
case/root/condition/chain/prompt-template inputs remain fixed.

The no-model control chooses LOW before MEDIUM, then lexicographic name. HIGH is
not admissible. Its admissibility is measured, not presumed optimal remediation.
Report model errors, valid selections, off-target choices and outside-allowlist
choices separately. An invalid model choice is not an executed unsafe operation.

```bash
# Deterministic policy and prompt characters, not model tokens or model decisions.
python -m eval.context_ablation --policy-only --reps 1 \
  --distractors 0 50 200 --max-calls 0

# Paired same-model pilot: 2 scenarios * 3 sizes * 2 contexts = 12 attempts.
python -m eval.context_ablation --with-llm --reps 1 --distractors 0 50 200 \
  --scenario redis-d1 --scenario cpu-multiple-sops --max-calls 12

# Full paired study: 9 cases * 3 reps * 3 sizes * 2 contexts = 162 attempts.
python -m eval.context_ablation --with-llm --reps 3 \
  --distractors 0 50 200 --max-calls 162
```

`--max-calls` bounds inference attempts within that command and is checked before
database/model setup. SDK automatic retries are disabled. This is not a currency,
token or cross-command budget. Obtain a total experiment budget before running
paid requests. Prompt characters are not a substitute for provider token usage.
Filtering cannot guarantee constant cost if the valid candidate set/history grows.

![Measured prompt-character control](figures/verified-20261011/prompt_characters.png)

This figure comes from the identified zero-call record, not provider token usage.

## Physical Executor Acceptance

The live executor harness requires a fresh observe-only collector and a complete
healthy baseline. It injects a fault, requires an independent observation and
collector event, checks application transactions, executes an explicitly selected
catalog SOP, and requires two healthy rounds plus a successful application canary.
Each case resets in `finally`; failures and leftover SOP containers are recorded.

```bash
python -m simulation.telemetry_collector --observe-only
python -m eval.live_acceptance --case redis_oom  # plan only
python -m eval.live_acceptance --execute --case redis_oom \
  --case network_partition --case external_pause --case redis_oom_persistent
```

The supported acceptance targets are Redis, payment network partition, and
product-catalog pause. Payment pause has no CRASH_LOOPING SOP in this catalog;
refusing it is correct, not permission to substitute an unapproved skill.

The canary uses its own local mock-shop session, a synthetic cart, a mock Visa
number and `example.invalid` email. It checks cart content and the order-complete
marker, then attempts to empty only that session's cart. No real payment is made.
It captures request-level impact, not the outage state of every reachable service.

These tests make **zero LLM requests and no agent API deliveries**. Fixed-SOP
success must never be reported as autonomous-agent success, MTTR or an LLM effect.
Restart-plus-flush can mitigate a persistent Redis cap at runtime; the launch
command still needs restoration to prevent recurrence after a later restart.

## Final Acceptance Still Required

- Collector/API outage and duplicate-delivery tests with running processes.
- Paid provider, malformed-decision and full report/recovery traces.
- Every supported fault family, realistic onset signals, misses and false alerts.
- Long healthy soak, rollback/cleanup failures and post-restart relapse checks.
- Repeated matched model experiments, paired usage measurements and uncertainty.
- Reviewed primary literature and university-format submission artifacts.

Historical runs remain historical. Publish raw identified repetitions and report
negative results; do not replace them with a predetermined success headline.
