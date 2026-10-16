# Symptom Observation: M1 First Slice

This opt-in local profile adds observations, not a new autonomous repair path.
It keeps the existing dual graph and remediation policies intact. M1 is not yet
complete: trace/log adapters, incident investigation and the hidden-fault live
acceptance test remain work for subsequent slices.

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

The existing dashboard still shows component health and legacy RCA incidents.
It does not yet display the new symptom episodes. Inspect the local monitor
status and bounded store for this slice; dashboard integration remains pending.

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
Full M1 acceptance still needs a separately authorized hidden payment partition,
an observed frontend episode, unrelated-anomaly controls and preserved recovery
evidence. It must not reuse an injector-provided diagnosis as the symptom origin.
