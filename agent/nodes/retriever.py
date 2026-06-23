"""
agent/nodes/retriever.py

Layer 2: Hybrid GraphRAG Retrieval

Runs two Neo4j queries per loop iteration:
    Q1 — get_root_cause():  multi-hop DEPENDS_ON traversal to find the
                             deepest unhealthy upstream node
    Q2 — get_skill():       APPLIES_TO lookup to retrieve the matching
                             SOP Skill node for that root cause + error type

This node runs TWICE per incident loop:
    - First call: populates root_cause_node + dependency_chain (Q1)
                  and retrieves first skill (Q2)
    - Subsequent calls: root_cause_node already known, skip Q1,
                        run Q2 with updated visited_skills to get next skill

Progressive Context Injection happens here:
    Only the SINGLE matched Skill node's description and script_path
    are placed into state — not all 9 skills. The LLM in reasoner.py
    receives only what is relevant to the current node.
"""

from __future__ import annotations

from core import get_logger
from core.exceptions import RootCauseNotFoundError, SkillNotFoundError
from graph.graph_client import GraphClient
from agent.state import AgentState

log = get_logger(__name__)


def retrieve_context(state: AgentState) -> AgentState:
    """
    Query Graph 1 (infrastructure) and Graph 2 (skill) and inject
    only the active context into state.

    First iteration:  runs Q1 to find root cause, then Q2 for first skill
    Later iterations: skips Q1 (root already known), runs Q2 for next skill
    """
    gc = GraphClient()

    # ── Q1: Root cause traversal (first iteration only) ───────────────────
    root_cause_node  = state.get("root_cause_node")
    dependency_chain = state.get("dependency_chain", [])

    if not root_cause_node:
        log.info(
            "q1_traversal_start",
            alert_service=state["alert_service"],
            error_type=state["alert_error_type"],
        )
        try:
            result = gc.get_root_cause(
                alert_service=state["alert_service"],
                error_type=state["alert_error_type"],
            )
            root_cause_node  = result.root_cause_node
            dependency_chain = result.dependency_chain

            log.info(
                "q1_traversal_complete",
                root_cause=root_cause_node,
                chain=dependency_chain,
                depth=result.depth,
            )
        except Exception as e:
            log.error("q1_traversal_failed", error=str(e))
            return {
                **state,
                "error_message": f"Root cause traversal failed: {e}",
            }

    # ── Q2: Skill retrieval ────────────────────────────────────────────────
    log.info(
        "q2_skill_lookup",
        root_node=root_cause_node,
        error_type=state["alert_error_type"],
        visited=state["visited_skills"],
    )

    try:
        skill = gc.get_skill(
            root_node=root_cause_node,
            error_type=state["alert_error_type"],
            visited=state["visited_skills"],
        )

        log.info(
            "q2_skill_retrieved",
            skill=skill.name,
            script=skill.script_path,
            risk=skill.risk_level,
        )

        return {
            **state,
            # Update root cause info (idempotent on subsequent iterations)
            "root_cause_node":  root_cause_node,
            "dependency_chain": dependency_chain,

            # Inject ONLY this skill's context — progressive injection
            "current_skill":       skill.name,
            "current_script":      skill.script_path,
            "current_script_type": skill.script_type,
            "current_description": skill.description,
            "current_risk_level":  skill.risk_level,
        }

    except SkillNotFoundError:
        # No skill matches this root node + error type combo
        # (or all matching skills have been visited already)
        log.warning(
            "q2_no_skill_found",
            root_node=root_cause_node,
            error_type=state["alert_error_type"],
            visited=state["visited_skills"],
        )
        return {
            **state,
            "root_cause_node":  root_cause_node,
            "dependency_chain": dependency_chain,
            "current_skill":    None,
            "current_script":   None,
        }