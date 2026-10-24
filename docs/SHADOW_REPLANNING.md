# Fresh-Evidence Shadow Replanning

This first M3 increment investigates successive **frozen failure-evidence epochs**
without changing the live agent, dashboard, Neo4j or SOP permissions. The replay
controller is in `eval/shadow_replan.py` because it consumes the existing frozen
input protocol, not a live collector. Shared budget enforcement is in
`agent/shadow_budget.py` and is optional in the existing broker/runner.

No fresh external model request, physical fault or repair was run for this
increment. Fixed/scripted/mocked-model checks are software controls, not causal
accuracy, measured model advantage or proof of live recovery.

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

At most four epoch attempts are allowed, including rejected refreshes. Cutoffs
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
supporting component evidence exceeds its 30-second freshness or time expires.

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

All epochs share the existing configured ceilings: default four decisions,
eight diagnostic calls, 180 seconds, 128 KiB of cumulative evidence/continuation
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
first epoch.** The next epoch then stops and withdraws the old candidate. Passing
small-scope replay controls does not establish useful full-scope replanning under
this profile. A future protocol must justify and evaluate caching, probe/scope
policy or changed budgets; this increment does not raise limits to hide the stop.

Accounting is locked and process-local, not a durable incident ledger, OS sandbox
or hard cancellation mechanism. A trusted process can create another controller.
Blocked I/O is checked cooperatively. M5 must address restart-wide enforcement,
credential isolation and unknown operation effects before adaptive live repair.

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

The library accepts an explicitly supplied model driver only with boolean
external-transfer approval on each call; version 2 trace context also needs
separate trace-transfer approval. These checks do not replace human approval
of a finite experiment. No such new trial is authorized or established here.

## Acceptance Still Required

Offline checks cover recovered payment with failing checkout, HIGH_CPU changing
to DEGRADED, independent alternatives, unknown/conflicting observations, runtime
replacement, immutable evidence, stale citations, duplicate refreshes, global
budgets, interrupted/unknown usage, partial CLI output and matched deterministic
input contexts. Mocked native-model tests make no network requests.

October 24 verification: 68 new replanning tests and 896 total tests pass; three
opt-in live checks were skipped. A separately frozen three-case fixed-policy
walkthrough retained both candidate changes and the full-scope budget stop.
It made zero external model calls and no Docker or repair operation.

This does not complete M1 live telemetry, M2 held-out/real-incident diagnosis or
M3 live preflight/relocalization. No live model-led refresh, procedure retrieval,
sandbox action, compensation or verified recovery was added. The original
fixed-catalog remediation workflow remains separate. See the
[implementation roadmap](IMPLEMENTATION_ROADMAP.md#m3-replan-from-fresh-observations).
