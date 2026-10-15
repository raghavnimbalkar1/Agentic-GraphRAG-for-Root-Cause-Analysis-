# M0 Collector Lifecycle: Development Verification

Date: October 15, 2026. Source baseline: `b6515866b99461299b73b2b216a7d6e2b872ec87`.
Branch: `codex/evidence-baseline`; working tree dirty during verification.

This record checks telemetry reliability. It is **not** an autonomous repair
trial, accuracy benchmark, healthy soak or matched policy comparison. No fault
was injected, no alert was submitted and no model or remediation was called.
The existing automatic collector was stopped while idle. The replacement was
left in observe-only mode so that subsequent checks could not trigger repairs.

## Retained Evidence

[collector_lifecycle.json](collector_lifecycle.json) contains:

- Exact per-file hashes for the tested source, plus selected baseline Git-object
  hashes for dependency constraints, Compose definitions and graph/catalog files.
- Running image IDs/digests and instance IDs. These identify the environment at
  this check, not necessarily every image used by earlier historical trials.
- A sanitized live catalog snapshot: 12 services, 15 skills, 16 dependencies,
  19 applicability links and one fallback link.
- All seven lifecycle rounds, timestamps, duration, observation IDs, quality,
  instance identity, dependency state and graph-sync result.
- Shutdown state, unchanged collector-event log and zero new RCA reports.

Core collector code did not change after this rehearsal. The dashboard then
received timed refreshes and was checked separately in a fresh Streamlit test
process. Its final source hashes therefore differ from those in the lifecycle
record. These are development checks, not a frozen release experiment.

[dashboard_freshness.json](dashboard_freshness.json) records a subsequent idle-page
check with the final dashboard source hashes. The observe-only collector was
stopped while the services remained running. Without a browser refresh, the
header changed to stopped, then expired; after the observation lifetime elapsed,
the live view stopped reporting a fresh healthy baseline. All 12 saved samples
were then classified STALE. Observe-only monitoring was restarted afterwards.

## Method and Outcome

The collector used real Docker probes and real Neo4j health updates with
`deliver_alerts=False`. The same collector instance crossed each failed
connection and recovery. A failed Docker endpoint was an unavailable local Unix
socket. The failed Neo4j endpoint was a locally reserved, non-listening TCP port.
The actual Docker engine and Neo4j server were not paused, stopped or modified
to create those failures. Recovery selected the configured real clients after
the five-second retry interval.

| Stage | Expected sensing state | Observed | Poll duration |
| --- | --- | --- | ---: |
| Docker endpoint unavailable at startup | Not ready | Passed | 0.04 s |
| Reconnect to Docker | Ready, all 12 healthy | Passed | 4.72 s |
| Docker endpoint lost during collection | Not ready | Passed | 0.02 s |
| Re-establish Docker connection | Ready, all 12 healthy | Passed | 4.77 s |
| Neo4j endpoint unavailable | Not ready | Passed | 5.42 s |
| Re-establish Neo4j connection | Ready, all 12 healthy | Passed | 4.61 s |
| Second healthy round | Ready, all 12 healthy | Passed | 4.45 s |

These durations describe one poll in this rehearsal. They exclude the deliberate
retry wait and are not detection latency, MTTR or statistical capacity estimates.
The collector wrote UNKNOWN during unavailable sensing rather than presenting a
last-known healthy value as current. Real probes restored healthy graph values.
It used temporary readiness-probe containers but executed no repair procedure.

## Other Checks

- Full offline suite: **217 passed, 3 skipped**. The skipped tests require an
  explicit live Neo4j opt-in.
- The three opt-in Neo4j tests then passed separately. They check the existing
  catalog without changing live health and create/delete UUID-scoped evaluation
  fixtures. They do not measure production root-cause accuracy.
- Python F-rule checks passed for the changed runtime areas and tests. Focused
  Ruff checks passed for the new collector/freshness modules and lifecycle tests.
- A fresh-process Streamlit render completed without application exceptions and
  displayed observe-only sensing and all 12 fresh healthy observations. Existing
  Streamlit deprecation warnings remain; no UI-triggered model test was run.

Offline lifecycle cases cover startup failure, reconnect, capped retry delays,
Neo4j failure, stale/future/invalid/missing observations, evidence expiring during
graph sync, partial graph updates, duplicate collector ownership, shutdown,
pending-episode identity after restart and changed-condition suppression.
Existing tests retain duplicate API delivery and legacy report coverage.

## Limits and Next Gates

Actual Docker Desktop pause/resume and OS suspension were **not** repeated. The
checks establish bounded client reconnection, not a guarantee for every engine
failure mode. Abrupt process death, an in-flight repair and lost acknowledgement
are different tests; persistent workflow reconciliation belongs to M5.

The default five-second poll target is not a hard guarantee: health probes and
failed connections take time. A stalled call can make the heartbeat expire;
readers must then treat sensing as unavailable. The new single-collector lock is
local to an audit directory and only coordinates collectors that implement it.

The source was not committed at test time. Image digests and hashes preserve the
tested environment, but reproducibility does not imply all upstream images will
remain downloadable. Preserve the older passing and failed trials unchanged.

Before enabling automatic delivery again, confirm a fresh complete baseline,
review any pending episodes and the model budget, stop the observe-only collector,
and start one normal collector. The broader adaptive investigation and controlled
journal comparison remain future milestones.
