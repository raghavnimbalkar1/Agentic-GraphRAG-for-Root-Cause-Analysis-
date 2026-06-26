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
    traversal_depth  = state.get("traversal_depth", 0)

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
            traversal_depth  = result.depth

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
    # The alert's error_type localises the symptom, but on a deep cascade it is a
    # SURFACE symptom (e.g. HIGH_ERROR_RATE seen at loadgenerator) that does not
    # name the root's real condition. So we try the alert's error_type first, then
    # fall back to the ROOT's actual telemetry-synced condition (its Neo4j status),
    # which is what truly determines the remediation. Symptom localises the root;
    # the root's real diagnosed condition selects the SOP.
    alert_error = state["alert_error_type"]
    candidates: list[str] = [alert_error]
    try:
        root_status = gc.get_all_service_statuses().get(root_cause_node)
        if root_status and root_status not in ("HEALTHY", alert_error):
            candidates.append(root_status)
    except Exception:  # noqa: BLE001
        pass

    log.info("q2_skill_lookup", root_node=root_cause_node,
             error_candidates=candidates, visited=state["visited_skills"])

    # get_skills returns ALL matching SOPs (the candidate set). The LLM in the
    # reasoner may only choose from THIS graph-derived set — the security boundary.
    skills: list = []
    matched_on = alert_error
    for candidate_error in candidates:
        skills = gc.get_skills(root_node=root_cause_node,
                               error_type=candidate_error,
                               visited=state["visited_skills"])
        if skills:
            matched_on = candidate_error
            break

    if skills:
        candidate_skills = [
            {"name": s.name, "description": s.description,
             "risk_level": s.risk_level, "script_path": s.script_path,
             "script_type": s.script_type, "trigger_condition": s.trigger_condition}
            for s in skills
        ]
        first = skills[0]   # default pick (lowest risk); reasoner may override
        log.info(
            "q2_candidates_retrieved",
            candidates=[s.name for s in skills], matched_on=matched_on,
            via_root_condition=(matched_on != alert_error),
        )
        return {
            **state,
            "root_cause_node":  root_cause_node,
            "dependency_chain": dependency_chain,
            "traversal_depth":  traversal_depth,
            # The full graph-vetted candidate set the LLM must choose from
            "candidate_skills":    candidate_skills,
            # Default to the lowest-risk candidate; the reasoner picks/justifies
            "current_skill":       first.name,
            "current_script":      first.script_path,
            "current_script_type": first.script_type,
            "current_description": first.description,
            "current_risk_level":  first.risk_level,
            "current_trigger":     matched_on,
        }

    log.warning(
        "q2_no_skill_found",
        root_node=root_cause_node, error_candidates=candidates,
        visited=state["visited_skills"],
    )
    return {
        **state,
        "root_cause_node":  root_cause_node,
        "dependency_chain": dependency_chain,
        "traversal_depth":  traversal_depth,
        "candidate_skills": [],
        "current_skill":    None,
        "current_script":   None,
    }