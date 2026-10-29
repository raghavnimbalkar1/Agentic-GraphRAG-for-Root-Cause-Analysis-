# Retained Observation Capture

This opt-in operator command bridges newly retained local observations to the
existing frozen diagnosis and candidate-recheck interfaces. It does not start a
model, transaction probe, fault, remediation or background investigation.

```bash
python -m eval.shadow_capture --output output/shadow/capture-001
```

Use a new directory for every attempt. The default source is the configured
audit directory; `--audit-dir` can select a trusted local collector directory.
Output stays local and should remain Git-ignored. Directory permissions are
owner-only; individual files use owner-only, exclusive creation. Previous
failures and interrupted attempts cannot be overwritten through this command.

## Capture Rules

1. Require a fresh version 2 collector heartbeat, ready evidence storage and
   explicitly disabled automatic delivery. Degraded component health does not
   itself block diagnosis: unknown observations must remain available to support
   an abstention. Missing, stale or stopped collection blocks capture.
2. Read registered service identities and `DEPENDS_ON` edges from Neo4j in one
   bounded query. No status, error, skill, predicted root or injector label is
   exported. Reject missing, duplicate or unregistered identities, duplicate or
   invalid edges, and an empty or oversized graph. This supports the bundled
   twelve-service deployment, not arbitrary application onboarding.
3. Bind a stable topology revision to canonical identities, edges and registry.
   Merely changing export time or row order does not change the revision. This
   is an export of **declared** topology, not trace-discovered dependencies.
4. Select the latest retained transaction available at the trusted cutoff.
   Require a unique, observed failure measured within ninety seconds. A newer
   healthy or unknown observation blocks an older failure. Equal latest times
   are ambiguous, not a reason to select the preferred label.
5. Freeze the existing sanitized, version 1 evidence scope in one SQLite read
   transaction. Reconcile the chosen trigger with that bundle. Existing row,
   payload, provenance, retention and availability bounds remain unchanged.
   Trace transfer is not added. A graph export older than thirty seconds is
   rejected.
6. Check collector policy again before saving. A freeze that finishes ten or
   more seconds after its cutoff is discarded. This is a cooperative eligibility
   limit, not hard preemption of blocked Neo4j, disk or SQLite work.

The command reads the evidence store without writing it. It writes only the new
capture directory. Its Neo4j client still uses the trusted local credentials;
read-only query behavior is not a separate database permission boundary.

## Records And Outcomes

| Record | Purpose |
| --- | --- |
| `started.json` | Source fingerprints and start time, created before graph access |
| `declared-topology.json` | Validated declared graph export, if available |
| `inputs.json` | Digest-bound sanitized input bundle, only for an eligible failure |
| `result.json` | Captured, not eligible, failed or interrupted, with a fixed reason |

A healthy baseline should return `not_eligible` with
`latest_transaction_not_failed` and create **no** input bundle. This is an
expected refusal, not a failed diagnosis or successful repair. The CLI exits
nonzero for a source failure; an expected eligibility refusal exits zero.

All results declare zero model calls and new application probes, no execution
authority, no causal attribution and no verified recovery. The timestamps are
checked against local policy, but the capture does not authenticate the evidence
source or independently prove current runtime freshness. A locally edited store
can still contain fabricated data. `retained-observation` identifies the input
channel, not a guarantee of real provenance.

## Diagnosis And Fresh Rechecks

Eligible bundles use the existing `load_inputs` interface. A trusted operator
can supply one to `ReplanningInvestigation.run_epoch`, using the same reviewed
profile, evidence layout and protocol as the repeated development check. A new
capture from independently collected measurements can then be supplied to
`recheck_candidate` on that **same in-memory investigation**. No state is restored
from a capture file and no incident budget is reset.

The recheck requires newer observations for every scoped component, a new failed
transaction with the same failed steps, unchanged identities/conditions and the
same declared topology. Reusing a bundle, replacing an instance or observing a
recovered condition rejects the check. A consistent receipt is separate from
the old candidate and still has `execution_authorized: false`; it grants no SOP.
See [candidate consistency rules](SHADOW_REPLANNING.md#explicit-candidate-consistency-recheck).

Capture never constructs an external model driver. Sending a newly captured
bundle to Groq requires separate finite approval for its actual data scope.
Earlier synthetic-campaign approval does not cover live observations, more
calls, faults or repairs. Persistence, action binding and guarded recovery
remain separate work.

## Verification Scope

Offline controls cover latest failure selection, healthy/unknown supersession,
ambiguous or stale triggers, graph identity and revision checks, collector policy
changes, private/exclusive records, sanitization and interrupted attempts. A
synthetic integration check uses the existing fixed policy, then verifies that
a new capture can produce a consistency receipt while changed conditions or
instances reject it. No external model or physical recovery is involved.

The October 29 local healthy-baseline check exported sixteen declared edges,
then refused capture after a real successful mock checkout. It produced no
failed-incident bundle, model request, graph update or repair. A real failed
symptom, model diagnosis, newer-evidence recheck and guarded action have **not**
yet passed through this bridge end to end. M1-M3 signoff remains open.
