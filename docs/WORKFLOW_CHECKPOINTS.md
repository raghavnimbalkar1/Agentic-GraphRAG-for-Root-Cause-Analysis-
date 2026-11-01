# Diagnostic Workflow Checkpoints

The shadow diagnostic LangGraph now supports opt-in local SQLite checkpoints.
They retain the initial context, parsed decisions, bounded tool responses,
session history and checkpointed usage after each node. This is durable diagnostic
history, **not automatic resume** of a diagnostic or repair workflow.

The implementation uses the official `SqliteSaver` with synchronous durability.
LangGraph documents the separation between persisted thread state and external
operations in its [persistence guide](https://docs.langchain.com/oss/python/langgraph/persistence).
Our wrapper adds one-shot admission, a local writer lock, bounded plain-JSON
serialization and read-only inspection. It does not implement a new checkpoint
engine, deserialize an execution grant or reconnect to a provider on inspection.

## Three Separate Stores

| Store | Purpose | Does not provide |
| --- | --- | --- |
| `audit/evidence.sqlite` | Independently observed symptoms/components/resources | Execution permission or causal proof |
| `audit/controlled_operations.sqlite` | Controlled operation admission, effect/verification history and review barrier | A resumable LangGraph or exactly-once Docker effects |
| `audit/diagnostic_workflows.sqlite` | Optional diagnostic graph checkpoints and one-shot run records | Live capability restoration, fresh evidence or provider allowance |

There is no distributed transaction across these stores, the model provider and
Docker. The [operation journal](OPERATION_JOURNAL.md) and
[passive current-state reconciliation](OPERATION_RECONCILIATION.md) remain separate
from diagnostic checkpoint inspection.

## Admission And Crash Meaning

The identity retains environment, registry digest, symptom evidence ID, decision
cutoff, context/implementation digests and driver kind. Thread IDs derive from the
environment and symptom ID. Changing the cutoff, prompt, provider or implementation
cannot admit that same trigger again in the same store.

Before invoking the graph, the wrapper commits a `running` admission record under
an OS writer lock. The lock covers the entire invocation. Native input checkpoints
commit before the first decision node; synchronous node checkpoints commit before
the next node starts. Storage failure does not fall back to an in-memory run.

A completed run has a terminal graph checkpoint and a separately committed
completion record. If the process dies after a provider call but before its
result is checkpointed, the last checkpoint can understate actual usage. The
inspector therefore reports **checkpointed** decisions/tokens, marks interrupted
usage incomplete and says pending work may have run. It neither bills that gap
as zero nor repeats the request to discover its result.

| Inspector State | Interpretation | Continuation |
| --- | --- | --- |
| `completed_history` | Terminal diagnostic state retained; candidate is historical | No automatic resume or execution |
| `interrupted_or_active_review_required` | Admission exists, but terminal completion is not established | Review only; usage can be incomplete |
| Rejected/inconsistent store | Missing, foreign, corrupt, replaced or unsupported data | No inferred continuation |

An interrupted record can also belong to a still-active writer; inspection does
not declare the process dead. A completion write whose acknowledgement is lost
is conservatively retained as review-only where possible. It is not silently
replayed. Checkpoint refusal latches a stop in the replanning controller, including
for later epochs. The CLI returns failure and stops at a rejected epoch.

The live controller's shared budget is recorded as historical data, not restored
as a new allowance. Cross-process continuation with fresh evidence and preserved
budgets remains future work. In the same live controller, new observation epochs
still share the existing bounded budget and may checkpoint their new triggers.

## Use

Development setup and CI include the optional persistence dependency. To install
it separately without starting any services:

```bash
python -m pip install -c constraints-tested.txt -e '.[shadow,persistence]'
```

The existing offline replanning command accepts a new explicit flag:

```bash
python -m eval.shadow_replan \
  --inputs PRIVATE_EPOCH_1.json --inputs PRIVATE_EPOCH_2.json \
  --checkpoint-database audit/diagnostic_workflows.sqlite \
  --output NEW_PRIVATE_REPORT.json
```

This command uses the fixed diagnostic policy, not a model. It runs no Docker
operation, fault, checkout or repair. Input and output rules remain unchanged:
frozen cutoff-ordered inputs, exclusive report creation and no hidden retries.
Library callers can pass `checkpoint_path` to `ShadowRunner.run` or
`ReplanningInvestigation.run_epoch`; existing model-transfer approval gates still
apply. Persistence itself grants no provider permission.

Inspect history without settings, a model, Docker, Neo4j or graph compilation:

```bash
python -m simulation.workflow_review
python -m simulation.workflow_review --database audit/diagnostic_workflows.sqlite
python -m simulation.workflow_review --include-state
```

The default view omits saved evidence. `--include-state` displays private sanitized
context and tool responses; do not publish that output automatically. Missing
stores are not created by inspection. There is deliberately no resume, retry,
delete, repair or grant-restoration command.

## Local Boundaries

New store/lock files use mode `0600`, SQLite WAL and `synchronous=FULL`. The wrapper
checks file identity and stops admission at 256 retained runs. Checkpoints and
pending writes have per-run limits; plain values have a two-MiB serialized bound
and a nesting limit. A database/WAL size guard stops further writes above 64 MiB;
this is not a hard process-memory or filesystem quota. There is no automatic
pruning or expiry unlock.

The serializer accepts only finite plain JSON values and tuples represented as
arrays. It does not accept pickle/msgpack payloads, instantiate constructors or
import objects from stored data. Node error writes use a fixed category instead
of raw exception text. Context digests catch accidental mismatch, not hostile
rewrites of a trusted local store. Neither hashes nor local file permissions are
attestations against a compromised host.

This boundary covers the opt-in diagnostic path and the same protected store.
Legacy workers, invocation without persistence, another database path and external
Docker clients are not fenced by it. The legacy API remains unchanged. No saved
candidate, grant, collector setting or repair workflow is made active on restart.

## Verification Scope

Fifty-four new offline controls cover native checkpoint/report parity, durable input
before a driver call, decision persistence before evidence dispatch, writer and
identity conflicts, missing optional dependencies, unsafe encodings, corrupt
stores, CLI rejection, process-local budget continuity and client-free inspection.
Two actual child-process exits use synthetic decisions and temporary SQLite
stores. They are not provider failures or physical recovery trials.
Actual SQLite commit failures before commit and lost acknowledgements after
commit are exercised separately at admission and terminal recording. A private
offline CLI walkthrough also retained two synthetic epochs, reloaded them in a
new process and refused a duplicate without another decision.
The final offline regression passed 1,939 tests with three opt-in live checks
skipped. Whole-tree F-lint, Bash syntax, dependency consistency and local
documentation validation passed as well.

No fresh model request, Docker inspection, application transaction, fault,
repair or runtime restart was run for this increment. The earlier payment trial
predates this source and does not validate these checkpoints physically. M5
still needs guarded continuation, durable cross-process allowances, operation
linkage, orphan cleanup, worker/dashboard integration and separately approved
physical acceptance. See the [roadmap](IMPLEMENTATION_ROADMAP.md).
