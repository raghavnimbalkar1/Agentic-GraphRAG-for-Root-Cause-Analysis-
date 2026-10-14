# Development Recovery Evidence: October 13-14, 2026

These are identified engineering trials, **not a frozen comparative benchmark**.
Code and policy changed between attempts. The public
[sanitized export](development_rehearsals.json) preserves failures, incident IDs,
model usage, decisions, executions and selected independent observations. Its
source hashes identify retained local records; those private files are not all
distributed here. Host paths and raw execution output are omitted.

## Outcomes

| Trial | Independent outcome | Agent handling | Model tokens |
| --- | --- | ---: | ---: |
| Payment partition, before verification repair | Failed: report said RESOLVED while checkout still failed | 16.437 s | 809 |
| Payment partition, corrected path | Verified application recovery | 13.448 s | 786 |
| Persistent Redis memory cap | Verified runtime recovery after restart then flush | 36.357 s | 1,625 |
| High CPU, original SDK call | Failed: unsupported update argument | 4.588 s | 836 |
| High CPU, supported SDK call with 0.1 CPU | Failed: quota applied but readiness degraded | 43.446 s | 1,717 |
| High CPU, 0.5 CPU | Verified mitigation; overall rehearsal failed at baseline cleanup | 13.812 s | 834 |

The last trial measured approximately 51% of one CPU after applying the quota,
passed gRPC readiness and application transactions, and returned all twelve
graph service statuses to HEALTHY. Three following collector snapshots were
healthy, but only two contain measurements after the report was observed. This
is a short follow-up, not a long reliability soak. Operator baseline restoration
timed out; Docker subsequently reported manually paused. Recovery evidence and
cleanup failure must both remain visible. Capping does not terminate the burner.

The 0.1-CPU trial also created a separate DEGRADED episode that escalated. That
secondary incident is included in the export and is not counted as another
physical injection. A browser-selector attempt stopped before injection and is
documented in the local incident review, not included as a recovery incident.

All six primary incidents used real Groq calls through the application adapter,
with `openai/gpt-oss-120b`; no mocked responses were supplied. The base source
revision was `14c8823` with uncommitted changes, not one identical frozen build.
Do not turn these deliberately selected development cases into a success rate.
Handling excludes detection; token totals are not money or proof of reasoning
quality. These root-local alerts do not measure multihop localization accuracy.

## Post-Resume Baseline Check: October 15

The [separate follow-up record](post_resume_baseline_20261015.json), captured
around 01:25 IST, confirms normal adservice CPU settings and no injected
busy-loop. All twelve services passed two direct health rounds and three fresh
collector rounds; Neo4j was healthy and current. Mock checkout passed before
and after restarting the collector, which had exited while Docker was paused.
The API, dashboard and storefront returned HTTP 200. No new fault was injected,
no new incident or model decision was recorded, and no SOP executor remained.

The deferred baseline verification is complete. This is operator restoration
evidence, not another autonomous trial; the original timeout and failed overall
rehearsal remain unchanged in the exported trials and figure.

## Worked Payment Incident

Incident `INC-46DD60E9026F4F82930F83FA412CC972`:

1. A private cart/checkout session succeeded before injection.
2. The dashboard physically disconnected payment from the simulation network.
   The injector did not set a graph status or send the operational alert.
3. During-fault checkout raised a read timeout. Home/cart alone were not adequate
   indicators of payment availability.
4. The collector observed `CONNECTION_REFUSED` and created this incident ID.
5. Q1 identified payment from a payment-origin alert; Q2 supplied
   `Payment_Restart_SOP`. This is a zero-hop case, not an LLM discovery of a hidden root.
6. Groq selected that named procedure. The controlled operation restarted and
   reconnected only the approved target; the restricted executor exited cleanly.
7. Service checks and two application transaction rounds passed. Independent
   after-checkout evidence also succeeded, and the graph was healthy.
8. The agent report recorded 13.448 seconds handling and 786 tokens. Dashboard
   request to observed report was approximately 24.15 seconds, including polling.

The earlier failure matters: accepting component readiness alone produced a
false recovery report. Application-level verification is therefore part of the
tested implementation, not merely a figure annotation.

## Reproduce The Figure

```bash
python -m eval.plot_rehearsals \
  eval/results/verified/20261014/development_rehearsals.json \
  --output docs/figures/verified-20261014
```

![Development trial observations](../../../../docs/figures/verified-20261014/development_trials.png)

The generated manifest records the source SHA-256 and plotted rows. Every bar
is a single observed incident; the colors reflect independent recovery evidence,
not merely the report's status string. No cross-paper ranking is implied.
