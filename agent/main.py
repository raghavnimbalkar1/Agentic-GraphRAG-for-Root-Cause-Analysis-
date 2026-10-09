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

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

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
    from agent.incident_manager import IncidentManager
    app.state.incidents = IncidentManager(settings.audit_dir / "incidents.sqlite")

    try:
        yield
    finally:
        log.info("agent_shutting_down")
        await asyncio.gather(*app.state.incidents.jobs.values(), return_exceptions=True)
        gc.close()


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
    try:
        neo4j_ok = GraphClient().health_check()
    except Exception:
        neo4j_ok = False

    return {
        "neo4j":        "ok" if neo4j_ok else "unreachable",
        "neo4j_uri":    settings.neo4j_uri,
        "llm_provider": settings.llm_provider.value,
        "llm_model":    settings.llm_model,
        "llm_connectivity": "not_probed",
        "max_attempts": settings.agent_max_attempts,
    }


async def _run_incident(alert: AlertPayload) -> RCAReport:
    from agent.nodes.ingest import ingest_alert
    from agent.nodes.evaluator import _make_report
    from core.audit import write_report
    from core.schemas import ResolutionStatus

    initial = {"alert_raw": alert.model_dump(mode="json")}
    partial = ingest_alert(initial)
    try:
        async for updates in agent_graph.astream(initial, stream_mode="updates"):
            for updated in updates.values():
                if isinstance(updated, dict):
                    partial.update(updated)
        report = partial.get("rca_report")
        if report is None:
            raise RuntimeError("Workflow produced no terminal report")
        return report
    except Exception as exc:
        report = _make_report(partial, ResolutionStatus.FAILED, f"Workflow failed: {exc}")
        write_report(report)
        return report


def _response(report: RCAReport) -> dict:
    return {
        "status": report.resolution_status.value, "alert_id": report.alert_id,
        "root_cause": report.root_cause_node, "dependency_chain": report.dependency_chain,
        "skills_executed": report.skills_executed, "total_hops": report.total_hops,
        "elapsed_s": report.handling_seconds, "report": report.model_dump(mode="json"),
    }


@app.post("/alert", response_model=None)
async def handle_alert(alert: AlertPayload, request: Request):
    from agent.incident_manager import IncidentConflict
    from core.health import SERVICES
    if alert.service not in SERVICES:
        raise HTTPException(status_code=422, detail="Service is outside the supported deployment")
    try:
        report = await request.app.state.incidents.submit(alert, _run_incident)
    except IncidentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _response(report)


@app.get("/incidents/{alert_id}")
def incident_status(alert_id: str, request: Request):
    from core.audit import read_report
    try:
        report = read_report(alert_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if report:
        return _response(report)
    if alert_id in request.app.state.incidents.jobs:
        return {"alert_id": alert_id, "status": "IN_PROGRESS"}
    raise HTTPException(status_code=404, detail="Incident not found")


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "agent.main:app",
        # Bind loopback only. The /alert endpoint triggers real Docker
        # remediation and has no authentication, so it must not be reachable
        # from the network. The fault injector and dashboard call it over
        # localhost:8888, so loopback is sufficient for the demo.
        host="127.0.0.1",
        port=settings.alert_listen_port,
        reload=False,
        log_config=None,   # suppress uvicorn's default logging — we use structlog
    )
