# Explicit Diagnostic Continuation

The persistent diagnostic path can now start a **new evidence epoch** from a
completed checkpoint. It does not replay the saved graph, repeat a model call,
restore an execution grant or resume a repair. Every continuation needs explicit
operator approval and newer immutable inputs. Returned candidates remain data;
the wrapper returns no live controller or procedure capability.

This extends [diagnostic checkpoints](WORKFLOW_CHECKPOINTS.md), not the legacy
incident worker. No automatic delivery, API route or dashboard repair control is
enabled by this increment.

## Eligibility

All conditions must pass before the parent checkpoint is claimed:

- The parent has a terminal native checkpoint and an immutable attached controller
  receipt. Older history-only checkpoints lack that attachment and cannot be
  upgraded by guessing the missing state.
- Native decisions, report, context and budget agree with the attached receipt.
  Original inputs and current source/dependency fingerprints match. Registry,
  profile, limits, layout and investigation protocol cannot change on restart.
- Usage is known, no decision is in flight, the incident is not stopped, and
  epoch/read/decision/token/time allowances remain. Unknown usage is not zero.
- The new cutoff advances, the latest failed transaction is the selected trigger,
  and there is new evidence or a valid topology revision. Previously seen IDs
  cannot acquire different contents. Real retained captures must be at most
  30 seconds old; synthetic frozen controls remain explicitly offline.
- The same existing operation journal is available. Its file identity must match
  the saved binding. An open deployment barrier, or any operation history for
  this incident or its original trigger (even closed history), requires separate
  review instead of continuation through this path.
- A model driver receives fresh explicit transfer approval. Approval recorded for
  a prior epoch is never inherited; trace-bearing inputs retain their separate
  transfer gate. The supplied CLI uses only the fixed policy and makes no model
  requests.

The old candidate becomes expired with reason `process_restart`. Old consistency
checks, procedure offers, selections and grants are not reconstructed. Fresh
diagnostics can still abstain when new evidence is unknown or ambiguous.

## Accounting And Ownership

Completed counters carry forward: evidence reads, response bytes, decisions,
admission units and reported tokens. The restored timer includes elapsed time
and downtime, using the earlier native completion/capture time conservatively.
The next cutoff can consume additional time; restart never adds allowance.
Backward/future timestamps or exhausted limits refuse continuation.

The saved budget's `durable=false` still has its original meaning: it is not a
per-call durable billing ledger. Only completed, known history can be reconstructed.
If a call may have occurred without a complete record, this path stops. It does
not estimate missing usage and then spend the difference.

The operation journal's writer lock covers the entire diagnostic epoch, excluding
cooperating controlled mutations. Its identity/barrier/history are checked before
provider/policy calls and before recording the result. The diagnostic store
commits one parent-to-child claim before entering the child graph. Each parent
can have one child; the child's trigger must also be new. A failure after this
claim does not refund it automatically, even if the graph never started.

Native graph completion and controller-receipt attachment remain separate commits.
Missing attachments, lost acknowledgements and interrupted child records require
inspection. They are not evidence that no request happened. There are no hidden
retries, extra cases or automatic polling loops.

## Offline Commands

These commands use frozen inputs and the fixed diagnostic policy. They create
private local bookkeeping but make no Groq call, Docker request, transaction,
fault or repair. Each output path must be new.

Start one epoch with literal approval:

```bash
python -m simulation.diagnostic_continuation start \
  --origin PRIVATE_ORIGIN.json \
  --workflow-database audit/diagnostic_workflows.sqlite \
  --operation-journal audit/controlled_operations.sqlite \
  --output NEW_START_REPORT.json --approve-start
```

An explicit start can initialize a missing operation journal. That initialization
does not certify a healthy application or account for legacy/external operations.
Continuation cannot recreate a missing journal.

Use the returned thread ID with newer evidence:

```bash
python -m simulation.diagnostic_continuation continue \
  --origin PRIVATE_ORIGIN.json --fresh PRIVATE_NEW_EPOCH.json \
  --parent-thread WF_ID \
  --workflow-database audit/diagnostic_workflows.sqlite \
  --operation-journal audit/controlled_operations.sqlite \
  --output NEW_CONTINUATION_REPORT.json --approve-continuation
```

Inspect history and any recorded child claim:

```bash
python -m simulation.workflow_review --database audit/diagnostic_workflows.sqlite
```

`continuation_record.present` means an attachment exists, **not** that continuation
is currently eligible. A recorded child may have no graph row if the process died
between claiming it and graph admission. Inspect that uncertainty; do not delete
the claim or journal to repeat the work. CLI failure returns nonzero and retains
an exclusive start marker/private receipt where possible.

Library callers use `start_diagnosis` or `continue_diagnosis` in
`agent/diagnostic_continuation.py`, supplying the configured stores, immutable
inputs and trusted driver. Profiles can be selected for a new incident, not
expanded while continuing an old one.

## Evidence And Limits

The increment adds 58 offline software controls. They cover counter preservation,
multi-epoch retirement, downtime/clock refusal, changed source/evidence, operation
barriers/history, missing journals, unknown usage, altered receipts, transfer
refusal, actual before/after-commit failures and client-free CLI imports. Two real
child-process exits stop after a child claim or inside a synthetic decision;
a separate process also completes a fresh epoch with the original spent counts.
These are not live provider failures, physical recoveries or diagnostic-accuracy
samples.
The complete offline suite passed 1,997 tests with three opt-in live checks
skipped. Whole-tree F-lint, whitespace and documentation validation also passed.
A private start/continue CLI walkthrough retained both processes' results and a
separate duplicate-refusal control, with zero model calls or repairs.

The guarantee is local and cooperative, using the same protected store and
journal. Other paths, database copies, hostile filesystem writes, distributed
workers and external Docker clients are not fenced. Lock ownership is not a
transaction with a provider or Docker. Source/receipt hashes are not attestations.

M5 remains incomplete. Interrupted provider calls and uncertain operations cannot
resume here. Procedure execution after a resumed investigation, orphan cleanup,
worker/dashboard integration and separately approved live acceptance remain
open. The earlier physical payment trial predates this source. No new live
model call, Docker access, fault, checkout or repair was run for this increment.
