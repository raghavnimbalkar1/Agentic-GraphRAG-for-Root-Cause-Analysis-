"""
agent/nodes/ingest.py

Layer 1: Orchestration & State Initialisation

Receives the raw AlertPayload dict and initialises every field in
AgentState with correct starting values. This is the first node in the
LangGraph graph.

Responsibilities:
    - Parse alert fields from the raw payload
    - Zero all counters and lists
    - Set max_attempts from settings
    - Pass initialised state downstream

Does NOT:
    - Query Neo4j (that's retriever.py)
    - Call the LLM (that's reasoner.py)
    - Make any decisions
"""

from __future__ import annotations

import time

from core import get_logger, settings
from core.schemas import AlertPayload
from agent.state import AgentState

log = get_logger(__name__)


def ingest_alert(state: AgentState) -> AgentState:
    """
    Parse the raw alert payload and initialise agent state.

    Input:  state["alert_raw"] — the dict posted to POST /alert
    Output: fully initialised AgentState ready for retriever.py
    """
    raw = state["alert_raw"]

    # Validate through the Pydantic schema so we catch malformed alerts
    # immediately rather than failing silently three nodes later
    try:
        alert = AlertPayload(**raw)
    except Exception as e:
        log.error("alert_parse_failed", error=str(e), raw=raw)
        return {
            **state,
            "error_message": f"Failed to parse alert payload: {e}",
            "all_healthy":   False,
        }

    log.info(
        "alert_ingested",
        alert_id=alert.alert_id,
        service=alert.service,
        error_type=alert.error_type,
        severity=alert.severity,
    )

    return {
        **state,
        # ── Alert fields ───────────────────────────────────────────────
        "alert_id":         alert.alert_id,
        "alert_service":    alert.service,
        "alert_error_type": str(alert.error_type),
        "alert_message":    alert.message,

        # ── Graph traversal (populated by retriever.py) ────────────────
        "root_cause_node":  None,
        "dependency_chain": [],
        "traversal_depth":  0,

        # ── Current skill (populated by retriever.py each loop) ────────
        "current_skill":       None,
        "current_script":      None,
        "current_script_type": None,
        "current_description": None,

        # ── Timing & telemetry — stamp NOW so MTTR is accurate ─────────
        "t_alert":    time.time(),
        "tokens_used": 0,

        # ── Execution tracking ─────────────────────────────────────────
        "visited_skills":    [],
        "execution_history": [],
        "attempt_count":     0,
        "max_attempts":      settings.agent_max_attempts,

        # ── LLM decision ───────────────────────────────────────────────
        "llm_decision": None,
        "llm_reason":   None,

        # ── Resolution state ───────────────────────────────────────────
        "all_healthy":             False,
        "services_still_unhealthy": 0,

        # ── Output ─────────────────────────────────────────────────────
        "rca_report":    None,
        "error_message": None,
    }