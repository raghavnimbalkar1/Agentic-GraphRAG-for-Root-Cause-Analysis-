# Trace Evidence and Observed Calls

This M1/M2 adapter imports one local OTLP/JSON export into a normalized private
snapshot. Optional local retention, read-only shadow access and frozen replay are
implemented. None runs a collector, contacts an endpoint, loads `.env`, changes
Neo4j or authorizes a repair. The shadow runner can pass this context to an
explicitly approved model; no such trace-bearing model trial has run. Live
image/export validation is pending.

```text
Operator-scoped OTLP/JSON file
  -> bounded structural parsing
  -> registered-service span projection
  -> same-trace direct CLIENT/SERVER parent joins
  -> normalized spans + observed calls + explicit coverage gaps
  -> optional validated SQLite retention (actual local stored time)
  -> opt-in trace_window / version 2 frozen shadow replay

Declared DEPENDS_ON graph and Skill graph remain unchanged.
```

## Operator Command

The committed example is **synthetic**, not captured application traffic:

```bash
python -m simulation.trace_import \
  --input eval/fixtures/trace_example.json \
  --output output/traces/example-001.json \
  --window-end 2026-01-01T12:01:00+00:00 \
  --window-seconds 60 \
  --source-kind synthetic
```

It retains two spans and one observed checkout-to-payment call. Reusing the
output fails rather than overwriting evidence. For a real operator-scoped file,
use `--source-kind retained-export` and its actual observation-window end. That
flag is a caller declaration, not authentication or proof of a real collection.

Input must be one UTF-8 JSON `ExportTraceServiceRequest` object containing
`resourceSpans`, not binary Protobuf, compressed data, Jaeger JSON, an array of
batches or newline-delimited requests. No full OTLP receiver compatibility is
claimed. Regular files only; direct input/output symlinks are refused. New
snapshot files are owner-only (`0600`), and newly created immediate output
directories use `0700`. Trusted operators supply paths; parent-directory
permissions, existing raw exports and OS-level credentials remain their responsibility.
The file-opening safeguards target the supported local macOS/Linux environment;
Windows support has not been validated.

## Projection Contract

The adapter follows the OTLP field representation: hexadecimal trace/span IDs,
integer enum values and integer/decimal-string nanosecond timestamps. Unknown
message fields are ignored after global structure checks. See the official
[OTLP JSON encoding](https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding)
and [trace message definitions](https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/trace/v1/trace.proto).

Retained fields are registered logical service, optional canonical container ID,
trace/span/parent IDs, exact integer start/end nanoseconds, span kind and status
code. `service.name` must exactly match the reviewed registry; unknown/renamed
services are counted and omitted without retaining their names. No automatic
registration or repair permission results. Missing or noncanonical container
identity remains unknown; an export's canonical ID is still an unattested claim.

Span names, URLs, headers, paths, arguments, error/status messages, events, links,
trace state, arbitrary resource attributes and SDK metadata are not retained or
hashed. This is a lossy fixed projection, not a general log redactor. Trace IDs
and timestamps still correlate activity and are **not anonymous**. Do not send
an export or snapshot to a provider without separate review and approval.

Each `SPAN-...` ID hashes only the normalized retained fields and registry digest.
The topology revision identifies the normalized spans, selected window, source
declaration and coverage counts. Neither digest is a signature, observation
attestation or proof against a local operator modifying data and hashes.

## Observed Call Rules

- A caller must have a `CLIENT` span and a different registered service must
  have its direct `SERVER` child, matching both trace ID and parent span ID.
  Timing coincidence, a span name, `peer.service` or a link never creates an edge.
- Each observed call retains supporting client/server evidence pairs, pair count,
  server-error pair count and first/last server times in the selected window.
  These are span-pair counts, not unique business transactions or an error rate.
- Unset status is not healthy. Server error status is an exported observation,
  not root attribution. Missing parents and unsupported cross-service kinds are
  counted. Unsupported asynchronous producer/consumer links do not become RPCs.
- Parent cycles and conflicting reuse of a trace/span ID reject the whole
  export. Exact normalized duplicates are counted once. The aggregated logical
  service graph may legitimately contain cycles across different calls.
- A server outside its client's time interval creates a clock-order warning.
  Context-based edges remain visible, but no cross-host latency or clock
  synchronization claim is made. Reversed individual span intervals are rejected.

These rules use OpenTelemetry's
[SpanKind and parent-context semantics](https://opentelemetry.io/docs/specs/otel/trace/api/#spankind).
They identify exported request relationships, not causal dependency completeness.
No sampled edge removes or silently replaces a declared `DEPENDS_ON` relationship.

## Limits and Availability

| Limit | Ceiling |
| --- | --- |
| Input file bytes | 1 MiB, read with one extra byte to detect excess |
| JSON nesting / nodes | 32 levels / 65,536 values, including discarded fields |
| Input spans | 256, including unregistered and out-of-window spans |
| Selected window | 60, 300 or 900 seconds |
| Normalized output | 512 KiB |

Oversized, malformed, duplicate-key, non-finite, conflicting or unsupported
selected data fail closed without a successful partial snapshot. Unregistered
groups are skipped with counts, not validated as registered-service spans.
Empty exports produce empty sampled evidence, never a healthy or complete graph.
Limits bound data volume, not wall-clock time, process memory or filesystem growth.
A disk failure can leave a partial output file, which must not be reused as a
valid snapshot; output names are never automatically retried or overwritten.

Windows select spans by **end time**; a retained span may start before the window.
Both members of a call pair must be retained. Missing parents can therefore mean
sampling, another export, window filtering or missing instrumentation. Services
without retained spans are listed, including services that may not be instrumented.
Absence is not proof that a service/edge disappeared or that a request succeeded.

`available_at` is local import time, separately recorded from the earlier span
window. These files do not become evidence available to a historical decision
merely because their spans have old timestamps. SQLite retention also records
the actual local `stored_at`. Both must precede a replay/investigation cutoff.
There is no continuous background ingestion.

## Retention And Shadow Reads

Retain a normalized snapshot in an **existing operator-owned evidence store**:

```bash
python -m simulation.trace_retain \
  --input output/traces/example-001.json \
  --store audit/evidence.sqlite
```

The command does not create a missing store, accept raw OTLP, or follow direct
input/store symlinks. It revalidates the complete normalized schema and recomputes
span IDs, parent joins, retained coverage and topology revision. Extra/free-text
fields, modified derived fields and unsupported authority claims are rejected.
Digests are consistency checks, not signatures; an operator can still rewrite
data and matching hashes. Parent directories and concurrent local path changes
are outside this operator-trusted file boundary.

The additive `trace_snapshots` table is separate from component/symptom/log rows;
existing stores/readers remain schema version 1. Retention defaults to 24 hours
and is capped at 32 snapshots of at most 512 KiB each. SQLite/WAL/index overhead
is additional; this is not a disk quota. Re-importing an existing retained
snapshot does not advance its original stored time. An expired/pruned snapshot
must be retained again with a new stored time and cannot enter an earlier cutoff.

A developer-owned `InvestigationSession(..., include_traces=True)` exposes
`trace_window` for any registered service with a 60-, 300- or 900-second window.
The default remains the original four tools. Disabled/invalid, duplicate and
failed trace requests consume the same eight-call budget as other diagnostics.
Time checks remain cooperative; successful responses consume the same byte limits.
No arbitrary path, trace query, endpoint or executable argument is model-selectable.

Each read selects the **latest eligible export only**, independent of whether
the requested service appears. It discloses eligible/omitted exports and never
merges exporters or falls back to an older target-matching export. It returns
at most 32 matching spans: the target's spans plus direct client/server peers
inside the requested window. Truncation is explicit, and returned edges cite
only returned spans. Original snapshot coverage/window and returned-view link
gaps are separate fields. A narrow sampled export does not cover a larger
requested time window merely because it was selected.

No export, no matching spans, unset status, or absent parents means missing
evidence, not health. `SPAN-`/`TRACE-` identifiers are contextual only: they are
not issued as supporting/contradicting hypothesis citations. Current readiness
and resource candidates still need admissible component/resource observations.
Declared edges, the dependency screen and all repair permissions are unchanged.

## Frozen Replay

The trusted `freeze` command opts into traces with `--include-traces`:

```bash
python -m eval.shadow_replay freeze \
  --store audit/evidence.sqlite --symptom SYMOBS-REPLACE-WITH-RETAINED-ID \
  --as-of REPLACE-WITH-AWARE-CUTOFF --topology path/to/declared-topology.json \
  --include-traces --output output/shadow/incident/inputs-v2.json
```

Use actual operator-supplied IDs, clocks and paths. Version 2 includes validated,
sorted trace snapshots with their original retention times from the same SQLite
read transaction. Late/backdated exports are excluded. A malformed or oversized
eligible scope fails rather than silently dropping records. The 8 MiB bundle
ceiling still applies. Replay restores availability and the optional tool in a
disposable store, not through the wall-clock retention writer.
Snapshot selection uses export-window end within the preceding 900 seconds.
Full snapshots preserve their original coverage and may contain earlier spans;
each tool read additionally filters span end times to its requested window.

Version 1 remains the default: no trace query, trace fields or added tool, and
its existing wire format/digests remain compatible. To inspect a version 2 bundle
offline, use the existing `eval.shadow_diagnose` fixed-policy command. The strong
fixed baseline has the same optional tool access but does not currently select
trace reads. No benefit from adding traces has been measured.

For any future trace-bearing Groq diagnosis, `--approve-external` alone is
insufficient: the operator must also explicitly approve the trace-ID/time/call
transfer using `--approve-trace-transfer`. The check occurs before provider
construction or a trial marker. A CLI flag does not replace human authorization
for the finite research trial. No trace-bearing external request has run here.

## Acceptance Still Required

The v0.10.5 frontend and checkout source has optional `ENABLE_TRACING=1` paths,
an OTLP/gRPC exporter and client/server instrumentation. The audit used source
revision `16a51f8dbabc4af2c4c82f81ca6d4813888c2c34`, not the newer ignored checkout.
Source links: [frontend](https://github.com/GoogleCloudPlatform/microservices-demo/blob/16a51f8dbabc4af2c4c82f81ca6d4813888c2c34/src/frontend/main.go),
[checkout](https://github.com/GoogleCloudPlatform/microservices-demo/blob/16a51f8dbabc4af2c4c82f81ca6d4813888c2c34/src/checkoutservice/main.go).

The supported Compose profile does not enable tracing or provide a collector.
Before live use, audit the other pinned images, set reviewed resource service
identities, validate propagation/export and measure CPU/memory overhead. The
8 GB local machine should not gain an unaudited telemetry stack by default.
Then run hidden-target payment/Redis and unrelated-anomaly M1 controls, preserving
missing spans, observed recovery and every failed collection. Optional retention
and frozen/shadow reads do not close these live acceptance gates. The dashboard
and active remediation workflow do not consume these traces.

Initial foundation verification (October 23): 74 synthetic trace tests passed, and the suite had
734 passing tests with three opt-in live checks skipped. The command above was
run on the committed example and returned two spans/one observed call. None of
these checks used live trace traffic, Docker, a provider or a remediation.

Retention/replay verification (October 24): 94 additional offline controls pass;
the full suite has 828 passing tests and three opt-in live checks skipped. All
30 retained version 1 development input bundles still load with their original
digests and no trace scope. A synthetic CLI-to-store-to-v2-replay walkthrough
retained two spans/one observed call and matched 36 service/window reader views.
No real exporter, trace-bearing model call, Docker operation or repair ran.
