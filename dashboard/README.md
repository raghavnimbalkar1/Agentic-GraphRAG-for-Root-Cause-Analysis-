# RCA Demo Dashboard

A Streamlit dashboard for presenting the Agentic GraphRAG RCA system live —
instead of reading terminal logs during a demo, you watch the dependency graph
go red when a fault hits and green again once the agent resolves it.

## Run

```bash
# 1. Infra + simulation must be up
docker compose up -d
docker compose -f simulation/docker-compose.yml up -d

# 2. Agent server must be running (the dashboard POSTs alerts to it)
python -m agent.main          # serves :8888

# 3. Launch the dashboard
pip install -e ".[dashboard]"  # streamlit + pyvis + pandas
streamlit run dashboard/app.py # serves :8501
```

## Tabs

| Tab | What it shows |
|---|---|
| **🚨 Live RCA Console** | Pick a fault + target, click **Inject**. The service dependency graph (read live from Neo4j) turns the root cause **red** and its blast radius **amber**, then back to **green** as the agent autonomously resolves it. Below: the reconstructed ReAct pipeline timeline (ingest → Q1 root cause → Q2 SOP → LLM reason → sandbox exec → verify) and the full RCA report. |
| **📜 Incident History** | Every audit report in `audit/` — table of alert ID, root cause, status, MTTR, tokens, SOPs, plus a per-incident inspector. |
| **📊 Evaluation Results** | Phase 7 RQ1/RQ2 benchmark from `eval/results/benchmark_all.json` — GraphRAG vs Zero-Shot vs Vector RAG, with comparison charts. |

## How the live transition works

`simulation.fault_injector` functions are synchronous and bundle: break the real
container → mark the service unhealthy in Neo4j → POST the alert (the agent
resolves it synchronously). The dashboard runs the injection on a **background
thread** while the main thread **polls Neo4j health** and redraws the graph each
tick, so the red → green transition is genuinely live. When the thread finishes,
the freshly written `audit/rca_<id>.json` is read and rendered.

## Files

```
dashboard/
├── app.py                      # main: 3 tabs, control panel, live runner
└── components/
    ├── graph_viz.py            # pyvis network from Neo4j, health-coloured
    ├── rca_report.py           # audit JSON → report card
    └── agent_log.py            # background fault runner + ReAct timeline
```
