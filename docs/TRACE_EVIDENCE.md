# Trace Evidence and Observed Calls

This M1 foundation imports one local OTLP/JSON export into a normalized private
snapshot. It does not run a collector, contact an endpoint, load `.env`, call a
model, change Neo4j or authorize a repair. Live image/export validation is pending.

```text
Operator-scoped OTLP/JSON file
  -> bounded structural parsing
  -> registered-service span projection
  -> same-trace direct CLIENT/SERVER parent joins
  -> normalized spans + observed calls + explicit coverage gaps

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
merely because their spans have old timestamps. There is no replay integration,
SQLite trace archive or continuous background ingestion in this increment.

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
missing spans, observed recovery and every failed collection. Add reviewed
retention/read-only model tools separately; this snapshot is not consumed by
the dashboard, frozen shadow replay or live remediation workflow.

Offline verification: 74 synthetic trace tests pass, and the full suite has
734 passing tests with three opt-in live checks skipped. The command above was
run on the committed example and returned two spans/one observed call. None of
these checks used live trace traffic, Docker, a provider or a remediation.
