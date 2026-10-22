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
mock, including output/timeout binding and usage extraction. **No real provider
request has validated this new loop yet.** Earlier live Groq repairs belong to
the original incident workflow and are not new shadow-loop results.

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
or journal evaluation results. Live M1 controls, the actual shadow Groq trial,
stronger isolation, topology/trace acquisition and a controlled comparison remain.
