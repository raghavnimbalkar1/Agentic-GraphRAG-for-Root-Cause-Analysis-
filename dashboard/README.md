# Dashboard

Streamlit views for live observations, graph investigation, incident evidence and
controlled research results.

## Run

After configuring the environment and starting the infrastructure:

```bash
streamlit run dashboard/app.py --server.address 127.0.0.1
```

Open [localhost:8501](http://localhost:8501). Live investigation requires Neo4j,
one API worker and a fresh collector. Archive-backed evaluation views can render
without Neo4j.

## Views

| Tab | Content |
| --- | --- |
| Start Here | Project context and runtime readiness |
| Transaction Symptoms | Read-only checkout history, failed steps, symptom episodes, freshness and retained evidence |
| Live RCA Console | Observations, controlled fault triggers and incident evidence |
| Dual Graph & Architecture | Service dependencies and SOP relationships |
| Live Duel vs Baselines | Isolated, explicitly invoked model comparisons |
| Incident History | Reports addressed by collector incident ID |
| Evaluation Results | Versioned experiment records and labeled historical results |
| Autonomy Run | Collector-driven campaign outcomes and denominators |

Observe-only monitoring is displayed as delivery disabled. Live fault controls
require the automatic collector, API and healthy baseline; comparisons may make
model requests. Do not infer model connectivity from provider configuration or
autonomous success from fixed-SOP records.

Transaction Symptoms reads the local evidence store without initializing or
modifying it. It needs neither a model call nor a Neo4j query for its own data.
The monitor can be stopped while historical episodes remain visible. A stale
observation is not current health; an observed recovery is not an agent repair.
Expired evidence references are reported explicitly. This tab provides no
injection, reset or execution controls.

`app.py` assembles the views; `components/` contains graph, report, log and
comparison renderers. Runtime reports are read from ignored `audit/`; identified
evaluation evidence is under `eval/results/verified/`.

See [Operations](../docs/OPERATIONS.md) and [Project Status](../docs/STATUS.md).
