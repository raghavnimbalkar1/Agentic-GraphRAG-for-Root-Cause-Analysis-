# M0 Observe-Only Stability Checks

Date: October 16, 2026. Loaded collector revision: `f97383b`.
No fault, model request or remediation was initiated by these checks.
Automatic collector delivery remained disabled.

| Run | Result | Evidence |
| --- | --- | --- |
| Interrupted check | Failed: 16 of 121 samples passed | [Raw record](interrupted_soak.json) |
| Check after collector recovery | Passed: 121 of 121 samples passed | [Raw record](recovered_soak.json) |
| Deliberate Docker Desktop pause/resume | Skipped at the user's request; not passed | No trial performed |

The first run lasted 600.018 monotonic seconds but 1,271.757 wall-clock seconds.
It recorded stale telemetry followed by unavailable dependencies. The clock gap
does not establish whether the host slept or why the dependencies were unavailable.
This run must not be counted as a healthy soak. The collector retained PID 13608;
it did not dispatch a new incident or create a report.

On resumption, Docker was reachable. The same collector recovered fresh healthy
observations without a restart. The repeat run sampled the audit files every five
seconds from 10:50:58 to 11:00:58 UTC on October 16. All twelve component statuses
were HEALTHY, telemetry was fresh, and no incident episodes were present in every
sample. Maximum observed heartbeat age was 5.179 seconds; maximum recorded probe
cycle duration was 6.469 seconds. The event-file digest did not change, and no new
RCA report appeared. These observations do not establish continuous availability
between samples or prove application-level recovery.

The collector had loaded M0 code before the run. M1 source edits made during the
check were not loaded into that process. These are local development checks, not
a frozen autonomous benchmark. The earlier client-side reconnection rehearsal is
recorded [separately](../20261015/README.md). Operator-controlled engine
pause/resume remains an untested lifecycle gate.
