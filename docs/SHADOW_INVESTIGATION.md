# Shadow Investigation Loop

The optional `agent.shadow.ShadowRunner` runs a separate LangGraph:

```text
retained failed transaction + declared dependency snapshot
  -> decision -> bounded retained reads -> decision
  -> evidence-checked provisional hypothesis or abstention
```

It does not replace the active incident/remediation graph. No dashboard control,
collector alert or incident endpoint invokes it automatically. The existing skill
graph and SOP execution permissions are unchanged. Every result has
`causal_attribution=false` and `execution_authorized=false`.

## Decisions and Evidence

One response can request one to three approved reads, propose a hypothesis, or
abstain. The typed schema forbids executable actions, SOP names, commands, URLs,
paths and extra fields. Duplicate JSON fields, non-finite JSON and oversized
responses are rejected. Invalid decisions consume a decision attempt and return
fixed feedback; raw malformed output is neither retained nor echoed into the
next prompt. Invalid output cannot reset the incident limits.
Resource metrics are target-specific. A mismatched metric/target is now rejected
before reading evidence, not reported as a telemetry outage. Model batches with
that error receive only the fixed `metric_target_mismatch` reason; the whole
invalid decision consumes an attempt without dispatching any request. Invalid
direct broker requests still consume a tool call. No raw validation input/error
text is copied into feedback.

The runner uses the four [retained evidence tools](INVESTIGATION_BOUNDARY.md).
It receives a recent failed transaction, registered service names, fixed workflow
anchors, metric/target pairs and a versioned declared topology supplied by a
trusted caller. The topology must match the registry and be captured by the
availability cutoff. It is not automatic discovery, an observed trace graph or
proof of causality. `declared-fixture` inputs are explicitly identified; there is
no automatic Neo4j export in this increment. The workflow anchors are reviewed
Boutique-specific prior knowledge, shared with the baseline, not trace evidence.

Both policies receive the same initial context and public tool responses. Prompt
feedback projects the latest eight component samples, two transaction records,
two log batches with four signals each, and three missing intervals. Omission
counts are explicit. Summary statistics still describe the reader's full bounded
window, not only those displayed samples. A hypothesis can cite only IDs actually
shown to that runner, even if the underlying reader returned more records.

Results retain the selected reads, feedback, valid decisions, accounting,
termination reason and broker history. The common availability cutoff remains
fixed throughout the run; freshness is at that cutoff, not current live health.
An anomalous service or a plausible hypothesis is never promoted automatically
to an attributed root or an approved remediation.
If dispatch reaches its deadline between reads, it retains the selected batch
and any completed projected feedback. An oversized batch is retained as a denied
batch without partial dispatch. A trace must not imply unread targets were probed.

## Opt-In Dependency Screening

The default `screening="provisional"` preserves the existing hypothesis-only
behavior for compatible replay. It reports `screening_policy="provisional-v1"`,
`candidate_screened=false` and no evidence assessment. In particular, an old
observation-backed checkout hypothesis is not silently relabeled a diagnosis.

Select `screening="dependency"` on `ShadowRunner`, `run_frozen` or `run_pair` to
use [the declared-scope policy](../agent/evidence_policy.py), version
`dependency-screen-v1`. This is still a separate, non-executing shadow path.

```python
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import run_frozen

replay = run_frozen(inputs, FixedProbePolicy(), screening="dependency")
assert replay["report"]["execution_authorized"] is False
```

Both policies receive the same screening contract: the failed step's reviewed
workflow anchor, its reachable declared dependencies, and the existing condition/
status interpretation. No component state or expected target is read to construct
that contract. Before a candidate passes this conservative screen:

- Every service in that scope must have a successful component/resource read,
  known observed state, a canonical runtime identity and a latest sample within
  30 seconds of the shared cutoff. Unknown, stale, missing, conflicting and
  unsupported states do not become healthy by default.
- Candidate condition and latest target-specific citation must match the public
  evidence. A numeric value without a reviewed pressure interpretation is not a
  screened resource diagnosis; the looser provisional API remains distinct.
- Other observed downstream or independent anomalies, declared unresolved
  alternatives and declared contradicting evidence block that candidate.
  Cyclic traversal is bounded; mutually dependent anomalies remain unresolved.
- The broker must still accept every reference. A structural assessment alone
  cannot bypass its issued-ID/freshness checks. `candidate_screened=true` appears
  only after both screening and the final broker proposal check pass.

The gate reviews **only projected evidence already shown to that policy**, not
hidden rows, an injector manifest or expected answers. It never selects a
replacement service. A premature proposal produces a recorded `proposal_deferred`
turn with fixed assessment fields; the policy can choose more reads, change its
hypothesis or abstain. The same four-decision/eight-read/time/context/usage limits
continue across deferrals. Security denials for unknown targets or unshown IDs
remain terminal. A deferred proposal spends its decision, not a new tool call.

Reports add `screening_policy`, `evidence_assessment` and `candidate_screened`.
These are additive fields; the input bundle schema and historical raw results
are unchanged. A screened candidate is **not an attributed root**, an accepted
repair plan, a live health assertion or proof of causality. The broker's direct
provisional API is not converted into a diagnosis endpoint or an OS security
boundary.

This first policy requires full declared-scope coverage and uses existing
normalized health labels. It can reject legitimate cases involving optional
dependencies, application errors hidden by readiness probes, or independent
resource anomalies. Missing/incorrect graph edges can hide the real alternative:
a regression deliberately shows that removing Redis's declared path can let a
checkout candidate pass this narrower screen. That is a documented limitation,
not causal correctness. No observed spans or complete-topology attestation exist.

A large scope can exceed the eight-read ceiling; the policy must abstain rather
than raise limits. Extra contract/feedback bytes count against prompt admission.
Even when four turns suffice structurally, the default reported-token threshold
may not suffice for a real model. A future pilot must predeclare and validate its
usage budget, not silently expand it after a failure. Full coverage is a
conservative information gate, not evidence of an optimal probe policy or LLM
advantage over the fixed baseline.

Offline commands, with no provider construction:

```bash
python -m eval.shadow_controls --screened
python -m eval.shadow_replay replay --screened \
  --inputs output/shadow/pilot-001/inputs.json \
  --output output/shadow/pilot-001/fixed-screened.json
```

Forty policy tests cover scope, freshness/identity, conflicting views, ambiguity,
cycles, missing edges, exact budget preservation, broker precedence, compatible
legacy mode and matched replay. Scripted deferral/change-of-target behavior is
not actual inference or general fresh-evidence replanning. Reviewing archived
pilot002 receipts yields incomplete evidence for both premature proposals and
unknown telemetry; this is a **post-hoc receipt check**, not a new model trial or
a measurement of what the model would do after feedback. No Groq request, Docker
operation, physical fault or remediation has validated this new mode.

## Pilot Limits

| Limit | Default ceiling |
| --- | --- |
| Decision attempts | 4, including invalid replies and provider failures |
| Retained tool calls | 8, shared with the underlying session |
| Requests per read decision | 3; an over-budget batch is denied before dispatch |
| Elapsed time | 180 seconds from the session start |
| Provider timeout per attempt | At most 20 seconds, limited by remaining time |
| Prompt envelope | 32 KiB per attempt |
| Cumulative admission units | 131,072 |
| Output request cap | At most 768 tokens by default; configurable up to 1,024 |
| Known reported-token stop threshold | 8,192 by default |

Admission units reserve prompt-envelope UTF-8 bytes plus the output-token cap
and a fixed 1,024-unit overhead on every attempted decision. Reservations are not
refunded on failure. These are conservative local admission units, **not measured
provider tokens, money or a proven tokenizer bound**. Prompt envelopes omit
provider-side formatting/hidden overhead. Provider-reported input/output totals
are recorded separately and must be consistent; missing/inconsistent usage stops
the run without another provider attempt. Failed requests retain their admission
charge and unknown usage, not an invented zero-token measurement.

The reported-token threshold is checked after each reply, so one request can
overshoot it. An overrun is retained and its decision is discarded. This is not a
guaranteed billing cap. Exact preflight token accounting requires a validated
model-specific tokenizer/provider contract. No such guarantee is claimed here.

Time checks are cooperative before/after work, not hard process preemption.
The SDK adapter binds output and request-timeout parameters and requires retries
disabled. There is no hidden provider retry loop. A session can be claimed by
only one runner; new runner objects cannot reset its budgets. Restart-safe
budgets, separate OS credentials and cancellation of blocked operations remain
future work. The caller still has the local user's OS permissions.

## Provider Adapter

`agent.shadow_provider.configured_groq_driver()` is an explicit library factory
that reuses the existing Groq configuration with retries disabled. Importing the
shadow modules does not load `.env`, a provider SDK or the executor. Calling the
factory intentionally loads provider settings; using its driver sends the
sanitized project context to Groq and requires operator approval. There is no
default live-model CLI or automatic provider call.
Automatic LangSmith tracing is disabled around both the graph run and adapter
invocation, including when a global tracing environment flag is enabled.

The adapter has been tested through the real Groq-compatible SDK using an HTTP
mock, including output/timeout binding and usage extraction. An explicitly
approved real Groq synthetic smoke trial is recorded below. It failed diagnosis;
successful model-led investigation has not been established. Earlier live Groq
repairs belong to the original workflow, not the new shadow path.

## Frozen Input Replay

`eval.shadow_replay` exports an input-only bundle from one read-only SQLite
transaction. Its scope is the reviewed registry, the 900 seconds before the
cutoff, and evidence available/retained at that cutoff. It includes the declared
topology and failed-transaction trigger, never mutable episode state, expected
targets, scoring labels or provider credentials. Unsupported/malformed in-scope
records, more than 4,096 rows, payloads over 8 KiB or a bundle over 8 MiB cause
failure rather than silent truncation. Normal diagnostic per-tool sampling
limits still apply and disclose truncation.

Component detail and artifact paths are replaced/removed; only the existing six
reviewed numeric measurement fields survive. Transactions and log batches use
their strict enum-based schemas. IDs, provenance, payload/column agreement and
measurement/collection/availability times are checked. Late backdated records
are excluded. Failed export is not an eligible successful trial.

The bundle has a canonical SHA-256 digest. Loading verifies the digest and
sanitized schema, rejecting duplicate JSON fields, non-finite values, extra
bundle fields and duplicate/unsorted evidence IDs. The digest detects accidental
changes against the recorded value, **not a signature or source attestation**:
a local operator could change both data and digest. `source_kind` is a caller
declaration, not proof that an observation occurred. Retain an independent
trusted manifest for a formal study.

Replay reconstructs a disposable local store with original observation and
availability times. It does not use the live writer's wall-clock stamping or
pruning. New sessions/policies share the same frozen data, ceilings, initial
context and response projections, not necessarily the same read choices.
`run_pair(inputs, comparator)` checks input/context parity; `run_frozen` records
the input digest, source-file hashes, installed package versions, explicit limits
and the full runner report. Source hashes are local provenance, not a clean-Git
or complete-environment attestation. A fresh replay session does not implement
restart-safe budgets for a live incident.

The CLI is deliberately offline; it never constructs a provider driver:

```bash
python -m eval.shadow_replay freeze-synthetic --case payment \
  --output output/shadow/pilot-001/inputs.json
python -m eval.shadow_replay replay \
  --inputs output/shadow/pilot-001/inputs.json \
  --output output/shadow/pilot-001/fixed-policy.json
```

For a trusted retained store, use `freeze --store PATH --symptom SYMOBS-ID
--as-of ISO-UTC --topology PATH --output PATH`; the topology JSON must match
`DependencySnapshot`. Input/report files use exclusive creation and owner-only
permissions. Use a new trial directory rather than overwriting a failed attempt.
Keep artifacts private under ignored `output/` until separately reviewed.
These data can still identify the deployment and are not automatically anonymous.
An explicitly supplied model driver can send projected bundle data externally;
the library's model path requires the same operator approval as the adapter.

## First Real Shadow Smoke Trial

On October 22, 2026, one approved synthetic payment-case trial used
`openai/gpt-oss-120b` through Groq, without mocked/cached inference. Both policies
received the same frozen input digest and implementation snapshot. The fixed
policy proposed payment; the model made **one provider request and zero tool
reads**, then proposed `checkoutservice` from frontend transaction evidence alone.
It also placed condition names in `unresolved_alternatives`, which requires
registered service IDs. The broker denied the terminal proposal. No fault,
Docker operation or repair ran; the report never authorized execution.

The request reported 1,684 input and 101 output tokens (1,785 total). This single
development failure is not an accuracy estimate, timing comparison or evidence
of model advantage. Raw inputs/reports, source hashes and the failed outcome are
retained privately. Afterward, the prompt clarified target-specific evidence
and service-ID alternatives; the model-visible schema now lists registered IDs.
Offline regressions cover the denied output and schema. The revised prompt was
then tested in the following three-case development batch; it did not establish
successful diagnosis.

## Three-Case Real Model Pilot

A separately approved October 22 batch froze the revised prompt, sanitized
inputs, expected controls, limits and source/package manifests before any model
call. The model and fixed policy used identical initial information/tools and
fresh sessions. Case labels and expected targets were scored afterward, never
supplied to the model. All three inputs had a failed synthetic checkout; their
component observations differed. These are hand-authored, pre-normalized
software controls, not physical incidents, raw-signal inference or a held-out study.

The unchanged model was Groq-hosted `openai/gpt-oss-120b`, temperature zero, with
cache and SDK retries disabled. Eight of twelve authorized requests were used.
No prompt/budget retuning, reruns, Docker operations or repairs occurred.

| Control | Real requests / retained tool calls | Provider-reported tokens | Recorded outcome |
| --- | --- | --- | --- |
| Payment | 2 / 3 | 5,671 | Read checkout/cart/Redis, then proposed checkout with an incompatible condition. Broker denied; payment was not inspected. |
| Redis | 2 / 3 | 4,761 | Read checkout; two unsupported checkout-resource reads returned unavailable in that revision. Proposed checkout with unresolved downstream alternatives. Valid as a provisional clue, wrong control target. |
| Unknown telemetry | 4 / 3 | 9,727 | Two invalid replies, then reads including payment UNKNOWN. Final reply crossed the 8,192 reported-token threshold; guard abstained. Not an accepted model abstention. |

**None met the predeclared diagnostic control criteria.** The fixed policy met
all three controls after eight reads per case. This small development batch is
not an RCA accuracy estimate or a claim of general model performance. No model
advantage has been shown. Total reported usage was 20,159 tokens; timing includes
deliberate request spacing and is not diagnostic latency or MTTR.

The original one-off scorer incorrectly counted the token-limit fallback as a
correct unknown-case decision. Original files remain intact; a separate corrected
assessment applies the predeclared rule that guard/provider/limit stops are
failures, not correct policy decisions. The public `assess_control` helper and
standalone controls now require a matching explicit terminal policy decision,
not just an expected-looking final report. Evidence checks distinguish observed
UNKNOWN from an empty window. Expected labels remain outside the policy.

The batch exposed three different boundaries: a matching clue is not a correct
root; unsupported tool inputs are not failed observations; and safe fallback is
not model success. Post-batch code rejects incompatible metric/target pairs and
returns fixed corrective feedback, with scripted bounded-correction regressions.
**Those contract changes have not had another real-model trial.** The active
repair path is unchanged and the shadow path remains disabled for live diagnosis.
Next acceptance requires a clear dependency-diagnosis objective, evidence
sufficiency for unresolved alternatives, useful schema feedback and a predeclared
budget that permits the intended investigation. Do not substitute the fixed
policy's answer and label it a model decision, or connect this path to repairs.

## Equal-Tool Fixed Policy

`eval.baselines.investigation.FixedProbePolicy` sees the same context, dependency
edges, tool catalogue and response projections through the same runner. It starts
from the failed transaction step's reviewed workflow anchor, scans declared
dependencies breadth-first within the same budget, and checks fresh known states.
It proposes a unique anomalous dependency leaf only when coverage is complete;
missing evidence, unsupported conditions and ambiguous/cyclic candidates lead
to abstention. It reads no ground-truth targets, raw SQLite rows or broker internals.

This is an explicit topology-aware deterministic control, not an optimal policy.
Its fixed scanning order can exhaust coverage; declared optional dependencies
can be misleading; and it does not interpret numeric trends like a future model
might. Equal tool access does not force identical read choices or costs. A fair
study still needs held-out tuning, repeated trials, uncertainty and full failure
accounting. No model-versus-baseline superiority is established by unit tests.

## Offline Verification

From a source checkout:

```bash
python -m eval.shadow_controls
python -m eval.shadow_controls --output output/shadow-controls.json
```

This command seeds temporary **synthetic** evidence and runs six fixed-policy
software controls: payment, Redis, payment plus unrelated CPU, unrelated CPU
alone, unknown telemetry and multiple candidates. Expected answers are checked
outside the runner and never supplied to its policy. The output identifies zero
real model calls and no causal-accuracy measurement. It loads no `.env`, Docker
or infrastructure credentials. The command is also checked by CI.

Scripted-driver tests separately exercise feedback-dependent decision paths,
budget exhaustion, invalid outputs, usage failures, unshown citations and provider
mock traffic. Synthetic usage is labelled synthetic; deterministic decisions have
no provider usage. These checks are not real inference, physical recovery, MTTR
or journal evaluation results. Live M1 controls, successful real shadow
investigation, stronger isolation, topology/trace acquisition and a controlled
comparison remain. Frozen-input tests check source independence, every permitted
tool/window's projected response parity, sanitization, integrity, limits and
failure retention. They do not supply missing live validation.
