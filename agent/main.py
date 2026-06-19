"""
agent/main.py

FastAPI alert ingestion server.

Endpoints:
    POST /alert      → receives AlertPayload, runs the full agent graph,
                       returns RCAReport or error status
    GET  /health     → liveness check (used by Docker healthcheck)
    GET  /status     → current Neo4j + LLM connectivity status

Run locally:
    uvicorn agent.main:app --host 0.0.0.0 --port 8888 --reload

Run via module:
    python -m agent.main
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from core import get_logger, setup_logging, settings
from core.schemas import AlertPayload, RCAReport
from graph.graph_client import GraphClient
from agent.graph import agent_graph

log = get_logger(__name__)


# ── Lifespan — runs on startup and shutdown ───────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: configure logging, verify Neo4j connectivity."""
    setup_logging()

    log.info(
        "agent_starting",
        port=settings.alert_listen_port,
        llm_provider=settings.llm_provider.value,
        llm_model=settings.llm_model,
        neo4j_uri=settings.neo4j_uri,
    )

    # Verify Neo4j is reachable before accepting traffic
    gc = GraphClient()
    if not gc.health_check():
        log.error("neo4j_unreachable_on_startup",
                  uri=settings.neo4j_uri,
                  hint="Run: docker compose up neo4j -d")
        raise RuntimeError(
            f"Cannot reach Neo4j at {settings.neo4j_uri}. "
            "Start Neo4j before running the agent."
        )

    counts = gc.node_counts()
    log.info("neo4j_graph_verified", node_counts=counts)

    yield

    # Shutdown
    log.info("agent_shutting_down")


# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Agentic GraphRAG RCA",
    description="Autonomous Root Cause Analysis agent for cloud-native microservices.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness check. Returns 200 if the server is running."""
    return {"status": "ok"}


@app.get("/status")
def status():
    """Connectivity status for Neo4j and LLM provider."""
    gc = GraphClient()
    neo4j_ok = gc.health_check()

    return {
        "neo4j":        "ok" if neo4j_ok else "unreachable",
        "neo4j_uri":    settings.neo4j_uri,
        "llm_provider": settings.llm_provider.value,
        "llm_model":    settings.llm_model,
        "max_attempts": settings.agent_max_attempts,
    }


@app.post("/alert", response_model=None)
async def handle_alert(alert: AlertPayload, request: Request):
    """
    Main endpoint. Receives an AlertPayload, runs the full RCA agent graph,
    returns the RCAReport.

    Called by:
        - simulation/fault_injector.py (automated fault injection)
        - Manual POST for testing
        - Future: Prometheus Alertmanager webhook

    Flow:
        AlertPayload → agent_graph.ainvoke() → RCAReport
    """
    t_start = time.time()

    log.info(
        "alert_received",
        alert_id=alert.alert_id,
        service=alert.service,
        error_type=alert.error_type,
        severity=alert.severity,
    )

    # Build initial state — only alert_raw is needed here.
    # ingest.py initialises all other fields from alert_raw.
    initial_state = {
        "alert_raw": alert.model_dump(mode="json"),
    }

    try:
        final_state = await agent_graph.ainvoke(initial_state)
    except Exception as e:
        log.error("agent_graph_failed", error=str(e), alert_id=alert.alert_id)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {e}")

    elapsed = round(time.time() - t_start, 2)

    report: RCAReport | None = final_state.get("rca_report")
    error:  str | None       = final_state.get("error_message")

    if error:
        log.error("agent_returned_error", error=error, elapsed_s=elapsed)
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": error, "elapsed_s": elapsed}
        )

    if not report:
        log.error("agent_returned_no_report", elapsed_s=elapsed)
        return JSONResponse(
            status_code=500,
            content={"status": "error",
                     "message": "Agent completed but produced no report.",
                     "elapsed_s": elapsed}
        )

    log.info(
        "alert_handled",
        alert_id=alert.alert_id,
        resolution=report.resolution_status.value,
        root_cause=report.root_cause_node,
        elapsed_s=elapsed,
    )

    return {
        "status":          report.resolution_status.value,
        "alert_id":        report.alert_id,
        "root_cause":      report.root_cause_node,
        "dependency_chain":report.dependency_chain,
        "skills_executed": report.skills_executed,
        "total_hops":      report.total_hops,
        "elapsed_s":       elapsed,
        "report":          report.model_dump(mode="json"),
    }


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "agent.main:app",
        host="0.0.0.0",
        port=settings.alert_listen_port,
        reload=False,
        log_config=None,   # suppress uvicorn's default logging — we use structlog
    )