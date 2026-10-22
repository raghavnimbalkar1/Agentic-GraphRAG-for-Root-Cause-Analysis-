# Retained Evidence Investigation

`agent.investigation` provides the first M2 boundary for selecting diagnostic
evidence. It is an offline/read-only library, not an enabled model workflow.
The existing LangGraph repair path, graph catalog and SOP permissions are
unchanged. No dashboard action or incident endpoint invokes this session yet.

## Entry and Tools

A trusted caller supplies the evidence-store path, an existing failed transaction
evidence ID and a UTC-aware availability cutoff. The trigger must be retained,
available by that cutoff and measured within the preceding 90 seconds. Its
origin remains frontend. Mutable episode summaries and injector ground truth
are not used to construct the initial context.

| Tool | Scope | Result |
| --- | --- | --- |
| `component_window` | One registered service | Retained component states, identity, timestamps, freshness and gaps |
| `resource_window` | One supported service/metric pair | Existing numeric samples and missingness; no new probe |
| `transaction_evidence` | Frontend checkout workflow | Up to 20 retained transaction records; never initiates checkout |
| `error_signals` | Frontend log pilot | Retained normalized signals; never contacts Docker or scans logs |

Requests use 60-, 300- or 900-second windows and fixed metric names. They cannot
supply an end time, path, URL, query, script, command or remediation name.
Unregistered services and undeclared fields/tools are denied. Canonical evidence
IDs and Docker instance IDs are checked before exposing component evidence;
arbitrary text cannot enter through those metadata fields. All readers open
SQLite read-only; the library imports no model, settings loader, Docker client,
agent graph or execution controller.

Example for a developer-controlled session, using a real retained trigger ID:

```python
from datetime import datetime, timezone
from pathlib import Path
from agent.investigation import InvestigationSession

session = InvestigationSession(
    Path("audit/evidence.sqlite"),
    symptom_evidence_id=existing_failed_transaction_id,
    as_of=datetime.now(timezone.utc),
)
initial_context = session.initial_context()
result = session.call({
    "tool": "component_window",
    "service_id": "paymentservice",
    "window_seconds": 60,
})
```

A separate optional [shadow LangGraph runner](SHADOW_INVESTIGATION.md) now uses
these tools for bounded decisions, with a same-tool fixed policy for comparison.
There is no default live-model invocation or automatic repair integration. Model
benefit still needs real evaluation. The example itself does not select a root
or repair a service.

## Availability and Limits

Every reader receives the same trusted cutoff as both its event-window end and
its availability upper bound. Rows stored after that cutoff are excluded, even
when their measurement time is earlier. Collection timestamps and provenance
must also agree. The normal dashboard's retrospective views retain their existing
semantics; this stricter behavior applies to the investigation session.
Results label eligibility as `evaluated_as_of` and component freshness as
`fresh_at_cutoff`, not current live health. History records the actual read-result
time separately as `recorded_at`.

This is a temporal eligibility filter over the current retained store, not a
complete immutable dataset snapshot. Pruned data cannot be reconstructed, and
arbitrary local database edits are not cryptographically detected. Optional
[frozen-input replay](SHADOW_INVESTIGATION.md#frozen-input-replay) now exports a
sanitized, bounded subset before a trial and identifies it by digest. It cannot
reconstruct already-pruned data or authenticate the original observations. A
publishable study still needs a frozen protocol and independent ground truth.

Pilot ceilings are eight tool requests, 180 elapsed seconds, 64 KiB per returned
data object and 128 KiB of cumulative data including the initial symptom. Small
response envelopes/history are additional overhead. Invalid, failed and repeated
requests consume calls; a duplicate query does not reread the store. Failed or
oversized responses issue no new evidence references. Limits can be reduced,
not raised above these ceilings. Closed sessions cannot restart their budgets.
Concurrent calls on the same session are serialized. History records the validated
target/window/metric and outcome, not the rejected caller's arbitrary input.

Elapsed time is checked before and after a read. This is **not** hard process
preemption of a blocked disk/SQLite operation. The session is in memory; a trusted
caller can construct another session. Restart-safe incident budgets, process
isolation and a real model/token budget remain future work. Read-only code and
import isolation do not remove the local user's filesystem or Docker authority.

## Hypotheses, Not Repair Permission

One terminal proposal attempt may cite issued evidence IDs and list unresolved
registered alternatives. IDs must have been returned to this session (or be its
initial trigger), be observed rather than unknown, and be fresh at the cutoff.
Pilot freshness thresholds are 30 seconds for component clues, 60 for log events
and 90 for transactions. These are explicit initial policies, not measured optima.

A proposed readiness failure, resource pressure or request failure needs at least
one matching target-specific clue. A resource proposal can instead cite an
actual CPU, memory or writable-layer measurement obtained through `resource_window`,
even when the collector has not labeled that sample as a fault. The measurement
is not proof that pressure exists; its interpretation and causal relevance remain
unverified. A missing numeric value cannot supply this basis for a proposal.
Only the latest non-conflicting component
observations can supply that positive clue. A prior failure followed by healthy
or unknown evidence cannot stand in for a current component anomaly. A partial
log scan cannot provide a positive event citation under this initial policy.
The checks constrain evidence references; they do not validate causal reasoning
or prove that the proposed service caused the transaction failure.

Every accepted proposal returns `causal_attribution=false` and
`execution_authorized=false`. No root is assigned, no graph is updated, and no
alert or SOP is submitted. A simultaneous adservice CPU anomaly remains a
separate observation; retrieval does not rank it as the checkout cause.
An explicit terminal abstention can record insufficient evidence, an unknown
condition, unresolved alternatives or provider unavailability. Abstention is not
a diagnosed root or a successful repair.

## Verification Scope

Offline tests use synthetic payment, Redis, payment-plus-unrelated-CPU and unknown
cases with the same initial frontend symptom. They check evidence eligibility,
not RCA accuracy or model benefit. Additional controls exercise late/backdated
data, recovered/unknown/conflicting samples, invalid tools/citations, output and
time limits, malformed storage and import isolation. No model call, physical
fault, live recovery or new timing/accuracy result is established by these tests.

Remaining work includes the M1 live controls and trace/topology adapters; declared
dependency/configuration tools; live shadow-model and usage-ledger acceptance;
stronger credential/deadline isolation; and real matched-policy/model trials.
The optional shadow runner and fixed-policy implementation are described above;
their offline validation does not complete those acceptance gates.
