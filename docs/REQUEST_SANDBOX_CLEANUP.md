# Interrupted Request-Sandbox Cleanup

This operator-only command can remove **one exact reserved SOP request container**
after its owner has stopped. It cannot repair a service, execute a procedure,
retry the original action, call a model, send a checkout or release a deployment
barrier. It is separate from [passive reconciliation](OPERATION_RECONCILIATION.md).

## Admission And Identity

Cleanup requires literal approval and the current open attempt version under
the [operation journal's](OPERATION_JOURNAL.md) exclusive writer lock. Before
opening Docker, the command commits `cleanup_inspecting` with the original stage.
Only v2 attempts with the exact write-ahead request name, nonce, pinned image ID
and `bound-sop-intent` role qualify. Unknown names and legacy v1 records do not.
The environment and registry digest must match this deployment.

A present container must match that reservation and have a full immutable ID.
The command rejects the original target and any retained scoped-service ID,
managed-service or Compose ownership labels, different command/user, added
capabilities, a writable root filesystem, service-network access, host namespaces,
devices, writable/extra host mounts and changed resource constraints. The two
read-only script mounts must share the expected temporary-directory layout.
These checks inspect metadata; they are not runtime attestation.

Before removal, another durable event records `cleanup_removal_admitted` and the
exact container ID with an unknown outcome. The adapter re-inspects that ID,
rechecks the reservation/isolation, writer ownership and deadline, then issues
at most one `remove_container(ID, force=True, v=False)`. It never deletes by a
reused name, removes volumes, lists/prunes containers or mutates a managed target.
The request may be running; forced removal is limited to this isolated request.

Acknowledgement alone is insufficient. Both the removed ID and reserved name
must be observed absent. If the reserved name was already absent, no removal is
sent. A reused name, timeout, missing acknowledgement or conflicting metadata
produces an inconclusive result. SDK error text is not copied into the receipt.

## Restart And Review

Each attempt permits **one cleanup admission**, including interruptions before
removal. A crash never refunds that admission. Later invocations cannot silently
repeat a possibly dispatched removal, even after an inconclusive result. Inspect
the journal; an independent passive assessment can observe subsequent absence.
A still-present or conflicting container requires separate manual review. This
conservative policy can strand a cleanup that failed before dispatch.

Cleanup finishes as `cleanup_finished`, or retains its last admitted stage if
interrupted or persistence is uncertain. Its receipt is separate from historical
`sandbox_cleaned`, `state` and `operation_confirmed`; those fields are not rewritten.
The barrier remains. Generic terminal acknowledgement cannot close a cleanup
stage. Cleanup reserves space for three journal events plus a later two-event
assessment; the overall 32-event limit and three-assessment limit still apply.

To release the barrier, the operator must request a **new passing current-state
assessment** using its new version and separate closure approval. That assessment
still needs matching catalog/topology, same owned instances and fresh independent
component/checkout witnesses. Cleanup events conservatively reset its 120-second
journal-age floor. Waiting alone does not establish health. This is an age guard,
not a continuously observed healthy soak or proof that the old repair happened.

## Commands

Inspect first; this command has no runtime side effects:

```bash
python -m simulation.operation_review show --include-receipt
```

After reviewing the exact reservation, explicitly authorize its one-shot cleanup:

```bash
python -m simulation.operation_review cleanup-request-sandbox ATT_ID \
  --version CURRENT_VERSION --approve-cleanup
```

Exit zero means a fresh absence observation and confirmed receipt write, **not
application recovery**. Every result has `execution_authorized=false`,
`automatic_retry_allowed=false`, `recovery_verified=false` and
`current_state_closed=false`. `remove_dispatched` describes this invocation, not
an interrupted predecessor. The durable removal admission deliberately retains
`outcome=unknown`; the separate report describes acknowledgement and observation.
`journal_finish_confirmed` in the returned result describes the adapter's receipt
acknowledgement, not a capability or a proof of remote effect.

Use [the reconciliation commands](OPERATION_RECONCILIATION.md#operator-commands)
for a separate review. Do not delete or replace a blocked journal.

## Evidence And Limits

Validation uses mocked Docker inspection/removal, real temporary SQLite stores
and actual subprocess exits with local synthetic-effect markers. It covers
identity/isolation conflicts, writer/version exclusion, write acknowledgement
loss, deadline expiry, name reuse, interruption and fresh reassessment. These
are software controls, not physical cleanup trials or diagnostic samples. No new
model call, live fault, checkout, container removal or repair was run for this
increment. The earlier payment recovery result predates this source.
The increment adds 90 offline controls. The complete regression passes 2,087 tests
with three opt-in live checks skipped; the cleanup/reconciliation group passes
159. A slow journal-owner-close regression first reproduced stale response
acceptance, then passed after the final freshness check moved past owner closure.
The installed SDK's create/delete serialization is exercised without HTTP I/O.

The deadline is cooperative: 20 seconds, with five-second SDK request timeouts.
A blocking daemon call may finish later; it is not remotely cancelled. Absence
observations expire after five seconds and are checked after resource closure and
journal acknowledgement. A delayed result is inconclusive even if a historical
absence receipt committed. Lost journal acknowledgement is not retried.

The local lock excludes cooperating owners only. Other Docker clients, delayed
create/remove calls, external changes and hostile filesystem writes are not fenced.
An absence observation cannot exclude a late create or prove exactly-once effects.
Changed implementation digests can also block later catalog reconciliation.
Automatic workers, distributed recovery, general orphan discovery and current-source
physical acceptance remain outside this increment. M5 is not complete.
