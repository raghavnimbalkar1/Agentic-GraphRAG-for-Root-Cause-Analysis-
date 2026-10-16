# M1 Healthy Shadow Observation Check

Date: October 16, 2026. Runtime source: `fe83a0f` on
`codex/symptom-observation`; tracked working tree clean at rehearsal start.
This is the first M1 slice, not full M1 acceptance or an autonomous repair trial.

## Checks

| Check | Observed result |
| --- | --- |
| Offline regression | 265 passed, three opt-in live graph tests skipped |
| Live graph regression | Three passed separately; isolated fixtures, no live fault |
| Lint | New modules/focused tests passed default rules; runtime/tests passed F rules |
| Registry packaging | Local wheel built without downloads; registry JSON included |
| New collector startup | Two fresh all-healthy rounds; evidence store ready; delivery disabled |
| Real mock transactions | Two complete healthy checkouts, with cleanup, at least 30 seconds apart |
| Evidence store | All twelve services represented; numeric measurements retained; two symptom observations |
| Symptom episodes | None opened on the healthy deployment |
| Final graph | Twelve services healthy; catalog counts 12 Service and 15 Skill |
| Existing audit stream | No new RCA report; collector event file unchanged during rehearsal |

The old observe-only collector was stopped deliberately. Its replacement,
PID 26193, runs with `--observe-only --record-evidence`. This is an upgrade
restart, not evidence of automatic dependency recovery; M0 has separate records.
The transaction monitor was invoked twice with `--once` and is stopped. No
continuous synthetic checkout workload was left running.

The first two recorded collector polls took 4.461 and 4.261 seconds. Transaction
checks took 0.138 and 0.159 seconds. These two healthy samples are not overhead
estimates, detection-latency measurements, MTTR or a recovery success rate. The
final store snapshot held 252 component records plus two symptom records and
occupied 335,872 bytes. The row count grew between the first and final snapshots
because the collector continued recording; this is not duplicate symptom delivery.
These file sizes do not include a guarantee about future SQLite/WAL allocation.

## Recorder Error and Provenance

The [partial rehearsal](partial_rehearsal.json) is preserved unchanged. Its helper
used `GraphClient` as a context manager, which is unsupported. Both transactions,
collector rounds and storage checks had completed, but the final graph-summary
step raised `TypeError`; the partial record correctly remains `passed=false`.

The [separate final check](final_check.json) uses explicit client cleanup and
checks fresh graph health, catalog counts, current collector readiness and stored
observations. It references the partial record by SHA-256 and retains source
hashes. No transaction was repeated to complete that check. Its pass does not
erase the recorder error or turn a healthy observation into a fault-recovery trial.

The storefront, existing dashboard health endpoint and API liveness endpoint
subsequently returned HTTP 200. The dashboard does not yet display these new
symptom episodes. See [the operating guide](../../../../../docs/SYMPTOM_OBSERVATION.md).

## Not Tested Here

No fault was injected, provider called, incident submitted or SOP executed.
The hidden-payment-fault symptom gate, independent-anomaly controls on live
services, trace/log ingestion and model-guided investigation remain pending.
The real Docker Desktop pause/resume test was skipped by the user, not passed.
HTTP failure/timeout/redirect cases in the regression suite use a mock transport.
There is no new evidence here for root-cause accuracy, LLM benefit or large-scale
deployment. The review paper was not changed.
