# Fresh-Evidence Shadow Replanning

This first M3 increment investigates successive **frozen failure-evidence epochs**
without changing the live agent, dashboard, Neo4j or SOP permissions. The replay
controller is in `eval/shadow_replan.py` because it consumes the existing frozen
input protocol, not a live collector. Shared budget enforcement is in
`agent/shadow_budget.py` and is optional in the existing broker/runner.

The initial replay increment used no external model request, physical fault or
repair. Later approved synthetic model development trials are recorded below.
Fixed/scripted/mocked-model checks are software controls, not causal accuracy,
measured model advantage or proof of live recovery.

## Incident And Epoch Contract

A trusted caller starts `ReplanningInvestigation` with an existing `FrozenInputs`
bundle. That bundle must be the first investigated epoch. The incident ID binds
the origin input digest and original frontend symptom ID. The original symptom
is preserved even when it ages past the normal 90-second trigger threshold.

Each later epoch needs its own recent failed transaction. Its trigger must be
the unique latest retained transaction, not an earlier failure followed by
healthy or unknown telemetry. A newer nonfailed/conflicting transaction blocks
the old failure from opening another diagnosis. This does **not** verify recovery.
The existing frozen format is failure-driven; all-healthy observation epochs and
independent application verification remain separate work.

The default permits at most four epoch attempts, including rejected refreshes;
the opt-in `two-epoch-v1` profile permits exactly two attempts. Cutoffs
must increase. Source kind and trace-transfer profile must remain fixed. A
previous evidence ID cannot identify changed payload, kind or retention time.
Old rows may disappear through pruning, but absence never means recovery.
A new cutoff alone is not new evidence: new retained IDs or a changed declared
topology are required. Changed edges need a new revision captured after the
previous cutoff. Snapshot validation and availability checks still apply.

Association across epochs is explicitly **operator-declared, not causal**. The
controller does not discover that two failures belong to the same incident. It
does not inspect injector targets or use expected labels to choose a service.
The existing policy and evidence screen make every new candidate decision.

## Candidate Lifecycle

| State | Meaning |
| --- | --- |
| `provisional` | This epoch's dependency screen and evidence broker accepted the candidate |
| `superseded` | A later admitted observation epoch replaced the prior evidence context |
| `expired` | Freshness, time, refresh rejection or epoch exhaustion withdrew the candidate |

Every refresh withdraws the prior candidate before another policy call. This is
conservative invalidation, not an optimized change detector. Rejected refreshes
also withdraw it. Old reports remain historical observations at their cutoffs;
they do not become current authorizations. Snapshots retire candidates when their
earliest required component observation exceeds its 30-second freshness, the
epoch's failed transaction exceeds 90 seconds, or a fatal incident guard stops
the investigation. Required healthy controls and upstream symptoms count, not
only the target's supporting citations. Unrelated services and older history
do not shorten the deadline. Provider waits consume this same lifetime; returning
a diagnosis never restarts it. A latched stop still withdraws the candidate if
the clock later returns to a valid reading.

An October 26 regression found that provisional candidates previously used only
their cited target observations for expiry. The controller now shares the
full-scope deadline calculation with consistency rechecks. This fixes currentness
bookkeeping, not model accuracy or recovery. Historical records are unchanged;
the revised source still needs a separately approved complete model validation.

New sessions do not inherit old issued citations. They must read evidence again
at the new cutoff and pass the unchanged dependency screen. An old observation
can be cited only if the current tool actually returns it and it remains valid.
Historical candidate summaries contain no reusable supporting IDs. A changed
runtime instance or condition requires current identified evidence; unknown,
conflicting and missing identity remains insufficient.

No candidate assigns an attributed root, approves a procedure or becomes a
`RESOLVED` incident. Every result keeps `execution_authorized=false`,
`causal_attribution=false` and `recovery_verified=false`. M4 must bind any future
action to its target, condition, identity, evidence version and preconditions.

## One Incident Allowance

With the unchanged `legacy` default, all epochs share the configured ceilings:
four decisions, eight diagnostic calls, 180 seconds, 128 KiB of cumulative evidence/continuation
context, 131,072 admission units and 8,192 reported tokens. Explicit pre-existing
profiles allow up to six decisions/360 seconds/32,768 reported tokens. Refreshes
do not multiply those limits. Byte envelopes/history remain additional overhead;
the prompt cap and per-response cap still apply.

Every invalid, failed or repeated diagnostic request consumes a call. Each
driver invocation consumes a decision and admission reservation before dispatch.
Reported usage accumulates across epochs. Unknown usage or interrupted in-flight
decisions block later attempts; measured overruns are retained rather than
inventing a billing guarantee. Admission is checked before provider rate waiting,
and waits consume the same deadline. Both monotonic processing time and elapsed
evidence-cutoff time count; advancing epochs cannot refund elapsed time.

**The full Boutique scope can exhaust all eight reads/four decisions in the
first legacy epoch.** The next epoch then stops and withdraws the old candidate.
Passing small-scope replay controls does not establish useful full-scope
replanning under that profile.

### Explicit Two-Epoch Research Profile

`two-epoch-v1` is a separate opt-in protocol, not an automatic retry or a limit
increase after failure. It allows two full investigations, with no caching,
new batch tool or free per-target work. It leaves the dependency screen,
component-read tools and non-execution boundary unchanged.

| Ceiling | Per epoch | Whole incident |
| --- | --- | --- |
| Diagnostic reads | 8 | 16 |
| Policy decisions | 4 | 8 |
| Cooperative elapsed time | 360 seconds | 720 seconds |
| Reported tokens | 32,768 | 65,536 |
| Evidence/continuation/profile context bytes | 131,072 | 262,144 |
| Admission units | 131,072 | 262,144 |

The 32,768-byte prompt and 65,536-byte tool-response caps remain unchanged.
Unused allowance is not a refund or permission to exceed a later epoch's cap.
Evidence-cutoff gaps and rate waits consume the whole-incident clock. Unknown
usage still blocks continuation. This is not a provider billing guarantee.

The profile and both sets of caps are recorded in the report and policy context.
Fixed and model policies receive the same facts, tools and ceilings. Each service
read is charged separately. A frozen full-scope fixed-policy control changes the
candidate from payment to checkout using 16 reads and eight decisions; the same
inputs under `legacy` retain the eight-read/four-decision stop. The increased
allowance is explicit cost headroom, **not an efficiency gain or model advantage**.
This profile alone does not establish live model-led recovery. A later targeted
synthetic model pass is recorded below; whole-profile acceptance remains open.

### Prompt Feasibility And Candidate Age

The two-epoch profile now uses a concise instruction protocol and lossless
factoring of repeated opaque provenance. `interned-strings-v1` expands into the
existing column-record/shared-metadata view. Service names, conditions, tool
arguments and citation IDs stay literal; they can be copied into native decision
fields without copying a reference object. Previous decision and read-request
examples also remain native objects, including inside encoded parent feedback.
Both policies decode the same facts.
The legacy/default structured protocol is unchanged. Tools, evidence screens,
individual-read charges and all limits remain unchanged.

An offline full-scope preflight initially needed a 9,498-token reservation,
including the output allowance and estimator margin, above the local 8,000-token
rate allowance. An intermediate encoding still needed 8,594. The concise
protocol and provenance-only encoding reduce the largest frozen fixed-policy
reservation to 7,953 in the final frozen preflight. These are local estimates,
not measured provider savings, guaranteed billing bounds or evidence of
diagnostic improvement.

Reasoning and rate waits also consume candidate freshness. A slow decision can
produce a screened historical hypothesis while leaving **no current candidate**.
The controller now anchors expiry before investigation work; completion does not
restart the evidence's freshness clock. A frozen-cutoff diagnosis is not live
readiness to execute. Fresh re-observation and M4 action binding remain required.

An additional opt-in [time-indexed evidence layout](TEMPORAL_DIAGNOSIS.md) marks
latest samples separately from retained history, without changing the default
layout or choosing a target. Offline parity/feasibility and post-run assessment
commands keep presentation, diagnostic adherence, guard stops and freshness
separate. They do not establish a new model acceptance.

The component reader also treats an omitted sample tied at the latest timestamp
as uncertain. The 100-sample response limit must not hide a possibly conflicting
state or runtime identity. Truncation of older history alone does not invalidate
an unambiguous latest sample.

Accounting is locked and process-local, not a durable incident ledger, OS sandbox
or hard cancellation mechanism. A trusted process can create another controller.
Blocked I/O is checked cooperatively. M5 must address restart-wide enforcement,
credential isolation and unknown operation effects before adaptive live repair.

### Opt-In Bounded Coverage

`bounded-coverage-v1` addresses two observed failure modes: stopping while
required services are unread, and spending all four decisions on reads plus a
single wrong proposal. It is a different orchestration protocol, not another
evidence layout or an increase in the existing limits. `model-directed-v1`
remains the default. Neither is enabled for adaptive live repairs.

The model makes the first decision. After a valid read or a deferred unsupported
terminal decision, a trusted read-only stage can complete the remaining mandatory
scope. Scope comes from the failed workflow anchor and declared dependencies,
not the injected fault or expected result. Every service read is individually
charged through the same broker, byte limits and deadline. These reads appear
as `coverage-controller` turns, not fabricated model decisions.

The controller supplies no candidate. The model must still choose a registered
target, condition and current citations, or give an evidence-supported abstention.
A wrong proposal receives the same screen critique; another policy decision is
required to correct it. A repeated wrong proposal never becomes a substituted
fixed-policy answer. Ungrounded abstentions can also receive a critique within
the remaining decision allowance. A genuine unavailable or uncertain observation
still permits immediate safe abstention without repeating a failed read.

If the remaining scope cannot fit the tool budget, or time/bytes run out,
completion stops rather than extending the allowance. Native schemas do not
advertise more reads after that allowance is exhausted. Candidate freshness still
starts before investigation, so provider rate waits can leave an accepted frozen
hypothesis expired and unusable for action.

Both fixed and model arms must explicitly select this same protocol in a matched
comparison. It is **hybrid orchestration**, not evidence that an unrestricted
model independently planned every diagnostic read. Software controls, real-model
regressions, held-out evaluation and live action readiness remain separate gates.

### Bounded Coverage Development Results

On October 25, a separately approved frozen batch tested this scheduling mode
with real Groq `openai/gpt-oss-120b` calls, low reasoning effort, native schemas,
client cache disabled and no SDK retries. Expected labels stayed outside policy
inputs. Every diagnostic read remained charged; no target was supplied by the
coverage stage. A second approval covered only the unknown-telemetry regression
after its schema-bound fix. All previous failed batches remain recorded.

| Development check | Provider calls | Component reads | Outcome | Reported tokens |
| --- | ---: | ---: | --- | ---: |
| Payment with degraded checkout | 2 | 8 | Model selected payment; screen passed | 8,248 |
| Checkout after payment recovered | 2 | 8 | Model switched to latest checkout evidence; screen passed | 8,855 |
| Redis with upstream symptoms | 2 | 8 | Model selected Redis resource pressure; screen passed | 8,561 |
| Unknown telemetry, first revision | 2 | 6 | Oversized batch stopped at the broker; failed control | 7,407 |
| Independent payment/currency failures | 3 | 8 | Proposal blocked; model then explicitly abstained | 14,542 |
| Unknown telemetry, corrected bound | 3 | 8 | Observed unknown sample; explicit supported abstention | 12,186 |

The first five rows are one 11-request batch: **its complete acceptance failed**
because unknown telemetry ended in a guard fallback. The last row is a separate
targeted regression, not a replacement result. Native `maxItems` now reflects
the actual remaining allowance, rather than advertising three requests when only
two remain. The local broker still independently enforces its limit.
The first batch used 47,613 provider-reported tokens; the regression used 12,186.
All 14 returned receipts reconcile with the recorded attempts. Unused approval
ceilings are closed, not permission for further runs.

The independent-failure case provides an actual correction trace: the model first
proposed currency while listing payment as unresolved; the screen blocked it,
and a further bounded model decision chose `unresolved_alternatives`. The
controller did not replace its answer. In each positive check, the model requested
three reads and the trusted stage supplied five more. This demonstrates the
hybrid protocol on constructed data, not fully model-planned acquisition.

Post-run review also tightened shared-dependency abstention support: separate
callers are not independent explanations when they share a failed or unknown
dependency. A legacy context-shape compatibility fix preserves the old adapter
default. A zero-call replay retained all recorded decisions and outcomes under
the final implementation; it is not new inference or a new freshness result.
Neither frozen implementation was edited while its provider calls ran.

The suite now passes 1,200 offline tests with three opt-in live checks skipped.
These reused synthetic cases are not held-out causal accuracy, a comparison
against prior research, a cost advantage or an MTTR measurement. Provider waits
expired the positive candidates; no current authorization, physical action or
recovery resulted. Repeated/held-out diagnosis and fresh live observation/action
binding remain required before M2/M3 acceptance or an adaptive repair deployment.

### Reliability Gate Revalidation

A subsequent, separately approved frozen batch used the same constructed
development cases and the final `dependency-candidate-v2` implementation at
`fe62f32`. All 12 requests returned valid decisions with known usage, no SDK
retry and no source change during dispatch. Its **complete acceptance failed**.

| Development check | Provider calls | Component reads | Outcome | Reported tokens |
| --- | ---: | ---: | --- | ---: |
| Payment with degraded checkout | 2 | 8 | Payment selected; screen passed | 8,226 |
| Checkout after payment recovered | 2 | 8 | Latest checkout selected; screen passed | 8,855 |
| Redis with upstream symptoms | 2 | 8 | Redis resource pressure selected; screen passed | 8,602 |
| Unknown telemetry | 2 | 6 | Observed unknown; explicit supported abstention | 7,307 |
| Independent payment/currency faults | 4 | 8 | Three proposals blocked; decision allowance exhausted | 20,949 |

The batch used 53,939 provider-reported tokens. The independent case repeatedly
proposed currency while listing payment as unresolved. Every proposal was
blocked; the final guard fallback is not an explicit diagnostic abstention or
a passed control. All outcomes remain recorded. The unused eight-request ceiling
closed at the end of this batch.

The compact instruction had said independent branches "require
unresolved_alternatives", without distinguishing the proposal field from the
abstention reason. It replaced the longer prompt that made this distinction.
`dependency-candidate-v3` now gives the explicit abstain-action envelope, explains
that listing alternatives in a proposal does not abstain, and makes rejection
feedback distinguish a genuine alternative from mistaken metadata. Native field
descriptions agree. The same dependency screen and budgets remain authoritative;
no target is supplied, no alternative is erased and no action is forced.

A separate four-call approval then covered one newly frozen independent-fault
regression under v3. It used **two fresh Groq calls and 8,762 reported tokens**.
After three model-requested reads and five mandatory controller reads, the model
explicitly abstained with `unresolved_alternatives`; the evidence-support check
passed. Unused capacity closed. Source snapshots, dispatch receipts, prompt
hashes and usage reconcile in both runs. **This targeted pass does not replace
the failed full batch or establish final-profile accuracy.**

The v3 suite passes 1,249 offline tests with three opt-in live checks skipped.
Zero-call replay retains the original failed decisions and outcomes. Tested
fixed and two-rejection prompt plans fit the existing 8,000-token local rate
allowance; the largest reservation is 7,977, leaving little margin. This is not
a guarantee for other read plans or a measured efficiency advantage. No complete
fresh v3 model batch, held-out study, current-evidence recheck or physical recovery
was run. The positive v2 proposals expired during provider waits. M2/M3 and the
main-promotion gate remain open; automatic incident delivery remains disabled.

### Complete v3 Attempt: Provider Blocked

A separate approval covered one complete, unchanged v3 batch at `ae8ba54`, with
at most 20 requests. The first request returned a valid read decision; eight
component reads completed. The second request returned HTTP 429 before a terminal
diagnosis. The failed request had no usage report. Only **2,802 tokens from the
successful request** are known; this is not a complete token or billing total.

The checkout continuation was rejected by the incident's unknown-usage guard.
The run was then interrupted during the next case's admission wait, before its
SDK dispatch, to avoid more requests against the provider limit. Redis, unknown
telemetry and independent faults were **not run**, not diagnosed incorrectly.
Both dispatch receipts, the error, partial controller records and source hashes
remain retained. The unused 18-request allowance is closed; there was no retry.
This is an interrupted, provider-blocked batch, not a diagnostic acceptance or
an accuracy measurement. Earlier failed batches and the targeted v3 pass remain
separate. The original error metadata does not establish which rate window ran out.

Subsequent offline hardening latches HTTP 429 for a shared `GroqShadowDriver`.
Later investigations using that same instance stop at admission without another
SDK attempt. Direct calls through the latched driver are blocked too. The stop
does not expire automatically, change quotas, retry, switch models or confer
repair authority. It is process-local and does not coordinate separate clients,
processes or already in-flight requests.

Future 429 diagnostics retain only allowlisted numeric counters, bounded retry
seconds and formatted reset durations. Arbitrary headers, cookies, request IDs
and vendor text are omitted. Groq documents request counters as per-day and token
counters as per-minute; local pacing alone does not establish organization-wide
quota availability. [Groq rate-limit documentation](https://console.groq.com/docs/rate-limits)
defines those headers; no historical values were reconstructed or inferred.
These changes pass 1,281 offline tests with three opt-in live checks skipped,
including mocked HTTP evidence that a second investigation makes no new request.
No additional real provider trial was authorized or run after the interruption.

Separately, a read-only live baseline found all 12 components healthy, Neo4j
connected, and API/dashboard/storefront HTTP 200. Four retained-evidence samples
over 30 seconds showed fresh, identified, advancing observations with delivery
disabled. No transaction evidence was initially retained in the 15-minute view.
One existing shadow-only canary then passed home, cart insertion, cart read,
mock checkout and own-cart cleanup, and stored a healthy observation. The monitor
exited after `--once`; this does not establish continuous symptom monitoring,
failure detection, a current candidate recheck or incident recovery. M1/M2/M3
acceptance and main promotion remain open.

### Final-Source Development Batch

On October 26, a separately approved complete batch ran once at `c1db152`, with
the same v3 contract, frozen source, unchanged budgets and reused synthetic
development inputs. It made **13 fresh Groq calls and used 60,973 reported
tokens**. All replies had known usage; source, dispatch, prompt and scoring
records reconciled before any implementation change. **The whole batch failed.**

| Frozen epoch control | Calls | Reads | Outcome | Reported tokens |
| --- | ---: | ---: | --- | ---: |
| Payment failure | 2 | 8 | Payment candidate screened | 8,525 |
| Checkout after payment recovered | 4 | 8 | Three superseded-payment proposals blocked; decision allowance exhausted | 22,431 |
| Redis resource pressure | 2 | 8 | Redis candidate screened | 9,055 |
| Unknown telemetry | 3 | 8 | Explicit supported insufficient-evidence abstention | 12,262 |
| Independent faults | 2 | 8 | Explicit supported unresolved-alternatives abstention | 8,700 |

Checkout's final guard fallback is not a model abstention or a passed control.
The model repeatedly cited the earlier payment failure although the latest
payment observation was healthy. The old sample was still within the time
window but had been superseded. The local screen blocked every such proposal.
The native schema, however, still enumerated all displayed observation IDs,
including historical ones and the transaction symptom. This is a contributing
contract gap, not evidence that the model has learned to replan correctly.

A subsequent offline change aligns native proposal citations with the existing
latest-evidence rule. For a dependency-screened proposal, citations are limited
to the latest fresh, known, identified observations across the required scope.
Healthy and anomalous observations both remain eligible; service and condition
choices are unchanged. No target is supplied or substituted. Missing/uncertain
scope cannot build a proposal schema, and the unchanged local screen still
blocks a wrong target even if it cites a latest observation.

The three recorded bad responses fail the tightened schema in zero-call replay.
This is software regression coverage, **not new model inference or a verified
relocalization fix**. The original batch remains failed, with its unused seven
calls closed. No extra case, hidden retry, live evidence, consistency recheck,
repair or recovery was added. Positive candidates expired; these reused controls
do not establish held-out causal accuracy, M2/M3 completion or main promotion.

### Targeted Current-Citation Regression

A separate eight-call approval covered one newly frozen payment-to-checkout
regression at `b182930`. It used **four fresh Groq calls and 17,218 reported
tokens**, with caching and SDK retries disabled. No implementation changed while
the run was in flight. Both frozen diagnostic controls passed:

| Epoch | Latest observed state | Model-selected candidate | Calls | Reads | Reported tokens |
| --- | --- | --- | ---: | ---: | ---: |
| First | Payment down; checkout degraded | Payment readiness failure | 2 | 8 | 8,421 |
| Second | Payment healthy; checkout still degraded | Checkout readiness failure | 2 | 8 | 8,797 |

The second proposal cited the latest checkout observation, not the superseded
payment failure. Each epoch used three model-requested reads plus five mandatory
controller reads; the controller supplied no target. The unchanged local screen
accepted both proposals. All source, input/control, dispatch, prompt and usage
records reconciled. There were no provider errors or extra calls; unused capacity
closed at four calls. No previous allowance was reused.

This is a **targeted synthetic development pass**, not a complete final-source
batch, held-out accuracy, causal proof or live recovery. The earlier 13-call full
batch remains failed and must not be pooled with this result. Both candidates
expired during provider waits; no current-evidence consistency receipt, execution
authority, procedure, repair or recovery was produced. Repeated same-source
controls, negative/held-out checks and live acceptance remain open. Main is not
promoted on this targeted pass.

### Complete Same-Source Citation Validation

A new, separately approved complete batch ran once on October 26 at `3567049`.
That revision changes documentation only; its application-source manifest is
identical to the citation fix at `b182930` and the targeted regression above.
All four scheduled cases, comprising five epoch controls, passed in this batch.
It used **11 fresh Groq calls and 46,951 reported tokens**:

| Frozen epoch control | Explicit model result | Calls | Reads | Reported tokens |
| --- | --- | ---: | ---: | ---: |
| Payment failure | Screened payment readiness failure | 2 | 8 | 8,345 |
| Checkout after payment recovered | Screened checkout readiness failure using its latest observation | 2 | 8 | 8,773 |
| Redis resource pressure | Screened Redis resource pressure | 2 | 8 | 8,767 |
| Unknown telemetry | Supported insufficient-evidence abstention | 3 | 8 | 12,428 |
| Independent faults | Supported unresolved-alternatives abstention | 2 | 8 | 8,638 |

All 40 component reads were charged: 17 model-requested and 23 mandatory
controller reads. The controller supplied no target. There were no provider
errors, unknown usage, retries, extra cases or source/prompt changes in flight.
Before any documentation edit, the audit reconciled frozen source archives,
input/control digests, all dispatch/return receipts, prompt hashes, scores and
usage totals. The separate 20-call approval is closed; unused nine calls closed
without reuse. The older failed batch and targeted result remain separate.

This passes the **complete synthetic development regression gate on this source**,
not a held-out accuracy study, repeated full-profile reliability or live recovery.
The reused cases helped develop the implementation. Positive candidates expired
during provider waits; no current-evidence consistency receipt, execution grant,
repair or physical recovery was produced. Repeated/held-out checks, live evidence
acceptance and guarded execution remain required. M1/M2/M3 are not signed off,
and main is not promoted on this result alone.

The subsequent [offline campaign tooling](SHADOW_CAMPAIGNS.md) prepares repeated
synthetic schedules and assesses supplied transcripts without dispatching a
model. Its preflight runs are not new model results or independent held-out
evidence; they do not change the acceptance scope of the batch above.

### Explicit Candidate Consistency Recheck

The optional library method `ReplanningInvestigation.recheck_candidate` checks a
previously screened candidate against a new frozen evidence bundle. It makes no
model call, runs no probe or repair, and does not revive the old proposal.
`current_candidate` stays empty once that proposal expires or is withdrawn.
A successful result is recorded separately as `current_consistency_check`, with
`execution_authorized: false`. Synthetic input produces only a synthetic check,
not a statement about the current running application.

This is a separately declared verification stage: **at most eight component reads
and ten seconds, once per latest candidate**. Its reads and bytes are recorded
separately from diagnosis costs. It does not add tools to a diagnosis epoch or
refund an earlier call. Both elapsed wall time and evidence-cutoff progression
still count against the original incident deadline. Limits are cooperative, not
hard interruption of blocked I/O. An interrupted check retains already charged
read attempts and cannot leave a current receipt.

Every scoped service needs a genuinely newer observation, not just a new ID on
old data. The latest failed transaction must also be new and fail the same steps.
The environment, input profile, topology source/revision/edges, component states
and runtime instance identities must match the original investigation. Missing,
unknown, conflicting, recovered or changed state rejects the check. A recovered
payment service with checkout still failing therefore requires new reasoning;
the verifier never substitutes checkout as a target. Recheck observations remain
identity-bound and may then be used by a new diagnosis epoch without collecting
another artificial refresh. They are not considered already diagnosed merely
because a verifier read them.

The receipt expires at the earliest scoped component freshness boundary or
transaction boundary. A new epoch, failed refresh, stopped incident or repeated
check request withdraws it. Even a consistent result establishes only unchanged
declared-scope health evidence. It is not causal proof, a concrete SOP condition,
a current catalog/runtime preflight, an execution grant or verified recovery.
No live agent or default CLI path invokes this method automatically. Offline
controls cover these rules; live evidence collection, fresh model acceptance and
action binding remain separate, open gates.

## Offline Operator Command

Prepare one to four independently frozen inputs with increasing cutoffs. Keep
expected labels in a separate manifest, outside those input bundles:

```bash
python -m eval.shadow_replan \
  --inputs output/shadow/incident/epoch-001.json \
  --inputs output/shadow/incident/epoch-002.json \
  --output output/shadow/incident/replanning.json
```

The CLI runs only the strong fixed policy and never loads settings or constructs
a provider. It creates owner-only exclusive start/result files. A later input
failure preserves earlier records, withdraws the current candidate and returns
a fixed error category without raw validation text. The start marker prevents
accidental same-output reuse; it is not resumable durable accounting. A killed
process can still lose in-memory trace data. Keep these artifacts Git-ignored.

To use the explicit research profile, supply exactly two frozen inputs and add
`--profile two-epoch-v1` to this command. Omitting it retains `legacy`; neither
profile switches to the other after an outcome.
To opt into coverage scheduling, also supply
`--investigation-protocol bounded-coverage-v1`. The default schedule is not
silently changed. Reports and assessment controls bind this protocol separately
from the budget profile and evidence layout.

The library accepts an explicitly supplied model driver only with boolean
external-transfer approval on each call; version 2 trace context also needs
separate trace-transfer approval. These checks do not replace human approval
of a finite experiment. The dated trials below used separate finite approval;
their unused allowances do not authorize another batch.

## Synthetic Model Development Record

Three separately approved batches ran on October 24 with Groq-hosted
`openai/gpt-oss-120b`, native structured responses, low reasoning effort, no cache
and zero SDK retries. Inputs, diagnostic controls, implementation hashes and
limits were frozen before each batch. Expected answers stayed outside the policy
context. The fixed policy received the same frozen facts, tools and caps; later
derived history and elapsed time could differ between policies.

| Batch | Constructed cases | Actual provider requests | Diagnostic outcome |
| --- | --- | --- | --- |
| Initial two-case batch | Payment-to-checkout; unknown continuation | 6 | Neither passed; first epochs encountered schema errors, and unknown usage blocked continuation |
| Corrected two-case batch | Same continuation types on a new frozen revision | 6 | Neither passed; payment abstained early, and the other case encountered a malformed read request |
| Final one-case regression | Payment-to-checkout only | 8 | All eight responses were schema-valid, but both terminal proposals were blocked; the case failed |

The final regression read all eight scoped services in each epoch. Its first
proposal named checkout despite an observed downstream payment failure; the
screen returned `downstream_anomaly_unresolved`. Its second proposal named
checkout but declared an unresolved payment alternative; the screen returned
`declared_unresolved_evidence`. Both epochs then exhausted their four decisions
and terminated without a candidate. No response was replaced with the expected
answer, no screen was weakened and no extra correction call was added.

All five constructed cases failed their two-epoch diagnostic controls across
20 actual provider requests. These are related development cases, not independent
incidents, a held-out accuracy estimate or a model comparison. Earlier failures
and zero-call preflights are retained separately in private research artifacts.
The last run's valid transport does not establish general schema reliability.
At that stage there was no accepted real-model two-epoch replan, action or
physical recovery from this path. These failures remain unchanged and are
separate from the later targeted current-citation pass above and from the
earlier single-epoch development acceptances.

A separate approved pair on October 25 used 16 actual requests, eight for each
layout, on the same historical payment-to-checkout case. Both named checkout in
both epochs. First-epoch proposals were blocked; second-epoch proposals passed
their frozen controls but expired during processing. Both full arms therefore
failed, with no demonstrated relocalization or temporal-layout benefit. All
16 responses were schema-valid, with 68,392 provider-reported tokens in total.
The [paired comparison record](TEMPORAL_DIAGNOSIS.md#approved-model-comparison-october-25)
retains this partial success separately from full-control failure, live freshness
and the five earlier failed cases. No action or extra trial ran.

## Acceptance Still Required

Offline checks cover recovered payment with failing checkout, HIGH_CPU changing
to DEGRADED, independent alternatives, unknown/conflicting observations, runtime
replacement, immutable evidence, stale citations, duplicate refreshes, global
budgets, interrupted/unknown usage, partial CLI output and matched deterministic
input contexts. Mocked native-model tests make no network requests.

Initial October 24 verification: 68 new replanning tests and 896 total tests pass; three
opt-in live checks were skipped. A separately frozen three-case fixed-policy
walkthrough retained both candidate changes and the full-scope budget stop.
It made zero external model calls and no Docker or repair operation.

The two-epoch extension adds 30 offline controls, bringing the suite to 926
passing tests with three opt-in live checks skipped. These cover explicit caps, matched
contexts, target changes, abstentions, per-epoch/incident limits, unknown usage,
latched stops and latest-sample truncation. These are software checks, not
independent incident samples or accuracy measurements. A separately frozen
four-continuation/eight-run comparison retains the legacy stops alongside the
new profile's target change and three explicit abstentions. The continuations
share a constructed origin; they are not independent real incident samples.

Subsequent prompt-encoding/freshness controls add 16 offline tests. Together with
128 procedure-contract tests, the combined suite passes 1,070 tests, with three
opt-in live checks skipped. Those three checks separately passed against real
Neo4j. These are software controls, not model accuracy; the failed model trials
above remain separate from the passing suite and fixed-policy walkthroughs.

This does not complete M1 live telemetry, M2 held-out/real-incident diagnosis or
M3 live preflight/relocalization. No live model-led refresh, procedure retrieval,
sandbox action, compensation or verified recovery was added. The original
fixed-catalog remediation workflow remains separate. See the
[implementation roadmap](IMPLEMENTATION_ROADMAP.md#m3-replan-from-fresh-observations).
