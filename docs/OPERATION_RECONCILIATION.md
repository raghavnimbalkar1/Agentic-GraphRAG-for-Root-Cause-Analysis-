# Passive Controlled-Attempt Reconciliation

This operator-only path reviews the **current state**, not the success of an
interrupted operation. It cannot prove that a restart happened, attribute recovery
to a procedure, exclude a late external effect or authorize another action. It
never invokes a model, executes a procedure, sends a checkout request, creates a
health-probe container or cleans up a container.

The [operation journal](OPERATION_JOURNAL.md) retains the original action history
and its deployment barrier. A passing preview keeps that barrier. A separate,
explicit closure command runs a new assessment under the writer lock and may
release only that barrier. The old incident and original trigger remain
non-replayable, including after closure. Automatic delivery is not enabled.

## Write-Ahead Identity

New controlled grants use `operator-controlled-v2`. Before Docker client creation,
the journal retains the controller's declared topology, workflow anchors, original
failed transaction, required service scope, instance IDs, component evidence IDs
and procedure durability. Before request-container creation, another commit
records its exact name, nonce, image ID and role. Disk latency cannot extend the
grant or request timeout. Failed reservation writes prevent container creation.

This ordering makes absence of a request-container reservation meaningful for
the v2 path: its create call was not admitted. A recorded reservation permits
inspection of that exact name. A present container or conflicting identity blocks
closure; this command does not remove either. A later observation of absence does
not rewrite an earlier uncertain cleanup result as a successful cleanup.

Old v1 attempts lack this provenance. Reconciliation refuses them rather than
inventing missing scope or container identity. Their barrier remains for separate
manual review; do not delete the journal to bypass it.

## Assessment Requirements

All conditions must pass twice within one cooperative 15-second assessment:

- The exact open journal version is eligible, no writer owns the lock, and fewer
  than three explicit reconciliation attempts have been recorded.
- At least 120 seconds have elapsed since the last execution or verification
  journal record. This is an **age guard**, not a continuously observed healthy
  window, a distributed fence or proof that an external operation cannot finish
  later. Read-only review events do not reset that origin.
- The procedure is not temporary containment. Mitigation needs a separate policy
  review; healthy samples alone cannot turn CPU containment into a durable fix.
- The request sandbox is absent by exact reserved identity, or v2 contains no
  request-create admission. No unrelated container is inspected for cleanup.
- Every scoped service remains the same owned, running, unpaused instance with
  an expected network endpoint. Image, network, start time and CPU quota remain
  stable across the two inspections.
- The collector is fresh, sensing-ready, graph-synchronized and observe-only.
  Declared topology and procedure/implementation digests match the retained
  admission basis. Scoped graph status is fresh and healthy.
- Each scoped service has at least two distinct retained healthy component
  measurements, at least one second apart, within 30 seconds and after the age
  guard. IDs must be new, instances must match, and conflicting, truncated,
  future, stale or unknown data cannot pass.
- At least two existing healthy checkout transactions have distinct identities
  and times, start after the age guard and finish within 90 seconds. The schema's
  full workflow and cleanup checks must pass, including cart/checkout markers.
  Recent failed or unknown peers block acceptance. No new checkout is sent to
  fill a missing witness.
- Runtime instances started before the selected healthy transactions. Retained
  evidence remains fresh after both inspections and resource closure, and just
  before the terminal journal write. Inspection/context receipts expire after
  five seconds. A journal commit is not atomic with external service state.

The deadline is cooperative. An SDK call can exceed it before returning; that
result is rejected, not cancelled at the remote daemon. Clock discontinuity,
resource-close uncertainty, unavailable evidence or journal-write uncertainty
cannot produce an accepted closure response.

## Operator Commands

First inspect the journal. This command performs no runtime or provider calls:

```bash
python -m simulation.operation_review show --include-receipt
```

Use its exact attempt ID and current version for a passive preview:

```bash
python -m simulation.operation_review reconcile ATT_ID \
  --version CURRENT_VERSION --approve-runtime-inspection
```

Both successful and inconclusive reviews append history and change the version.
A successful preview records `reconciled_observed_healthy`; an incomplete review
records `reconciliation_inconclusive`. Both retain the barrier. A process killed
during review leaves `reconciling`; a later explicitly requested review preserves
that interruption. There are no automatic retries, polling loops or expiry-based
unlocks. At most three reviews are allowed, including previews and interruptions.

If the operator accepts a current-state-only closure, inspect the new version
and request a **new assessment**, not reuse of the preview report:

```bash
python -m simulation.operation_review reconcile ATT_ID \
  --version CURRENT_VERSION --approve-runtime-inspection \
  --approve-current-state-closure
```

Only a fresh passing assessment records `operator_closed` and removes the barrier
in the same transaction. Original operation/cleanup uncertainty is unchanged.
If commit acknowledgement is lost, `current_state_closed` is unknown; inspect
the durable row before deciding what happened. A returned report is never an
execution grant. A distinct future incident still needs new evidence and explicit
approval through the complete controlled path.

## Evidence And Limits

The offline increment adds 69 tests, including two real subprocess exits against
temporary journals and synthetic evidence. Tests cover stale/conflicting data,
instance drift, request-container identity, lock/version conflicts, admission
write uncertainty, interrupted review, fresh reassessment and deadline expiry.
These are software controls, not physical recovery trials or diagnostic samples.

The earlier payment recovery trial used frozen revision `4a12a86`, before this
path and the durable journal. It does not validate this revision physically.
No new Groq call, fault, checkout or repair was run for this increment.
A separate read-only baseline inspection found 12 owned running instances,
12 fresh healthy graph statuses, a valid catalog and an observe-only collector.
Component windows were fresh; the current retained checkout window was empty.
That verifies adapter reads, not interrupted-attempt acceptance. The complete
offline regression passed 1,885 tests with three opt-in live checks skipped.

This is a single trusted-host controlled pilot. Other Docker clients, old workers,
queued external effects and hostile filesystem changes are not fenced by its
lock. Current-state closure is explicit operator acceptance of scoped evidence,
not an exactly-once guarantee or safe automatic resumption. Keep automation off.
LangGraph checkpoints, guarded workflow resume, actual orphan cleanup, distributed
fencing, worker/dashboard integration and broader physical acceptance remain
open M4/M5 work. See the [implementation roadmap](IMPLEMENTATION_ROADMAP.md).
