# Time-Indexed Shadow Diagnosis

This opt-in research increment addresses evidence presentation and assessment,
not a new repair path. The active agent, default shadow layout, diagnostic tools,
dependency screen, per-read charges and all budget ceilings remain unchanged.
It has not established successful model-led two-epoch relocalization or live
recovery. A separately approved model comparison is recorded below.

## Problem Observed

The October 24 model regression read all required services but proposed an
upstream checkout failure despite a downstream payment failure. In its next
epoch it named checkout but declared an unresolved payment alternative. Both
proposals were blocked. The final report contained a guard-generated abstention,
which alone did not explain what the policy actually attempted.

The older readable index repeated observations without marking which IDs were
latest. A continuation can include both an earlier failed payment observation
and a newer healthy payment observation. That creates a plausible presentation
problem, not proof that formatting caused either model error. Full model
development history remains in the [replanning record](SHADOW_REPLANNING.md#synthetic-model-development-record).

## Evidence Layout

`time-indexed-v1` uses the same displayed component/resource reads. For each read
its native JSON index shows all samples tied at the newest measurement instant,
including their exact IDs, statuses, qualities and whether identity is present.
Numeric values remain visible when supplied. Time comparison uses aware
timestamps, including equivalent instants in different timezones, not ID or
string order.

The index also records earlier-sample counts, freshness at the frozen cutoff,
age, latest conflicts/truncation and disclosed prompt omissions. Unread scope
entries are marked `unread`, not UNKNOWN. Failed or repeated reads are not
silently reconciled into a confident latest state. The index does not rank
services, infer a cause or propose a replacement answer.

Every underlying displayed fact, timestamp, identity and historical ID remains
in the inner losslessly encoded view. The literal index is an additional display
of those facts, not permission to omit inconvenient history. Existing bounded
reader/prompt truncation still applies and is disclosed; this is not an export
of all possible telemetry. Both fixed and model policies receive the same layout
when it is selected. The broker still checks original evidence, not the index.

Use the explicit option with two ordered, separately frozen inputs:

```bash
python -m eval.shadow_replan \
  --inputs output/shadow/incident/epoch-001.json \
  --inputs output/shadow/incident/epoch-002.json \
  --profile two-epoch-v1 --evidence-layout time-indexed-v1 \
  --output output/shadow/incident/temporal-fixed.json
```

This CLI runs only the fixed policy. It constructs no external model client,
collects no new telemetry and dispatches no operation. Omitting the layout option
retains `legacy-v1`; no outcome automatically switches layouts.

## Offline Feasibility

The preflight compares both layouts on identical frozen inputs and unchanged
`two-epoch-v1` caps. It checks decoded underlying facts and fixed decisions,
records actual prompt sizes and can apply the local GPT-OSS tokenizer/schema
estimator without credentials or an SDK client:

```bash
python -m eval.shadow_replan_preflight \
  --inputs output/shadow/incident/epoch-001.json \
  --inputs output/shadow/incident/epoch-002.json \
  --estimate-gpt-oss-tokens \
  --output output/shadow/incident/layout-preflight.json
```

An initial time-indexed display exceeded the local 8,000-token rate reservation.
After reducing duplicated display metadata, a retained payment-to-checkout
continuation reserved at most 7,999 tokens versus 7,953 with the older layout.
A retained unknown continuation reserved 7,316 versus 7,053. Reservations include
output allowance and an estimator margin. These are local estimates on two
related constructed inputs, not measured provider cost, diagnostic improvement
or a guarantee that a different model read plan fits. The payment case has very
little admission headroom. No cap was raised to make either preflight pass.

Exit 2 retains a failed parity, incomplete fixed replay or estimated feasibility
result; it is not a request to retry until passing. All output/start files are
owner-only and exclusive. Keep them outside version control.

## Post-Run Assessment

`eval.shadow_replan_assess` reads a retained snapshot and a separate controls
manifest. Freeze controls before inference for any prospective comparison; do
not derive expected answers from the model prediction. Controls bind the source/
dependency manifest digest, layout, budget profile, evidence/decision limits,
ordered input digests, expected terminal decision and required component clue.

The assessor includes missing, extra and misaligned epochs rather than dropping
them. An expected proposal requires an explicit policy terminal, dependency-screen
acceptance, known usage and a matching component clue. A guard-generated
abstention cannot pass as a correct explicit abstention. Output separates:

- Frozen diagnostic-control adherence.
- The last proposed target and its dependency-screen rejection, when retained.
- Policy termination versus guard fallback and usage completeness.
- Whether the snapshot records a current candidate, without independently
  asserting wall-clock freshness or authorizing execution.

```bash
python -m eval.shadow_replan_assess \
  --report output/shadow/incident/replanning.json \
  --controls output/shadow/incident/controls.json \
  --output output/shadow/incident/assessment.json
```

The manifest has `profile`, `evidence_layout`, `implementation_digest`,
`evidence_limits`, `shadow_limits` and consecutive `epochs`. Each epoch declares
`epoch`, `input_digest`, `expected`, `clue_service` and optional `clue_status`.
`expected` is either `{outcome: proposal, service_id, condition}` or
`{outcome: abstained, reason}`. The two-epoch profile requires both controls.
The implementation digest is SHA-256 over canonical JSON of the snapshot's
implementation manifest, using the exported `implementation_digest` helper.

This is assessment of supplied research artifacts, not cryptographic attestation
of an experiment or independent causal ground truth. A retrospective reassessment
of the five existing model cases retained all five failures, with zero new model
calls. It does not turn those tuned cases into a held-out study.

## Approved Model Comparison: October 25

One separately approved pair used source `ec8758c`, the same previously tuned
historical payment-to-checkout input bundles, Groq-hosted `openai/gpt-oss-120b`,
low reasoning effort, native structured responses, cache disabled and zero SDK
retries. The frozen order was time-indexed then legacy. Exactly 16 real provider
requests ran, eight per layout, with no extra cases or retries.

| Layout | Epoch 1 | Epoch 2 | Both Epoch Controls | Provider-Reported Tokens |
| --- | --- | --- | --- | ---: |
| `time-indexed-v1` | Checkout proposed; blocked | Checkout accepted at cutoff | Failed | 34,992 |
| `legacy-v1` | Checkout proposed; blocked | Checkout accepted at cutoff | Failed | 33,400 |

Each epoch read all eight scoped services and used four model decisions. In the
first epoch, the frozen evidence marked payment DOWN and checkout DEGRADED.
Both arms proposed checkout, and the unchanged screen returned
`downstream_anomaly_unresolved`.
With no decision allowance left, both ended in guard-generated abstention. A
post-run software control confirmed that the expected payment candidate could
pass using already displayed evidence; it did not replace the model's answer.

In the second epoch, the evidence marked payment healthy and checkout degraded.
In both arms, the model selected checkout using its latest observation ID, with
no unresolved alternative. Those decisions passed their frozen-cutoff controls.
However, both layouts named checkout in **both** epochs: neither demonstrated
the required payment-to-checkout target switch. A correct second decision alone
does not establish adaptive relocalization or make the full arm a success.

All 16 responses were schema-valid, with complete provider-reported usage totaling
68,392 tokens. That reconciles the retained response receipts and reports; it is
not an independently audited billing figure or a general schema-reliability claim.
Both accepted second-epoch candidates expired during processing, leaving no
current candidate, action authorization, remediation or verified recovery.

This single pair does not demonstrate a diagnostic advantage for the temporal
layout. It uses related, previously tuned synthetic data, not held-out incidents
or a population accuracy estimate. Shared rate admission and arm order prevent
a controlled timing comparison. The five earlier failed cases remain failures;
do not pool these epochs as new independent fault samples. Raw records and all
zero-call preparations remain in private research artifacts.

The next gate is diagnosis-policy reliability, including the distinction between
the workflow symptom and a provisional dependency explanation, and decision
scheduling that can accommodate a critique without silently widening budgets.
Further formatting-only trials are not evidence that this problem is fixed.
No screen was weakened and the new layout remains opt-in.

## Acceptance Boundary

Offline controls cover time ordering/ties, recovered versus historical failure,
UNKNOWN/missing identity/truncation, decoded-fact parity, native schema, unchanged
call limits, reconstructed blocked proposals, malformed controls, guard fallback
and private output handling. Mocked model replies are software tests only.
The increment adds 77 controls; the full offline suite passes 1,147 tests with
three opt-in live checks skipped. The three skipped checks were not rerun here.

Neither readability checks nor fixed-policy success establishes model-led
replanning, live evidence refresh, causal attribution or recovery. The approved
pair above did not pass the full diagnostic control. Further real model trials
require separate finite approval and all-outcome retention; no unused allowance
from this comparison carries forward.
The [procedure contracts](PROCEDURE_CONTRACTS.md) and remaining M1/M4/M5 acceptance
gates still precede adaptive live repair. The completed review manuscript and
website hosting are unchanged.
