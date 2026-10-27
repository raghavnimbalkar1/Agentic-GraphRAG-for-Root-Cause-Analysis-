# Frozen Shadow Campaigns

`eval.shadow_campaign` prepares repeated synthetic controls and scores supplied
reports. It has **no model-execution command**, creates no provider client, and
cannot inspect Docker, inject a fault or run a remediation. Preparing a campaign
does not authorize external transfer or reserve a provider allowance.

This addresses a research-record gap after the October 26 complete development
pass: one successful batch is not repeated reliability, and reused development
cases are not independent held-out evidence. The
[earlier results](SHADOW_REPLANNING.md#complete-same-source-citation-validation)
remain separate and unchanged.

## Prepare

A trusted local JSON plan contains:

| Field | Meaning |
| --- | --- |
| `repetitions` | Integer from 1 to 3; every case gets every declared repetition |
| `cases[].case_id` | Unique lowercase identifier, with digits/hyphens; never a path |
| `cases[].cohort` | `development` or `prospective`; not an independently verified hold-out |
| `cases[].inputs` | One or two ordered frozen-input file paths |
| `cases[].controls` | One separate `EpochControl` per input, including its exact digest |

Each control specifies its consecutive epoch number, input digest, expected
proposal or abstention, clue service and optional clue status. Controls never
enter a policy's input. Input bundles must be canonical, trace-free version 1
synthetic evidence; retained observations are rejected. Reusing the same input
sequence under another case name is rejected: use repetitions instead.

```bash
python -m eval.shadow_campaign prepare \
  --plan /absolute/path/to/plan.json \
  --output output/campaign-001
```

Input paths in the plan are resolved from the command's working directory. The
output directory must not exist. Preparation creates an owner-only directory and
exclusive owner-only files, copies the inputs, and freezes:

- The repetition-major schedule and every planned trial identity.
- Separate controls, input digests and fixed-policy preflight reports/prompts.
- Diagnosis source/dependency hashes, the campaign tool hash and a source archive.
- A **proposed**, not approved, call ceiling: four decisions per epoch per repeat.

The diagnosis protocol stays `two-epoch-v1`, `time-indexed-v1` and
`bounded-coverage-v1`, with its existing per-epoch and incident limits. The
deterministic policy runs once per case for preflight; those runs are not counted
as model repetitions. A failed fixed control is retained and returns exit code 2.
Invalid input or a reused output path returns 1. No partial preparation grants
authority to run a model.

## Assess

After a separately authorized runner has retained its reports, supply a local
JSON mapping from planned trial IDs, such as `case-a-r01`, to their replanning
snapshot paths. Paths are resolved from the command's working directory.

```bash
python -m eval.shadow_campaign assess \
  --campaign output/campaign-001 \
  --reports /absolute/path/to/report-map.json \
  --output output/campaign-001/assessment-001.json
```

Assessment reloads the frozen inputs and requires the original diagnosis source,
packages and campaign tool. It retains every planned slot, including missing or
invalid reports. New cases cannot be inserted after preparation. It checks:

- Profile, source, input/epoch identities and frozen topology/symptom/cutoff.
- Explicit model terminals against separate controls; a guard fallback is not a
  model abstention.
- Evidence support by recomputing the proposal screen, rather than trusting the
  stored `candidate_screened` flag; abstention support is also recomputed.
- Recorded attempt counts/order, output ceilings and known usage reconciliation.
- Continued recorded dispatch after an unknown-usage or rate-limit stop.
- Identical complete transcripts submitted as separate repeats; these require
  review and cannot establish independent repetitions.

Incomplete, duplicate, mismatched or failed controls block the aggregate pass.
Missing usage remains unknown. Duplicate reports have no aggregate token total,
to avoid counting the same record twice. Development and prospective trial counts
are kept separate. Assessment never dispatches a request or retries a failed
trial. It writes only to a new output file; invalid reports remain untouched.

## Tokenizer Cache

Campaign preparation uses the fixed policy and needs no tokenizer download.
Separate local GPT-OSS token estimates and real structured model runs do need
the reviewed vocabulary. The tokenizer's default cache can be temporary. Set a
persistent ignored cache and initialize it explicitly before a model campaign:

```bash
export TIKTOKEN_CACHE_DIR="$PWD/build/tiktoken-cache"
python -c 'import tiktoken; tiktoken.get_encoding("o200k_harmony")'
```

On a cache miss this fetches public vocabulary data whose hash is checked by the
installed tokenizer package; it sends no project prompt and makes no LLM call.
It needs network access for that fetch. Schema/wire unit tests instead use an
explicit arithmetic tokenizer mock and do not certify provider token counts.

## Evidence Limits

This is a local consistency and scoring tool, **not independent attestation of
SDK origin or a tamper-proof execution ledger**. Supplied report fields alone
cannot prove fresh external inference. An actual model campaign still needs its
frozen approval, bounded runner, pre-dispatch and returned/failed receipts, prompt
records, provider usage, global stop rules and independent reconciliation.

`prospective` means designated before its future model batch. The researcher can
still know the case, so this label is not a claim of independent blinding. A
proper held-out study also needs a documented separation from development and no
post-result tuning or selective reruns. Freeze a new campaign after any source
change; do not pool old results across revisions.

Passing these controls does not establish causal accuracy, live freshness,
procedure authorization, physical recovery, M1-M3 completion or main promotion.
Positive proposals may already have expired during provider waits. Fresh-evidence
checks and guarded live acceptance remain separate gates. Raw campaigns, model
records and research notes belong under the ignored `output/` directory.
