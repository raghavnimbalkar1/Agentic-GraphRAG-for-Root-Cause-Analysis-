"""
agent/graph.py

Wires all agent nodes into a LangGraph StateGraph.

Node execution order:
    ingest → retrieve → reason → [execute | report] → evaluate → [retrieve | report]

Conditional routing:
    After reason:
        "execute"   → run_sop   (Phase 4: stub, Phase 5: real sandbox)
        "skip"      → evaluate  (skip execution, check health, get next skill)
        "escalate"  → report    (terminate, write escalation report)

    After evaluate:
        all_healthy = True          → report   (resolved, terminate)
        attempt_count >= max        → report   (exhausted, terminate)
        current_skill = None        → report   (no more skills, terminate)
        else                        → retrieve (loop — get next skill from graph)
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.nodes.ingest    import ingest_alert
from agent.nodes.retriever import retrieve_context
from agent.nodes.reasoner  import llm_decide
from agent.nodes.executor  import run_sop
from agent.nodes.evaluator import evaluate_and_route, generate_report
from core import get_logger

log = get_logger(__name__)


# ── Routing functions ─────────────────────────────────────────────────────

def route_after_ingest(state: AgentState) -> str:
    """
    If ingest failed to parse the alert it sets error_message but leaves the
    rest of the state uninitialised. Skip straight to report so downstream
    nodes don't KeyError on missing alert fields. main.py surfaces the
    error_message as a 422.
    """
    if state.get("error_message"):
        log.warning("routing_to_report", reason="ingest_error",
                    error=state.get("error_message"))
        return "report"
    return "retrieve"


def route_after_reason(state: AgentState) -> str:
    decision = state.get("llm_decision")
    if decision == "execute":
        return "execute"
    else:
        # skip, escalate, and None all go through evaluate
        # evaluate builds the report and routes to report
        return "evaluate"


def route_after_evaluate(state: AgentState) -> str:
    """
    After health check, decide whether to loop or terminate.
    """
    if state.get("all_healthy"):
        log.info("routing_to_report", reason="all_services_healthy")
        return "report"

    if state.get("attempt_count", 0) >= state.get("max_attempts", 5):
        log.warning("routing_to_report", reason="max_attempts_reached",
                    attempts=state.get("attempt_count"))
        return "report"

    # Step 3: a NEXT_IF_FAIL fallback SOP was loaded by the evaluator (real
    # verification of the previous SOP failed). Go straight to `reason` to
    # evaluate/execute it — skip `retrieve`/Q2, which would re-select by
    # trigger and overwrite the fallback.
    if state.get("fallback_pending"):
        log.info("routing_to_reason", reason="next_if_fail_fallback",
                 skill=state.get("current_skill"))
        return "reason"

    if not state.get("current_skill"):
        log.warning("routing_to_report", reason="no_skills_remaining")
        return "report"

    # Still unhealthy, attempts remaining, skills available — loop
    log.info("routing_to_retrieve", reason="still_unhealthy_looping",
             attempts=state.get("attempt_count"),
             unhealthy=state.get("services_still_unhealthy"))
    return "retrieve"


# ── Build the graph ───────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    # ── Register nodes ────────────────────────────────────────────────────
    workflow.add_node("ingest",   ingest_alert)
    workflow.add_node("retrieve", retrieve_context)
    workflow.add_node("reason",   llm_decide)
    workflow.add_node("execute",  run_sop)
    workflow.add_node("evaluate", evaluate_and_route)
    workflow.add_node("report",   generate_report)

    # ── Entry point ───────────────────────────────────────────────────────
    workflow.add_edge(START, "ingest")

    # ── Fixed edges ───────────────────────────────────────────────────────
    workflow.add_edge("retrieve", "reason")
    workflow.add_edge("execute",  "evaluate")
    workflow.add_edge("report",   END)

    # ── Conditional edges ─────────────────────────────────────────────────
    workflow.add_conditional_edges(
        "ingest",
        route_after_ingest,
        {
            "retrieve": "retrieve",
            "report":   "report",
        }
    )

    workflow.add_conditional_edges(
        "reason",
        route_after_reason,
        {
            "execute":  "execute",
            "evaluate": "evaluate",
            "report":   "report",
        }
    )

    workflow.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {
            "retrieve": "retrieve",
            "reason":   "reason",     # Step 3: NEXT_IF_FAIL fallback path
            "report":   "report",
        }
    )

    return workflow


# ── Compile once at import time ───────────────────────────────────────────
# All modules import `agent_graph` directly:
#   from agent.graph import agent_graph

agent_graph = build_graph().compile()

log.info("agent_graph_compiled",
         nodes=list(agent_graph.get_graph().nodes.keys()))