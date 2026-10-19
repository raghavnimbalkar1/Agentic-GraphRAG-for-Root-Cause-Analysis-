# Dashboard

Streamlit workspace for live observations, graph investigation, incident evidence
and controlled research results. A light theme, native sidebar navigation and
shared status indicators organize the operational and research views.

## Run

After configuring the environment and starting the infrastructure:

```bash
streamlit run dashboard/app.py --server.address 127.0.0.1
```

Open [localhost:8501](http://localhost:8501). Live investigation requires Neo4j,
one API worker and a fresh collector. Archive-backed evaluation views can render
without Neo4j.

## Views

| View | Content |
| --- | --- |
| Overview | Fresh collector health, reviewed dependency map, recent reports and transaction-monitor state |
| Transaction symptoms | Read-only checkout history, symptom episodes, component/resource windows, freshness and evidence IDs |
| Incident console | Observations, controlled fault triggers and incident evidence |
| Incident history | Reports addressed by collector incident ID |
| Dual graph | Infrastructure/skill graph selector and procedure catalog |
| Evaluation | Versioned experiment records and labeled historical results |
| Baseline comparisons | Isolated, explicitly invoked model comparisons |
| Autonomy runs | Collector-driven campaign outcomes and denominators |

Navigation renders only the selected page. Direct URLs include `/transactions`,
`/console`, `/incidents`, `/graphs`, `/evaluation`, `/comparisons` and `/autonomy`.
The overview renders without opening a graph connection. Its dependency map uses
the existing hand-reviewed localization fixture; health colours come from current
collector evidence, with provenance and freshness checks. It is a declared map,
not automatic topology discovery. Unknown observations stay unknown when Docker
or Neo4j is disconnected. Graph synchronization and agent reachability are shown
separately; the configured model name is not an inference connectivity check.

The native mobile sidebar collapses automatically. Graph canvases include
zoom/fit controls and embedded vis-network assets. Theme configuration lives in
`.streamlit/config.toml`; shared layout styling is in `dashboard/theme.py`.
The dashboard extra requires Streamlit 1.58 or later within major version 1.

Observe-only monitoring is displayed as delivery disabled. Live fault controls
require the automatic collector, API and healthy baseline; comparisons may make
model requests. Do not infer model connectivity from provider configuration or
autonomous success from fixed-SOP records.

Transaction symptoms reads the local evidence store without initializing or
modifying it. It needs neither a model call nor a Neo4j query for its own data.
The monitor can be stopped while historical episodes remain visible. A stale
observation is not current health; an observed recovery is not an agent repair.
Expired evidence references are reported explicitly. This view provides no
injection, reset or execution controls.

Component evidence can end now or at a selected checkout's measurement time.
It shows registered service conditions and available metrics, including missing
values, unknown/stale state, runtime-instance changes and partial windows. A
nearby anomaly is temporal context, not an attributed cause. See the
[window contracts and limits](../docs/SYMPTOM_OBSERVATION.md#component-evidence-windows).

`app.py` assembles the views; `components/` contains graph, report, log and
comparison renderers. Runtime reports are read from ignored `audit/`; identified
evaluation evidence is under `eval/results/verified/`.

See [Operations](../docs/OPERATIONS.md) and [Project Status](../docs/STATUS.md).
