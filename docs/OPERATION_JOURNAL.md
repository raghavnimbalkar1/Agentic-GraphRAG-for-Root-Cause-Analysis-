# Controlled Operation Journal

The controlled executor now requires a local SQLite journal before opening
Docker. This adds restart-persistent admission and conservative duplicate denial
to the operator-controlled pilot. It does **not** make execution exactly once,
resume a LangGraph workflow, or authorize another operation after a crash.

## Admission Order

The default path is `audit/controlled_operations.sqlite`. The journal is separate
from the collector evidence store and the legacy incident-claim database.

1. Validate the opaque grant's current evidence and operator permission.
2. Acquire the journal's OS writer lock; commit a preparing attempt and deployment
   barrier before Docker client creation or sandbox activity.
3. Commit the request sandbox's exact name, nonce, image ID and role before its
   create call. Run and remove it, retaining its cleanup result. Re-probe the
   full required scope and validate the unchanged transaction, graph and catalog.
4. Commit `effect_attempted` before entering the fixed controller operation.
   Recheck evidence and inspection age after the commit: disk latency cannot
   extend the grant's lifetime.
5. Record the controller acknowledgement or uncertainty. Release the OS writer
   lock when this execution invocation ends; retain the durable barrier.
6. Reacquire the same owned attempt for read-only recovery verification and
   commit `verifying` before opening Docker or sending checkout probes. Persist
   the complete bounded recovery receipt before returning verified recovery.

SQLite uses WAL mode, `synchronous=FULL` and explicit write transactions. New
database and lock files are created with mode `0600`. Inspection uses a single
read-only snapshot, so the attempt, event head and barrier cannot come from
different concurrent commits. Event receipts have a two-MiB bound, attempts have
a 32-event bound, and the journal stops admitting attempts at 1,000 retained
rows. There is no automatic deletion, expiry-based unlock or history pruning.

## Identity And Ownership

Each attempt retains its environment and registry, original transaction evidence
ID, incident, plan/offer/catalog/implementation digests, target instance, skill,
condition and attempt number. The original transaction comes from the controller,
not a model response. Both incident and original transaction are unique within
an environment, even after terminal review; changing a plan version, target or
input bundle cannot reuse that same incident/transaction.

A deployment-wide barrier also blocks a second controlled attempt with a new
incident or transaction while the preceding attempt awaits review. It is keyed
by environment, not registry revision, so metadata changes do not bypass it.
The OS lock covers an active execution or verification invocation. Other local
writers and terminal reviewers cannot take ownership while that lock is held.

The lease's random owner secret stays in memory. Reports and CLI inspection omit
it. Stored receipts do not recreate the opaque grant or a live execution owner.
An attempt ID, database row or `execution_authorized` field is not a capability.

## Crash Interpretation

| Recorded Stage | What Is Known | Automatic Retry |
|---|---|---|
| `preparing` | Preparation was admitted; fixed-operation admission is not recorded | Denied |
| `effect_attempted` | Admission committed; the external effect may or may not have occurred | Denied |
| `effect_uncertain` | An admitted effect did not obtain a confirmed completion record | Denied |
| `operation_confirmed` | Controller acknowledgement recorded; application recovery still unverified | Denied |
| `verifying` | Read-only verification started; no terminal recovery receipt recorded | Denied |
| `verified_recovery_window` | Scoped sampled recovery recorded, not permanent defect repair | Denied; terminal review required |
| `verified_mitigation` | Temporary containment verified during the sampled window | Denied; reconciliation required |
| `not_recovered`, `recurrence_observed`, `verification_inconclusive` | Recovery not established | Denied |
| `not_dispatched` | This adapter finalized without entering the fixed operation | Denied; terminal review required |

There is an unavoidable gap between journal admission and Docker's effect. A
process killed inside that gap leaves `effect_attempted`; that state deliberately
does not mean the operation ran. A timeout or acknowledgement loss after the
effect also cannot justify repeating it. Journal write uncertainty stops dispatch
or prevents a verified-recovery return, depending on where it occurs.

An expired/revoked diagnosis cannot execute again. Its already confirmed effect
may still receive one separately approved read-only verification attempt using
the same in-memory owner and durable record. Recovery verification's cooperative
deadline includes journal-admission latency.

## Operator Commands

Inspection makes no provider request, Docker call or application transaction. It
does not create a missing journal or reconfigure automatic delivery:

```bash
python -m simulation.operation_review show
python -m simulation.operation_review show --include-receipt
```

After inspecting the receipt and any cleanup obligations, a local operator may
acknowledge a terminal `not_dispatched` or `verified_recovery_window` attempt.
A no-dispatch result with uncertain request-sandbox cleanup is not eligible:

```bash
python -m simulation.operation_review acknowledge-terminal ATT_ID \
  --version CURRENT_VERSION --approve-review
```

Use the exact ID/version from inspection. The command checks that version under
the writer lock and releases only that deployment barrier in the same transaction
as the review event. It does not erase the attempt, authorize execution, retry
the old incident, clean an orphaned sandbox or prove the deployment healthy.
A subsequent distinct incident still needs fresh evidence and new explicit
operator approval through the full guarded path.

In-flight, uncertain, failed-verification and mitigation states **cannot** be
cleared with this command. The subsequent [passive reconciliation path](OPERATION_RECONCILIATION.md)
can inspect new v2 attempts without repeating their effect. A passing preview
retains the barrier; a separate literal operator approval reruns the assessment
and can close only the current-state review, not prove the old operation ran.
Temporary containment and missing provenance/evidence remain blocked. Do not
delete or replace the journal to work around a blocked attempt.

## Verification Scope

Tests use temporary local journals, mocked Docker/controller/canary adapters and
real subprocesses that exit with `os._exit`. They cover preparing, admission
before a simulated effect, effect before acknowledgement, confirmed operation,
verification and terminal verification. These are crash-boundary software tests,
not six physical recovery trials. Additional controls cover active writers,
changed origins/versions, commit acknowledgement loss, corruption, file loss,
snapshot consistency, strict terminal review, receipt limits and disk latency.
The final offline suite passes 1,816 tests with three opt-in live checks skipped;
83 added controls and the 242-test focused group pass. Software test counts are
not diagnostic sample sizes or physical recovery rates.

The earlier physical payment trial remains evidence for its frozen `4a12a86`
revision, which did not contain this journal. It is not physical acceptance of
the changed journal-backed executor. No new model request, fault injection or
repair is claimed for this increment.

## Remaining Boundaries

The journal protects only this controlled path, on one trusted local host using
the same configured journal and lock file. Legacy workers and other Docker
clients do not enforce its barrier. Moving to another path, deleting/restoring
files, hostile filesystem writes, network filesystems and distributed workers
are outside this guarantee. Source hashes and receipt digests are not signatures
or attestations. The configured audit directory must remain protected and intact.

The subsequent passive review adds fresh current-state assessment and explicit
closure while retaining historical effect uncertainty. It does not add workflow
checkpoints, crash-time sandbox cleanup, proof of an interrupted effect,
distributed fencing, automatic fallback or worker/dashboard integration. M4/M5
and main-promotion acceptance remain open.
