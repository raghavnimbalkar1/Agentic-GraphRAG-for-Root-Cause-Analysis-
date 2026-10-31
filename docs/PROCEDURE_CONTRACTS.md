# Versioned Procedure Contracts And Read-Only Preflight

The first M4 increment describes the effects of each reviewed operation and
binds a proposed procedure to a specific observation, container and catalog.
It does **not** connect the adaptive shadow investigator to execution.
The October 31 increment connects that investigator to typed, read-only SOP
offers and selection preflight, described below. It still cannot dispatch.

The current fixed-catalog remediation workflow remains separate. Its existing
condition recheck and controller allowlist still apply. The new data-loss gate,
implementation binding and richer preconditions apply to the new read-only
preflight path, **not yet to every legacy repair**. Do not report this increment
as a completed action-authorization boundary.

## One Graph Catalog, Eight Operation Policies

Neo4j remains the source of named skills, metadata, `APPLIES_TO` and
`NEXT_IF_FAIL`. One query captures the catalog without discarding unknown
properties. The compiler rejects unsupported fields, scripts, target/condition
bindings, risk labels, paths, timeouts and fallback transitions.

Local policies describe eight maintained implementations, not a second list of
15 named SOPs. The existing executor and controller import their unchanged
operation/target allowlists from this module. A new graph node cannot authorize
a new implementation or enlarge those capabilities.

Each immutable contract contains:

- The graph name, trigger, targets, parameters, timeout and catalog risk label.
- A policy version, registry digest and implementation-file SHA-256 digests.
- A fixed operation and its reviewed parameter defaults.
- Side effects, effect risk, required permission and repeat policy.
- Expected postconditions, durability class and compensation limitation.

The implementation fingerprint includes the script, policy, health interpretation
and sandbox sources. Controlled operations also include the control client and
gateway. Symlinks, missing files and oversized sources are rejected. Catalog
digests include every contract and fallback edge. A change to another skill also
invalidates a pending plan; this is conservative, not minimal invalidation.

These are filesystem fingerprints, not signatures, deployed-image attestation
or proof that an old process has reloaded changed Python modules. A trusted
caller must recapture the catalog from the graph and current reviewed files.
An arbitrary deserialized catalog is not an authority source.
The operator commands and initializer currently require the bundled repository
SOP layout. They reject a custom `SOPS_DIR` instead of hashing a different tree
from the one the legacy executor would use. Installed-package/custom-adapter
layouts need a separately reviewed implementation-source resolver.

## Effects Are Not The Old Risk Labels

| Operation | Effect | Outcome limit | New preflight permission |
| --- | --- | --- | --- |
| Container / Redis restart | Process restart, loss of in-flight work, possible network reconnect | Process reset does not patch a defect or persistent configuration | Disruption |
| Adservice throttle | CPU period 100,000; quota 50,000 | Temporary containment; busy-loop remains | Resource change |
| Frontend CPU restoration | Remove quota | Only justified when a positive quota is actually present | Resource change |
| Email disk cleanup | Delete exactly `/tmp/diskfill.bin` | Targeted cleanup, not general disk repair | Fixed cleanup |
| Redis flush | Delete all databases' keys; reset runtime memory/policy | Data loss; persistent launch configuration is not repaired | Disposable data |
| Redis pool reset | Disconnect normal clients | Legitimate clients are also affected; recurrence remains possible | Disruption |
| Redis config reset | Set runtime policy to `allkeys-lru` | Runtime correction, not persistent configuration repair | Runtime configuration |

All new preflight permissions default to false. General disruption permission
does not permit Redis deletion. The flush contract reports effect risk `HIGH`
despite its legacy graph label `LOW`. The legacy executor is not silently
reconfigured by these descriptions.

## Observation-Bound Intent

`bind_plan` requires a registered target, exact graph trigger and canonical
version 2 observed component evidence. Environment, registry, observation ID and
64-character Docker instance ID must match. Evidence must be less than 30 seconds
old. Unknown, healthy, future, stale or unidentified observations cannot bind a
repair intent.

The immutable plan records the incident, trusted revision, catalog/contract
digests, target, condition, instance, evidence ID/payload digest and expiry.
It always says `execution_authorized=false`. It is neither a signed capability
nor a durable operation lease. No model receives a shell command or supplies
these local permissions through this path.

`check_preflight` additionally requires:

1. The same incident and current trusted plan revision.
2. A newly captured catalog with unchanged implementation and metadata.
3. Fresh condition evidence for the same container, without rewritten IDs.
4. A trusted runtime inspect matching the registered container name and ownership
   label. The inspect must follow plan creation and be less than five seconds old.
5. Supporting resource measurements, not just an error label: CPU, Redis memory
   cap, client count, expiring-key count, disk/memory pressure or drifted policy.
6. The operation-specific permission. Frontend quota removal also needs a
   measured positive CPU quota.

The result is eligibility for further review, **never dispatch permission**.
The rules do not establish causal correctness, incident uniqueness, adequate
resource capacity or application recovery. Checks are cooperative; they do not
close the race between an inspect and a future mutation.

The known Redis restart-to-flush edge is captured and checked as a same-target
transition. It does not let `bind_plan` substitute OOM for the flush's declared
STALE_DATA trigger. A typed, independently verified prior-attempt contract and
explicit data-loss policy are still required before adaptive fallback execution.

## Operator Commands

Read the actual Neo4j catalog and validate local implementations without running
any procedure or model:

```bash
python -m graph.scripts.audit_procedures \
  --output output/preflight/catalog-001.json
```

`python -m graph.scripts.init_graph --verify-only` now also validates these
contracts. It makes no graph writes. Use normal initialization only when a
catalog update is intended; do not clear graph volumes for this increment.

Inspect one procedure against a running container:

```bash
python -m simulation.procedure_preflight \
  --skill Payment_Restart_SOP \
  --service paymentservice \
  --incident-id INSPECT-PAYMENT-001 \
  --revision 1111111111111111111111111111111111111111111111111111111111111111 \
  --output output/preflight/payment-001.json
```

This command reads two catalogs/observations only when the first condition
matches. It inspects the exact observed container ID. It retains the evidence,
plan and runtime projection in an owner-only, exclusive report. Its diagnostic
gRPC probe can create a restricted temporary probe container. It never injects a
fault, constructs a model, calls the control gateway or dispatches a SOP.

The CLI supplies no effect permissions. A healthy service returns
`no_matching_observed_condition`; an otherwise valid fault can return
`operator_permission_required`. Exit code 2 means not eligible, not a failed
repair. Missing clients or failed reads remain unavailable, not healthy.
Do not reuse output paths. Partial/failed attempts remain separate records.

## Binding A Current Diagnosis To Procedure Choices

`ReplanningInvestigation.prepare_procedures()` now reads the graph catalog and
binds choices to its own current screened candidate or its current full-scope
consistency check. It accepts a candidate revision, not an imported candidate
dictionary. A saved passing report cannot create an offer in a new controller.
The original expired proposal remains expired when a consistency receipt is used.

Before any selection latency, `agent/procedure_binding.py` freezes:

- The incident, candidate revision, evidence input digest and source kind.
- The exact observed condition, target instance and observation payload digest.
- The complete catalog digest, implementation fingerprints and permitted SOP names.
- The operator's effect permissions, effects, durability labels and expiry.

The existing dependency screen runs again against the controller's evidence.
The procedure trigger must match the target observation exactly: a model's
`readiness_failure` category cannot turn observed `DOWN` into
`CONNECTION_REFUSED`. Only already-supported investigator conditions are usable;
this does not expand diagnosis to every trigger in the legacy catalog.
Numeric preconditions are checked before offering a resource procedure.
Conflicting same-time measurements reject the offer even if status labels agree.
Runtime-specific preconditions, such as a positive CPU quota, still need preflight.

Permissions default to deny. Only permitted effects appear in the choice set;
different disruption/resource policies can therefore produce different choices
for the same observed adservice CPU condition. A `NEXT_IF_FAIL` edge does not
substitute for an independently established trigger or a prior-attempt proof.

The returned `selection_context` contains exact skill names and descriptive
effects, not script paths, script contents, environment variables or runtime
grants. Graph descriptions remain untrusted text; a description cannot add an
allowed skill name. No provider is constructed or called by this interface.

`select_procedure()` accepts only a bounded dictionary with `offer_digest`,
`action` (`select` or `abstain`) and, for selection, one `skill_name`. Extra target,
code, path, parameter, permission or authority fields are rejected. The controller
consumes the pending offer on the first attempt, including malformed responses,
read failures and interruptions. This is **one-use selection**, not a one-use
execution capability. Retrying a response cannot renew its evidence lifetime.

Selection preflight recaptures the catalog and checks a new target observation,
trusted ownership/instance inspection and current permissions. Catalog read time
counts against evidence and five-second inspection freshness. Permissions may
be revoked while selection is pending; newly granted permissions cannot enlarge
the frozen names. The earliest required scope observation still bounds expiry,
including healthy controls. Changed epochs, failed refreshes, incident stops or
expired consistency checks withdraw pending offers.

One evidence basis can create only one offer. Preparation attempts, including
rejections, are bounded by twice the configured epoch limit (at most eight).
Immediate preflight evidence IDs/digests cannot identify changed payloads in a
later epoch. Read-only offer/selection histories remain in the controller
snapshot; they are process-local and are not a durable operation journal.

Every result keeps `execution_authorized=false`; selection also records
`action_dispatched=false`. An eligible plan must **not** be treated as an
authorization by the legacy executor. This path has no executor hook, provider
call, additional diagnostic read or mutation. Any future selection-model call
needs explicit admission/accounting rather than borrowing an old trial allowance.
The caller must import observed scope changes; the controller is not a continuous
watcher and a short-lived offer is not a dispatch-time guarantee.

## Verification And Remaining Work

Offline controls cover all eight policies and all reviewed restart bindings,
metadata/code changes, stale plans, replacement containers, ownership, rewritten
evidence, numeric preconditions, independent permissions, fallback rejection,
read-only operator behavior and private artifact handling.
The increment adds 128 tests. Its complete offline suite passes 1,054 tests;
three opt-in live checks are skipped there and were separately run successfully.
An existing test's accidental storefront request was replaced with an explicit
canary double, so unit behavior does not depend on Docker being available.

On October 24, a real read-only audit validated 15 skills, 19 applicability
bindings and one fallback. Independent live probes observed all 12 containers
healthy; the five-step private-session checkout canary passed. Three separately
enabled Neo4j tests passed, including UUID-scoped fixture controls. These are
not a new LLM trial or an adaptive repair demonstration.

The October 31 binding increment adds 63 synthetic software controls for current
and refreshed evidence, changed targets, exact triggers, numeric conflicts,
effect policy, invalid/injected selections, drift, delays, one-use selection,
concurrency, interruptions and immutable evidence. These checks make no provider
requests, Docker probes, faults or repairs. They do not establish model selection
quality or live action acceptance; the earlier test totals above are historical.
The complete offline suite passes 1,572 tests, with three opt-in live tests skipped.

Before adaptive execution, constrain the actual execution environment,
validate prior attempts/fallbacks and consume a separate single-use action grant
at the executor. Add procedure-specific postcondition/relapse tests. Declared
postconditions and durability classes are not measured durability results.
M5 must address restart-wide attempts, unknown effects, reconciliation, hard
cancellation and credential isolation. No fresh model inference or repair is
claimed by this increment.

See the [roadmap](IMPLEMENTATION_ROADMAP.md#m4-make-procedures-explicit-and-outcomes-honest),
[SOP catalog](SOP_CATALOG.md) and [current status](STATUS.md).
