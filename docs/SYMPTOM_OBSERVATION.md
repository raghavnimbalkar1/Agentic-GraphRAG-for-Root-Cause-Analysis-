# Symptom Observation and Retained Diagnostic Evidence

This opt-in local profile adds observations, not a new autonomous repair path.
It keeps the existing dual graph and remediation policies intact. M1 is not yet
complete: trace/log adapters, observed topology, alternative-cause controls and
incident investigation remain work for subsequent slices. One development
payment-partition observation check has passed; it is not a repeated RCA study.

## Data Flow

```text
Reviewed deployment registry
  -> component probes -> versioned observations -> bounded SQLite evidence

Independent mock checkout monitor
  -> transaction steps -> healthy / failed / unknown observations
  -> persistent frontend symptom episodes (shadow-only)

No symptom episode is sent to the agent or executor in this profile.
```

The observer does not read the fault injector. A checkout failure records
`symptom_service=frontend`; `root_cause_node` remains null and `candidate_roots`
remains empty. A concurrent component anomaly is not automatically its cause.
Model-guided investigation and evidence-backed attribution are M2 work.

## Registry and Evidence

`core/deployments/online_boutique.json` declares twelve logical service names,
their runtime container names, ports and reviewed probe types. The health module
and graph service definitions now share this registry. Each new observation has
an environment ID, registry revision and canonical SHA-256 digest. Component
records also retain the inspected container ID when available. Version 1
observation records remain readable; version 2 requires deployment provenance.

The registry is bundled in the Python wheel. Loading another registry validates
its description but does not switch the active collector, canary, graph or
controller. Adding a service never grants restart permission. This is groundwork
for onboarding, not a supported arbitrary-application installer or discovery tool.

`audit/evidence.sqlite` contains component and transaction evidence. Queries are
scoped by environment and registry digest, with optional service/time filters.
The default retention policy is 20,000 records or 24 hours, whichever is reached
first. Queries return at most 1,000 records. Payloads are limited to 8 KiB.
SQLite can retain allocated pages after deletion; this is not a filesystem quota.

The store retains allowlisted numeric measurements and fixed probe summaries,
not exception text, HTTP bodies, cookies, form values or arbitrary log fields.
CPU, memory and writable-layer samples are now retained even when readiness is
healthy. These are existing probe measurements, not comprehensive metrics or
proof of a fault cause. There is no raw-log, span or trace ingestion yet.

## Transaction and Episode Rules

- The fixed mock workflow is home, add one item, read cart, checkout and cleanup.
  It uses its own session and a synthetic test payment. Never point it at a real
  commerce or payment system.
- Requests stay on `http://127.0.0.1:8080` and four approved paths. External
  redirects, other ports, credentials in URLs and query strings are rejected.
- Each response is capped at 1 MiB; redirects are bounded. The transaction has
  a 25-second budget and cleanup gets a separate three-second budget, with
  request timeouts. These are cooperative limits, not process-level preemption.
- Two consecutive observed failures open one persistent `SYM-...` episode.
  Two successful complete transactions mark it `recovered`. This means observed
  transaction recovery, not an agent repair or a RESOLVED RCA report.
- Unknown evidence cannot open or recover an episode and resets debounce counts.
  Stale, future-dated and out-of-order measurements cannot advance an episode.
  A long sampling gap resets debounce counts without discarding an open episode.
- Restart preserves the active episode ID. Each episode keeps up to 32 evidence
  references; retention may remove their underlying samples. At most 200 closed
  episodes and 32 monitor/environment keys are retained. Open episodes stay open
  until recovery is observed; exceeding the key limit requires operator review.

The default monitor delay is 30 seconds after each completed check. It does not
overlap probes or run catch-up bursts after suspension. Two failures therefore
do not imply a fixed 30-second detection latency. Measure actual latency and the
extra mock-transaction workload in later experiments.

## Run Locally

Use the same project environment and audit directory as the existing processes.
Stop the existing collector before starting this one; never run two collectors.
Docker, Neo4j and the local mock storefront must already be running.

```bash
python -m simulation.telemetry_collector --observe-only --record-evidence
```

In a separate terminal, perform one transaction check and stop:

```bash
python -m simulation.symptom_monitor --once
python -m json.tool audit/symptom_monitor_status.json
```

For scheduled checks, omit `--once`:

```bash
python -m simulation.symptom_monitor --interval 30
```

Stop the monitor before returning the component collector to automatic delivery.
The monitor blocks transaction checks when the collector status says delivery is
enabled. This is an operational guard, not process credential isolation. Both
processes run as the local user; the standard settings loader can read `.env`.
The monitor has no model, alert-submission or repair path, but M2's stronger
credential separation is not implemented by calling this mode "shadow".

The collector reports evidence-store readiness separately from sensing readiness.
A storage error does not create a service fault or enable automatic delivery.
Monitor writes and invalid probe results are rate-limited and reported as degraded.
The macOS/Linux monitor holds a separate file lock; a second monitor using the
same audit directory exits. `--once` leaves a stopped status with its last result.

The dashboard's **Transaction Symptoms** tab shows checkout observations, failed
steps, a latency plot, open/recovered symptom episodes and the retained evidence
for each episode. It refreshes every five seconds and does not start probes or
submit repairs. The read-only database connection cannot initialize, prune or
write evidence. Its data is independent of Neo4j, unlike the live graph views.

The view distinguishes a stopped monitor, stale observations, unknown results
and an unavailable store. A historical healthy result is not current health.
"Observed recovery" is not a RESOLVED agent report. Root attribution remains
empty. The view displays at most 200 observations and explicitly reports missing
episode references when retention has removed their data. Environment and
registry checks prevent mixing evidence from different deployment versions.

## Component Evidence Windows

The same tab includes **Component evidence**. Select one registered service,
a one-, five- or fifteen-minute window, and an available measurement. The window
can end now or at the measured completion time of a retained checkout. The reader
uses only existing records. Opening the view never runs a new probe or repair.

`core/diagnostics.py` defines the bounded request and reader. The caller cannot
supply SQL, a URL, a filesystem path or an executable operation through the
request. Trusted application code supplies the database path and registry.
The reader opens SQLite in read-only/query-only mode and never creates a store.
Importing this reader does not load application settings or `.env`; the existing
dashboard still loads settings. This is not an operating-system sandbox or a
claim that the entire dashboard process is isolated from credentials.

| Measurement | Service | Interpretation |
| --- | --- | --- |
| CPU percentage | adservice | Docker CPU delta; 100% is one CPU, not all host capacity |
| Memory usage | recommendationservice | Docker memory usage in bytes, not proof of a leak |
| Writable-layer size | emailservice | Container writable-layer bytes, not host free space |
| Redis memory cap | redis-cart | Configured maxmemory bytes; zero means unlimited |
| Redis clients | redis-cart | Connected clients seen by the existing probe |
| Expiring keys | redis-cart | Keys with expiry; not proof of stale application data |
| Frontend HTTP duration | frontend | Home-page request time, not full checkout latency |

These are the measurements already emitted by `health-probes-v1`, not universal
resource monitoring. Some probe branches return early; absent values remain
missing, never zero. A component condition is not an independent readiness result
for every sample. The dashboard separates these conditions from checkout outcomes.

Each response carries evidence and runtime-instance IDs, measurement/collection
times, provenance, unknown counts and gaps over 30 seconds. A state more than
30 seconds old, an unknown sample, or conflicting latest observations cannot
be presented as a fresh healthy state. This display threshold is independent of
the existing agent's configurable execution-evidence threshold. Historical state
at the window end is separate from freshness at read time.

Queries return the latest 100 samples and disclose truncation. Summary statistics
apply only to those returned values; the mean is sample-based, not time-weighted.
Gaps include window boundaries and omitted/expired data. They are not proof of
collector downtime. Instance changes remain explicit. Payloads are checked
against their storage scope, and output contains only reviewed fields. A response
is capped at 128 KiB. Retention is still bounded by the store's row/time limits.

Historical windows filter **measurement time**, not the time each record first
became available. They are retrospective views, not a frozen decision-time replay
engine. A later-written record with an earlier measurement can appear in such a
view. A matched evaluation must separately enforce evidence-availability cutoffs.
No component anomaly is ranked or promoted to a cause by this reader.

## Verification Boundary

The [October 16 development record](../eval/results/verified/20261016/m1/README.md)
contains 265 passing offline tests, three separately passing live graph checks
and two healthy real mock checkouts. It retains the recorder's partial failure
and the separate successful graph-summary check.

Offline checks cover registry validation and permissions, provenance compatibility,
retention, concurrent database writers, episode restart/deduplication, freshness,
unknown evidence, HTTP failures, local redirects and failure-path scheduling.
HTTP failure tests use `httpx.MockTransport`; they are not physical fault trials.

A healthy live checkout only validates collection on a healthy deployment.
The October 18 development check observed a frontend episode during a hidden
payment partition and recovery after manual restoration. Raw protocol and
outcomes are retained locally; no new autonomous-repair result is claimed.
The full live M1 acceptance gate still requires alternative-cause and
unrelated-anomaly controls, with preserved recovery evidence.
The observer must not reuse an injector-provided diagnosis as the symptom origin.
One development check does not replace repeated controlled trials. Alternative
causes, trace/log adapters and topology evidence remain separate work.
