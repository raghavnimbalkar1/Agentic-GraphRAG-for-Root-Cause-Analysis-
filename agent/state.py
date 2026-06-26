"""
agent/state.py

AgentState is the single shared data structure that flows through every
node in the LangGraph StateGraph. Every node receives the full state,
modifies only what it owns, and returns the updated state.

LangGraph merges return values with the existing state automatically —
nodes only need to return the keys they changed, not the entire dict.
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict

from core.schemas import ExecutionResult, RCAReport


class AgentState(TypedDict):
    """
    Complete state schema for one RCA incident.

    Lifecycle:
        Initialised by:  ingest.py  (from AlertPayload)
        Updated by:      retriever.py, reasoner.py, executor.py, evaluator.py
        Finalised by:    evaluator.py (rca_report written on termination)
    """

    # ── Input — set once by ingest.py, never modified ─────────────────────
    alert_id:          str               # e.g. "INC-A3F2B1C0"
    alert_service:     str               # e.g. "frontend"
    alert_error_type:  str               # e.g. "OOM_KILLED"
    alert_message:     str               # raw message from fault injector
    alert_raw:         dict[str, Any]    # full original AlertPayload dict

    # ── Graph traversal — set by retriever.py ─────────────────────────────
    root_cause_node:   Optional[str]     # e.g. "redis-cart"
    dependency_chain:  list[str]         # ["redis-cart", ..., "frontend"]
    traversal_depth:   int               # how many hops to root cause

    # ── Current skill — updated each loop iteration ────────────────────────
    current_skill:     Optional[str]     # Skill node name being evaluated
    current_script:    Optional[str]     # absolute path to SOP script
    current_script_type: Optional[str]  # "python" | "bash"
    current_description: Optional[str]  # plain-English description for LLM
    current_risk_level:  Optional[str]   # "LOW" | "MEDIUM" | "HIGH" from Skill node
    current_trigger:     Optional[str]   # the real condition this SOP remediates
                                         # (skill trigger); used for verification

    # ── Timing & telemetry ────────────────────────────────────────────────
    t_alert:           float             # time.time() at alert ingestion (for MTTR)
    tokens_used:       int               # total LLM tokens consumed this incident

    # ── Execution tracking ─────────────────────────────────────────────────
    visited_skills:    list[str]         # prevents revisiting the same SOP
    execution_history: list[ExecutionResult]  # all sandbox runs this incident
    attempt_count:     int               # incremented each loop iteration
    max_attempts:      int               # hard limit — default 5

    # ── LLM decision — set by reasoner.py, consumed by graph router ───────
    llm_decision:      Optional[str]     # "execute" | "skip" | "escalate"
    llm_reason:        Optional[str]     # LLM's explanation (for the report)

    # ── Fallback chain — set by evaluator.py when real verification fails ──
    fallback_pending:  bool              # True → router goes evaluate→reason,
                                         # current_skill already loaded with the
                                         # NEXT_IF_FAIL (Q3) fallback SOP

    # ── Resolution state — updated by evaluator.py ────────────────────────
    all_healthy:       bool              # True when count_unhealthy == 0
    services_still_unhealthy: int        # last Q5 result

    # ── Output — written by evaluator.py on termination ───────────────────
    rca_report:        Optional[RCAReport]
    error_message:     Optional[str]     # set if agent itself errors