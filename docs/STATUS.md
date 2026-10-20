# Project Status

Evidence reviewed: **2026-10-11**. Repository documentation organized: **2026-10-12**.
Provider setup and two autonomous dashboard paths verified: **2026-10-13**.
CPU mitigation and publication documentation reviewed: **2026-10-14**.
M0 collector reliability and freshness checks: **2026-10-15 to 2026-10-16**.
M1 symptom dashboard and retained component windows: **2026-10-18 to 2026-10-19**.
M1 sanitized frontend error-signal pilot: **2026-10-21** (offline checks; Docker unavailable).

The implementation is a research prototype with offline regression, live Neo4j
and physical fixed-SOP coverage. **Broad autonomous acceptance and the final
submission are not complete.** The October 11 batch made no model requests.
Later Groq inference and bounded live rehearsals are recorded separately below.

## Implementation And Acceptance

| Area | Implemented and checked | Remaining acceptance |
| --- | --- | --- |
| Graph | Unified health-preserving catalog; typed queries; isolated fixtures; live validation | Fresh isolated database seed/reseed |
| Agent | Bounded workflow, terminal escalation, same-target fallback; real Groq decisions through two live recovery/report paths | Repeated all-family autonomous acceptance and model comparisons |
| Observation | Independent probes, durable episodes, acknowledged retry, observe-only mode; ten-minute healthy component soak | All fault families, missed/false alerts and longer application-level soak |
| Coordination | SQLite replay protection, serialized API, exact incident correlation | Running-process outage/restart/duplicate tests and independent-root demonstration |
| Execution | Native arm64 image, socketless restricted SOPs, owned-target controller, real recovery checks | Remaining SOP families; timeout, replay, cleanup-failure and relapse checks |
| Evaluation | Matched/negative cases, information-parity baseline, paired selection harness and call caps | Repeated model comparisons, measured usage and uncertainty |
| Dashboard | Eight views; symptom/retained-component evidence, self-contained graph assets, incident correlation and reports | Broader UI coverage and maintained evidence provenance |
| Submission | Architecture, protocol, runbook and evidence boundaries | Verified literature, thesis, required paper/slides/poster and recording |

Automatic topology discovery, Kubernetes deployment, general causal discovery
and correlated multi-fault inference are outside the current scope. TrainTicket
is a supplied localization fixture, not a deployed remediation testbed.

## Verification Record

The 2026-10-11 record includes:

- **156 offline tests passed**; three opt-in live graph checks skipped in that
  suite and separately passed against real Neo4j.
- Python F-rule lint, supported Compose validation, shell syntax and dependency
  consistency checks passed.
- The supported 12-container stack and native arm64 executor ran on Docker
  Desktop. Existing Neo4j data was preserved.
- The catalog validated at 12 Service, 15 Skill, 16 DEPENDS_ON, 19 APPLIES_TO and
  one NEXT_IF_FAIL edge.
- Clean-source graph controls ran 22 cases three times: 51 positive root matches
  and 15 negative decision matches, with no prediction errors. These constructed
  fixtures do not establish production causal accuracy or an LLM contribution.
- A zero-call policy/library control recorded 27 pairs. The figure measures
  prompt characters, not provider tokens, model quality or billing.
- Four clean-source fixed-SOP cases passed: Redis memory cap, payment network
  partition, product-catalog pause and persistent Redis restart-to-flush fallback.
  Each captured failed during-fault and successful before/after transaction
  canaries, two healthy rounds, reset and no leftover SOP containers.

Primary measurements used source revision `ad3f204` with a clean working tree
at run creation. Exploratory failures are retained and excluded from the formal
passing-trial headline; an initial recorder data-loss failure is disclosed.
See the [raw evidence index](../eval/results/verified/20261011/README.md).

The saved environment check confirmed API liveness with collector delivery
disabled. It did not probe the model. This is a dated record, not a statement
that processes are currently running. Fixed-SOP results are not autonomous-agent
success-rate or MTTR evidence. Earlier result artifacts remain historical.

## Groq Connection Check (2026-10-13)

The local environment now selects Groq-hosted `openai/gpt-oss-120b`. The saved
Groq key authenticated successfully, and one request through the application's
provider adapter returned valid JSON selecting `Redis_Config_Reset_SOP` for a
synthetic configuration-drift case. Provider-reported usage was 684 input and
116 output tokens (800 total). No fault was injected, graph state changed, SOP
executed, or recovery measured. This confirms inference connectivity and a
single-candidate response contract, not diagnostic accuracy or autonomous
acceptance. Repeat with `python -m scripts.check_llm`.

After the provider addition, **166 offline tests passed** and three opt-in live
Neo4j tests were skipped. The added tests cover credential selection, endpoint
and request compatibility, token accounting, missing-key escalation, and the
non-executing smoke check. The earlier physical and graph evidence remains a
separate dated record.

## Completion Sequence

### Live Rehearsal (2026-10-13)

Three development attempts were retained, not pooled as a formal success rate:

- Payment network partition, first attempt: the real Groq call and SOP ran, but
  checkout still returned HTTP 500 after the report said RESOLVED. This is a
  failed application-recovery trial despite its old report status.
- Payment network partition after repair: RESOLVED, checkout 200 before / read
  timeout during / 200 after; 786 provider-reported tokens, 13.448 seconds agent handling,
  about 24.15 seconds from dashboard injection request to observed report.
- Persistent Redis memory cap: RESOLVED after Redis_Restart_SOP then
  Redis_Flush_SOP; the first verification still observed the 1 MiB cap. Two
  real model decisions used 1,625 tokens in total. Agent handling was 36.357
  seconds; injection request to observed report was about 49.53 seconds.

The repair reconnects a partitioned target after restarting it, avoiding an
intermediate network address. Recovery now requires two successful application
transactions when frontend is in the affected set, in addition to service
probes. Missing or failed application evidence cannot yield RESOLVED. The
restricted controller still acts only on the approved target.

Both corrected trials used Groq `openai/gpt-oss-120b`, actual dashboard injection,
independent collector-created incident IDs, sandbox execution, fresh observations
and healthy graph updates. Both alerts originated at the failed service: these
are depth-zero automatic localization examples, not new multi-hop accuracy
measurements. All faults were reset; no SOP containers remained. These are
single development checks per path on a dirty working tree, not a replacement
for the controlled benchmark or an autonomous success-rate estimate.

After these fixes, 176 offline tests passed and three opt-in graph tests were
skipped. Three live graph tests separately passed earlier on October 13.
See the [public rehearsal evidence](../eval/results/verified/20261014/README.md).

### CPU Investigation And Documentation (2026-10-14)

The original high-CPU repair failed because the installed Docker SDK does not
accept `nano_cpus` in container updates. A real-SDK serialization regression now
checks the supported period/quota request. The first live correction preserved
the old 0.1-CPU cap; it applied successfully but starved adservice readiness.
That FAILED report and its secondary DEGRADED escalation remain in the evidence.

One further authorized trial used a 0.5-CPU cap. Real Groq inference selected the
throttle procedure, CPU measured about 51%, readiness and checkout passed, and
all twelve graph statuses were healthy. The report recorded 13.812 seconds
handling and 834 tokens. This is a runtime mitigation, not removal of the burner.
Baseline cleanup subsequently timed out; Docker later reported manually paused.
The overall rehearsal remains failed even though the mitigation was verified.
No current healthy runtime or completed cleanup is asserted by this dated record.

The offline suite now has **183 passing tests**, with three opt-in live checks
skipped. Python F-rule lint passed. New documentation covers all SOPs, bounded
autonomy and the two-journal study plan. The README uses actual captures and
reproducible figures; historical comparative metrics were not promoted to results.

### Baseline Restored After Resume (2026-10-15)

At approximately 01:25 IST, direct checks confirmed that adservice was already
recreated with its normal CPU settings and no injected busy-loop. No further
container reset was necessary. The collector had exited during paused-Docker
startup; after two healthy all-service rounds and a successful mock checkout,
one collector was restarted. Three fresh collector rounds and Neo4j observations
showed all twelve services healthy, with automatic delivery enabled and no episodes.
Checkout passed again; the API, dashboard and storefront returned HTTP 200.
No new incident or model decision was recorded, and no SOP executor remained.

This closes the deferred baseline check, not a new autonomous recovery trial.
The original cleanup timeout remains in the historical rehearsal result. The
[post-resume evidence](../eval/results/verified/20261014/post_resume_baseline_20261015.json)
is a separate dated snapshot, not an ongoing availability guarantee. Collector
startup recovery after Docker downtime was still manual at that check. The later
M0 changes below add reconnection; they do not retroactively change that record.

### M0 Reliability Slice (2026-10-15)

Implemented on `codex/evidence-baseline`: bounded Docker/Neo4j reconnection,
single-collector ownership per audit directory, separate heartbeat and evidence
freshness, versioned observation provenance, and guarded retries for pending
incidents. Invalid, expired, unknown or incomplete evidence cannot dispatch an
alert. A changed pending condition is held for review rather than retried under
its old diagnosis. General replanning remains M3 work.

The dashboard distinguishes stale/unknown observations from faults and refreshes
its status and live health sections every five seconds. Potential dependency
exposure is not labeled confirmed impact. Legacy report and heartbeat reading
remain compatible. The paper and the actual remediation policies were not changed.

**217 offline tests passed; three opt-in live tests skipped.** The three live
Neo4j checks passed separately. A supervised observe-only lifecycle check crossed
unavailable Docker and Neo4j client endpoints, reconnected to the real backends,
and recovered fresh healthy observations for all 12 services. It created no new
collector incident or RCA report. These were client-side connection failures,
not an actual Docker Desktop pause or database restart. No model call, injected
fault or repair was made.

The old idle collector was stopped and the updated collector left running in
**observe-only mode**, with automatic alert delivery disabled. The dashboard was
restarted to load the changed modules. This describes the verification session,
not a continuing availability promise. Source hashes, image identities, catalog
snapshot, timing and limitations are in the
[M0 development record](../eval/results/verified/20261015/README.md).

M0's first reliability slice is implemented. A repeat ten-minute observe-only
soak on October 16 passed all 121 samples, with the same collector process, all
twelve component statuses healthy and no new incident or report. The preceding
interrupted run failed (16/121 passing samples) and is retained unchanged. The
collector recovered fresh observations without a restart. Actual engine
pause/resume was explicitly skipped by the user, not passed. Both records and
their limits are in the [stability record](../eval/results/verified/20261016/README.md).
Lifecycle coverage remains partial. M1-M7 are not implemented by this change. The roadmap now also
includes M8: local-first Python distribution, supported-application onboarding
and a public website, after the research and transfer foundations.

### M1 First Observation Slice (2026-10-16)

M0's first reliability slice and both stability records are on `main` at
`725825a`. M1's first slice is implemented on `codex/symptom-observation`:
a shared versioned service registry, bounded local evidence store, and a separate
scheduled mock checkout monitor. Symptom episodes originate at frontend and
leave attribution empty. No model call, repair path, trace/log ingestion or
dynamic topology is added. Full M1 acceptance and dashboard integration remain
pending. See the [M1 operating guide](SYMPTOM_OBSERVATION.md).

**265 offline tests passed; three opt-in live checks were skipped and separately
passed against Neo4j.** The registry JSON was verified in a locally built wheel.
Two real healthy mock checkouts and cleanup succeeded with the M1 collector
recording observations for all twelve services. No model request, fault or repair
was made. The recorder initially failed on its final graph-summary operation;
the partial record is retained, and a separate read-only final check confirmed
all graph services healthy. See the [M1 evidence](../eval/results/verified/20261016/m1/README.md).

The collector was left running with delivery disabled and evidence recording
enabled. The transaction monitor completed two one-shot runs and stopped.
These are dated development observations, not ongoing availability promises.

### M1 Read-Only Investigation Evidence (2026-10-18 to 2026-10-19)

The symptom view displays failed checkout steps, episode references, freshness
and observed recovery without asserting an attributed root or autonomous repair.
One October 18 development trial produced two real checkout timeouts after a
payment-network partition. An independently observed frontend episode opened;
manual restoration and two successful transactions closed it. The observer was
not given the injected target. This is one development check, not causal accuracy
or an autonomous-repair success rate. Raw trial notes and artifacts remain local.

October 19 adds a read-only component reader and dashboard controls for registered
services and seven existing numeric measurements. Requests use fixed windows and
fields, not arbitrary queries or new probes. Missing/unknown readings, instance
changes, inconsistent provenance, stale observations and truncated history are
explicit. Historical views are measured-time views, not decision-time replays.
An import-isolation regression also removed eager settings loading from the
shared package while preserving its existing settings/logging exports.

**323 offline tests passed; three opt-in live checks were skipped and separately
passed against Neo4j.** A local read-only check found fresh healthy evidence for
all twelve services and samples for all seven metrics. Desktop and mobile
controls were checked. No new fault, model call or repair was made on October 19.
Automatic delivery remained disabled. These checks do not establish a new RCA
accuracy, MTTR, LLM benefit or production-safety result.

The first registry/monitor slice was already squashed into `main` at `5e9985a`.
The later dashboard and evidence-window changes preserve that implementation.
M1 remains incomplete: alternative-cause controls, sanitized log/trace adapters,
observed topology and investigation remain further work.

### Workspace Interface (2026-10-19)

The dashboard now uses native grouped sidebar navigation, a shared light theme,
compact runtime status, and an operational overview in place of the introductory
screen. Only the selected page renders. The overview combines current collector
observations with the existing reviewed topology fixture; it does not contact
Neo4j or discover dependencies. Graph-backed investigation remains in Dual graph
and Incident console. The graph views include zoom/fit controls; dependency nodes
use a stable hierarchy. Evidence freshness and observation-only delivery remain
explicit. The retired introductory illustration has been removed.

**329 offline tests passed; three opt-in graph checks skipped.** Desktop/mobile
checks cover navigation, nonblank graph canvases, node-label separation and graph
controls. Infrastructure was unavailable during this UI check, so no new live
graph acceptance, model inference, fault trial or repair was performed. This
interface work does not advance the M2 decision-loop acceptance gate. See the
[dashboard guide](../dashboard/README.md) and [capture provenance](images/README.md).

### Frontend Log-Signal Pilot (2026-10-21)

The optional one-shot scanner reads only an owned frontend at the reviewed image
reference. It projects JSON request failures into fixed operation categories and
recognized gRPC codes. Raw messages and request/session fields are not retained.
Identity, provenance, payload/line/byte limits, partial scans and missing Docker
are explicit. A separate read-only reader exposes retained batches; nothing is
connected to the model, alert delivery or executor by this increment.

**373 offline tests passed; three opt-in live graph checks skipped.** This
includes 44 new synthetic format, transport, identity, sanitization and storage
checks. The real CLI recorded unavailable Docker and read the same record back
from an isolated local evidence store. No live log-success, model request, fault,
repair or new accuracy/MTTR result is claimed. Source inspection used the pinned
v0.10.5 tag, not an assumed current format. Runtime image validation, broader
service/trace support, hard overall preemption, alternative-cause controls and
model-guided investigation remain open. See [adapter limits](SYMPTOM_OBSERVATION.md#sanitized-frontend-error-signals).

### Remaining Gates

1. Reproduce fresh isolated catalog initialization without deleting existing
   project volumes.
2. Repeat the now-demonstrated collector-to-API paths under frozen source and
   model settings, preserving every attempt and restoring the healthy baseline.
3. Cover supported fault families, fallback, invalid decisions, API failure,
   delivery/restart behavior, cleanup uncertainty and recurrence.
4. Freeze source/model/protocol and run repeated controlled comparisons plus a
   seeded autonomous campaign and healthy soak. Report failures and uncertainty.
5. Implement the bounded adaptive-investigation upgrades in
   [Agentic Scope](AGENTIC_SCOPE.md), then evaluate the LLM and dual graph against
   equal-information deterministic/flat alternatives.
6. Complete the separate review and implementation/results journal manuscripts
   using the [Journal Study Plan](JOURNAL_STUDY_PLAN.md), with step-by-step evidence,
   reviewed primary literature and reproducible figures. Update the thesis too.

Use the [evaluation protocol](EVALUATION_PROTOCOL.md) and
[research evidence matrix](RESEARCH_EVIDENCE_MATRIX.md) to bound claims. The
[archived review](archive/COMPLETION_PLAN.md) retains the original B01-B14 findings
and work-package plan; its pre-repair observations are not current defects.
