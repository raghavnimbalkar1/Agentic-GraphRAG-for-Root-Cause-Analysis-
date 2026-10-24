# Time-Indexed Shadow Diagnosis

This opt-in research increment addresses evidence presentation and assessment,
not a new repair path. The active agent, default shadow layout, diagnostic tools,
dependency screen, per-read charges and all budget ceilings remain unchanged.
It has not established a new real-model diagnosis or recovery result.

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

## Acceptance Boundary

Offline controls cover time ordering/ties, recovered versus historical failure,
UNKNOWN/missing identity/truncation, decoded-fact parity, native schema, unchanged
call limits, reconstructed blocked proposals, malformed controls, guard fallback
and private output handling. Mocked model replies are software tests only.
The increment adds 77 controls; the full offline suite passes 1,147 tests with
three opt-in live checks skipped. The three skipped checks were not rerun here.

Neither readability checks nor fixed-policy success establishes model-led
replanning, live evidence refresh, causal attribution or recovery. Real model
trials require a separately approved finite protocol and all-outcome retention.
The [procedure contracts](PROCEDURE_CONTRACTS.md) and remaining M1/M4/M5 acceptance
gates still precede adaptive live repair. The completed review manuscript and
website hosting are unchanged.
