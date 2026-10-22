# Agent Autonomy, Real Effects And Current Limits

## Short Answer

This is a **bounded, model-assisted autonomous remediation workflow**, not a
chatbot and not a general-purpose autonomous SRE. The sense/decide/act/verify loop
is real. Initial root selection is a deterministic graph heuristic, and most
faults expose only one applicable SOP. The incremental benefit of the LLM over a
good deterministic policy is a research question that has not yet been answered.

Using curated tools does not make a system fake. It limits its authority. But a
model choosing a single obvious tool is weak evidence of adaptive reasoning.
LangGraph orchestration alone also does not establish meaningful autonomy.

## Where Decisions Actually Happen

| Stage | Implementation | Dynamic behavior | Fixed knowledge / limitation |
| --- | --- | --- | --- |
| Observation | [Shared probes](../core/health.py), [collector](../simulation/telemetry_collector.py) | Read current Docker state, resource counters, Redis configuration, gRPC readiness and HTTP behavior | Fixed service set, thresholds and condition names; not a general log/trace analyst |
| Incident creation | [Episode manager](../simulation/incident_delivery.py) | Debounce repeated bad observations, assign IDs, deliver and retry | Collector favors independent graph roots; demonstrated automatic alerts are root-local |
| Root attribution | [Graph client](../graph/graph_client.py), [retriever](../agent/nodes/retriever.py) | Traverse current unhealthy dependencies; reject ambiguous/stale evidence | Hand-authored topology and deterministic policy, not learned causal discovery |
| Candidate retrieval | Q2 over `APPLIES_TO` and trigger condition | Candidate set changes with root, condition and visited skills | Exact condition matching against a maintained library |
| Model decision | [Reasoner](../agent/nodes/reasoner.py) | Real provider call chooses execute/skip/escalate and an exact candidate | Model receives the graph-selected root; it does not independently discover it |
| Actuation | [Executor](../agent/nodes/executor.py) and [controller](../agent/tools/control_gateway.py) | Mutate the real approved simulation container | Only curated operations; no generated executable code |
| Verification | [Evaluator](../agent/nodes/evaluator.py) | Fresh measurements decide success, further allowed attempts or termination | Fixed verification policy; no general autonomous investigation after the condition changes |

## Is The Answer Given By The Fault Injector?

No operational alert or graph status is written by
[fault_injector.py](../simulation/fault_injector.py). The injector changes the
environment, and the separate collector must observe it. This prevents the most
direct form of answer leakage.

That does **not** eliminate benchmark coupling. A Redis-cap threshold, known
disk-fill filename and target-specific memory/CPU probes deliberately match
known testbed fault mechanisms. The system recognizes measured symptoms within
that scope. It has not demonstrated robust diagnosis of arbitrary previously
unseen faults, and a label such as `OOM_KILLED` is not proof of its literal cause.

## Payment: A Real Application Example

The corrected October 13 dashboard trial disconnected `paymentservice` from the
simulation network. Independent telemetry reported `CONNECTION_REFUSED`.
Groq selected `Payment_Restart_SOP`; the controller restarted the service and
reconnected it. Checkout timed out in the during-fault canary, then completed
successfully after repair. The evaluator required two successful transaction
rounds before reporting recovery.

This is a genuine intervention in running Online Boutique, not a changed label
or prewritten response. Boutique's payment is a **mock payment service**, not a
bank or production merchant. The trial proves that one supported network fault
can be repaired, not that all payment failures can be diagnosed. A declined
card, application bug, removed container or unsupported crash condition is not
covered by that demonstration. See the [identified evidence](../eval/results/verified/20261014/README.md).

## Gaps That Matter

1. **Root-local alerts weaken the multihop demonstration.** The collector uses
   graph-independent roots to suppress cascaded alerts. Live repairs therefore
   show zero-hop root selection. Multihop fixture results are a different study.
2. **The active repair model does not choose diagnostic probes.** It receives a
   preselected root and a short SOP list. A separate shadow loop now selects
   retained-evidence reads, but its three-case real-model pilot failed diagnostic
   acceptance. It cannot authorize repairs; a supported provisional hypothesis
   is not an established root. See [shadow evidence](SHADOW_INVESTIGATION.md#three-case-real-model-pilot).
3. **Changed conditions are stopped, not re-diagnosed.** Retrieval retains the
   original root condition and Q1 is skipped on later attempts. Preflight safely
   blocks stale execution, but the next rationale can still repeat stale context.
4. **Some repairs are temporary or injection-specific.** CPU capping leaves the
   busy-loop running; disk cleanup knows one filename; runtime Redis repairs do
   not necessarily survive restart. Containment is not durable cause removal.
5. **Limited choice limits the model claim.** The adservice HIGH_CPU case has two
   admissible candidates; many other cases have one. We need trials where a
   different observation legitimately changes the best permitted action.
6. **Safety is narrower than non-regression.** Name validation blocks invented
   operations, not harmful allowed actions. Redis flush loses data; the 0.1-CPU
   cap degraded readiness. There is no general automatic undo mechanism.

## Next Implementation Priorities

These are planned upgrades, not claims about the present implementation.

| Priority | Change | Acceptance evidence |
| --- | --- | --- |
| 1 | Keep symptom origin, observed candidates and final attributed root distinct; accept real upstream transaction failures without labeling a known root in the alert | Hidden-target payment/Redis dependency incidents, plus unrelated and simultaneous-fault controls |
| 2 | Add a bounded set of **read-only** investigation tools: dependency readiness, recent resource windows, relevant trace spans and sanitized errors | Model chooses useful probes from competing hypotheses; all probe calls and evidence IDs retained |
| 3 | Re-localize and re-retrieve after a changed condition, clearing stale candidates and using new evidence | HIGH_CPU-to-DEGRADED and root-recovered/dependent-failed tests; old operations remain blocked |
| 4 | Add meaningful same-condition choices with cost, disruption, preconditions, reversibility and durable/temporary outcome labels | Matched trials where changing measured constraints changes the appropriate action; compare with a no-LLM policy |
| 5 | Version topology and discovery inputs; support adapter-based service registration | Held-out service names/edges without editing diagnostic logic; stale/missing topology fails safely |
| 6 | Add relapse monitoring and approved rollback where possible; require approval for destructive data operations outside disposable simulation | Recovery persists through a defined soak/restart check; failed actions do not silently worsen the system |

Do not gain apparent flexibility by allowing arbitrary model-generated shell
commands. Keep the execution allowlist while making evidence collection and
plan revision more adaptive. The strongest publication claim would concern the
measured contribution of this architecture, not an assertion that all RCA has
been automated.
