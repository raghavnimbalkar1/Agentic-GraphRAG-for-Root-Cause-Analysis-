# Identified Evidence: 2026-10-11

Primary measurements below used source revision
`ad3f20487544176c0a01e011ee71ff941e267aab` with a clean working tree at run creation.
No model requests were made. Metadata names the configured provider/model, not a
provider that participated in these measurements.

## Primary Records

| Record | Measured scope | Outcome |
| --- | --- | --- |
| `localisation_v3_20261011T173056Z_4f3b73d0.json` | Real Neo4j; 22 controlled cases, three repetitions | 51/51 positive roots, 15/15 negative decisions; no errors; constructed policy fixtures, not production accuracy |
| `selection_policy_v3_20261011T173108Z_3d44a9e4.json` | Nine cases at 0/50/200 synthetic distractors; one repetition per size | 27 policy/prompt-control pairs, zero model calls; no model-selection or token-cost result |
| `live_executor_v1_20261011T173135Z_52b40a8e.json` | Four physical simulation faults, fixed approved SOPs | Four pass, including two-SOP persistent Redis fallback; zero leftover SOP containers; not autonomous-agent trials |
| `environment_preflight.json` | Post-reset dependency, API liveness and denied-controller check | All 12 services healthy, managed labels valid, Neo4j/API/controller reachable, collector fresh; delivery deliberately disabled; model not probed |

The live cases are Redis OOM, payment network partition, product-catalog pause
and persistent Redis OOM. Each records a successful before canary, a failed
during-fault canary, a successful after canary, independent observations and
collector ID, two healthy rounds, executions and fault reset. Application evidence
is private-session cart/checkout behavior, not outage proof for every graph node.

The environment preflight was captured after the final checks. Unlike the three
primary experiments it is an operational snapshot, not a frozen inference trial.
Its nonzero closed-loop gate is intentional: `alert_delivery_enabled=false`.

## Exploratory Records And Failures

- `live_executor_v1_20261011T172355Z_a102013f.json`: development run, dirty source;
  Redis, network and persistent Redis cases passed. The attempted payment pause
  had no applicable CRASH_LOOPING SOP and correctly executed nothing. This
  failure remains in that run's denominator (`all_passed=false`).
- `live_executor_v1_20261011T172944Z_2b9e46b9.json`: development rerun of the
  approved product-catalog pause target, passed. Do not pool it with the previous
  payment-pause case as though the target/protocol had not changed.
- An earlier initial recorder attempt failed with
  `TypeError: Object of type datetime is not JSON serializable`. Its complete raw
  trial could not be saved; that data-loss limitation is disclosed, not counted
  as success. The harness now serializes typed results with `mode='json'`. All
  faults were reset before the formal rerun. This paragraph is a retrospective
  diagnostic note, not a reconstructed measurement.

Do not combine development and clean-source runs into a success-rate headline.
These one-off executor checks establish the tested paths, not repeatability over
all supported fault families, autonomous recovery, model benefit or MTTR.

## Figure Reproduction

```bash
python -m eval.plot_controls \
  eval/results/verified/20261011/selection_policy_v3_20261011T173108Z_3d44a9e4.json \
  --output docs/figures/verified-20261011
```

The PNG and manifest identify the source SHA-256 and measured rows. This is a
prompt-character figure with a fixed candidate set, not a model-token experiment.
See [the protocol](../../../../docs/EVALUATION_PROTOCOL.md) and
[completion progress](../../../../docs/COMPLETION_PROGRESS.md) for pending gates.
