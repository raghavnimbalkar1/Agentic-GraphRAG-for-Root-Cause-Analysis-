# Current Viva Runbook

Use this with the current README and completion progress, not the historical demo.
Live acceptance is pending; do not present offline mocks as a live incident.

## Preparation

1. Complete the README setup in an isolated deployment, keeping existing Neo4j
   data and unrelated containers intact. Confirm ownership labels and build the
   current executor image for the host architecture.
2. Start one API worker, the independent collector and Streamlit. Wait for all
   12 intended containers, a fresh complete graph snapshot and healthy probes.
3. Run `python -m scripts.check_environment --live`,
   `python -m graph.scripts.init_graph --verify-only` and the opt-in graph tests:
   `RCA_LIVE_GRAPH_TESTS=1 python -m pytest tests/test_live_graph.py -q`.
   Stop if controller connectivity, freshness or readiness fails.
4. Verify the chosen provider/model beforehand within the approved budget; the
   configured-provider status and read-only preflight do not make an inference call.
   Save a sanitized corrected report/recording as an explicitly labeled replay.

## Demonstration

1. Open Live Health. Explain that the graph is a hand-authored service dependency
   model, with health written only by observations. Show the healthy baseline.
2. Start with `python -m simulation.fault_injector inject config_drift`.
   This changes Redis policy; it does not send an alert or tell the graph the answer.
3. Follow the exact new collector incident ID. Show the measured condition,
   dependency path, potential blast, candidate SOPs, decision and approved target.
4. Show the non-root SOP result and two-round verification evidence. A green exit
   code alone is not recovery. Display terminal escalation/failure honestly.
5. Reset with `python -m simulation.fault_injector reset config_drift`, confirm
   independent healthy observations and that the collector re-arms. Before the
   next scenario, ensure the previous episode is cleared.
6. After rehearsal, demonstrate persistent Redis OOM:
   `python -m simulation.fault_injector inject redis_oom_persistent`.
   Redis restart preserves the command-line cap; fresh verification should cause
   the same-target flush fallback. Inspect both attempts and the original condition.
7. Always reset using `python -m simulation.fault_injector reset redis_oom_persistent`
   and verify the baseline. A live memory-cap repair may not survive another restart
   unless the launch configuration is reset; explain mitigation versus permanent repair.

For a failed injection or unknown observation, stop, record the failure and reset
the physical fault. Do not overwrite graph health to force a successful demonstration.
Do not delete the incident ledger to retry an uncertain operation.

## Research Views

The comparison tab uses isolated evaluation nodes, not live service statuses.
Model comparisons require explicit calls and may download embedding assets.
Explain graph-only control, common observed health inputs and different topology
access. Do not promise that one baseline always wins/loses at a certain depth.

Show corrected versioned results with model/commit/run provenance. If none exist,
show the methodology and say the experiment is pending. Historical runs must be
labeled historical; do not cite them as corrected release accuracy or recovery.

## Questions To Prepare

| Question | Defensible answer |
| --- | --- |
| Is this a chatbot? | No. It is a fault-to-verified-action workflow with constrained tools and audit evidence. |
| Does the LLM discover the root? | Q1 selects dependency-leaf candidates from observed health; the LLM chooses SOPs and supplies a labeled narrative. |
| Does reachability prove cascade impact? | No. It is potential blast; real application impact requires separate observations. |
| What happens with missing data or multiple roots? | Freshness/unknown/ambiguity guards stop automatic execution and request review. |
| Can a model invent a script? | It cannot supply executable paths; exact candidate selection and curated path/operation policies are enforced. |
| Is the host completely isolated? | No. SOPs are socketless and restricted, but the trusted host controller and injector have daemon authority. |
| Why not Kubernetes/OpenClaw? | Local Compose and the implemented controller support the research scope; older proposals are not claimed as delivered. |
| What are the current limits? | Hand-authored topology, heuristic signals, local single-worker coordination, limited fault library and pending corrected live experiments. |

After the live gates pass, update the preparation record with the tested commit,
engine/architecture, image IDs, model, chosen incident IDs and cleanup outcome.
