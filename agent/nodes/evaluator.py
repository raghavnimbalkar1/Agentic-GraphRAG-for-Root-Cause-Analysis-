"""
agent/nodes/evaluator.py

Layer 5: Evaluation & Resolution

Runs after every sandbox execution. Checks whether the remediation worked
by querying Neo4j for unhealthy services in the dependency chain (Q5).

Makes the loop termination decision:
    all_healthy  = True  → generate report, terminate
    all_healthy  = False → follow NEXT_IF_FAIL edge (Q3), loop back
    max_attempts reached → escalate, terminate
    llm_decision = escalate → terminate immediately

Also handles execution results: marks current skill as visited, appends
ExecutionResult to execution_history, increments attempt_count.
"""

from __future__ import annotations

from datetime import datetime

from core import get_logger
from core.schemas import ExecutionResult, RCAReport, ResolutionStatus
from graph.graph_client import GraphClient
from agent.state import AgentState

log = get_logger(__name__)


def evaluate_and_route(state: AgentState) -> AgentState:
    """
    Check health of all affected services and decide whether to loop or stop.

    Called after:
        - executor.py runs a sandbox script (Phase 5+)
        - reasoner.py decides "skip" or "escalate" (routed here via graph.py)
    """
    gc = GraphClient()

    # Mark current skill visited (prevents infinite re-trying same SOP)
    visited = list(state.get("visited_skills", []))
    if state.get("current_skill") and state["current_skill"] not in visited:
        visited.append(state["current_skill"])

    # Increment attempt counter
    attempt_count = state.get("attempt_count", 0) + 1

    chain = state.get("dependency_chain", [state.get("alert_service", "")])

    # ── LLM explicitly escalated — terminate without health check ─────────
    if state.get("llm_decision") == "escalate":
        log.warning(
            "incident_escalated_by_llm",
            reason=state.get("llm_reason"),
            root_cause=state.get("root_cause_node"),
            attempt=attempt_count,
        )
        unhealthy_count  = gc.count_unhealthy(chain)
        all_healthy      = False
        resolution_status = ResolutionStatus.ESCALATED

    else:
        # ── Normal path: check live health via Q5 ─────────────────────────
        unhealthy_count = gc.count_unhealthy(chain)
        all_healthy     = unhealthy_count == 0

        log.info(
            "health_check",
            unhealthy_count=unhealthy_count,
            chain=chain,
            all_healthy=all_healthy,
            attempt=attempt_count,
            max_attempts=state["max_attempts"],
        )

        if all_healthy:
            resolution_status = ResolutionStatus.RESOLVED
            log.info(
                "incident_resolved",
                root_cause=state.get("root_cause_node"),
                attempts=attempt_count,
            )
        elif attempt_count >= state["max_attempts"]:
            resolution_status = ResolutionStatus.ESCALATED
            log.warning(
                "incident_escalated",
                root_cause=state.get("root_cause_node"),
                attempts=attempt_count,
                still_unhealthy=unhealthy_count,
            )
        else:
            resolution_status = None   # still looping

    # ── Build report if terminating ───────────────────────────────────────
    rca_report = None

    if resolution_status is not None:
        rca_report = RCAReport(
            alert_id             = state["alert_id"],
            alert_service        = state["alert_service"],
            alert_error_type     = state["alert_error_type"],
            root_cause_node      = state.get("root_cause_node", "unknown"),
            dependency_chain     = state.get("dependency_chain", []),
            skills_executed      = visited,
            execution_history    = state.get("execution_history", []),
            total_hops           = attempt_count,
            resolution_status    = resolution_status,
            all_services_healthy = all_healthy,
            timestamp            = datetime.utcnow(),
        )
        log.info(
            "rca_report_generated",
            status=resolution_status.value,
            skills_executed=visited,
        )

    return {
        **state,
        "visited_skills":           visited,
        "attempt_count":            attempt_count,
        "all_healthy":              all_healthy,
        "services_still_unhealthy": unhealthy_count,
        "rca_report":               rca_report,
    }


def generate_report(state: AgentState) -> AgentState:
    """
    Terminal node. Writes the RCA report to the audit log.
    Called only when all_healthy=True or max_attempts exceeded.
    """
    report = state.get("rca_report")
    if not report:
        log.warning("generate_report_called_with_no_report",
                    note="evaluate_and_route should always produce a report before this node")
        return state

    import json
    from pathlib import Path
    from core.config import settings

    audit_path = settings.audit_dir / f"rca_{report.alert_id}.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    with open(audit_path, "w") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, default=str)

    log.info(
        "audit_report_written",
        path=str(audit_path),
        status=report.resolution_status.value,
        root_cause=report.root_cause_node,
    )

    return state