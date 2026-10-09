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

Graph/reality sync:
    A successful sandbox execution (e.g. restarting redis-cart) changes the REAL
    state of the target environment, but the Neo4j Service node still holds
    whatever status was last written to it. Without an explicit sync step, the
    Q5 health check reads stale graph state and never sees that the SOP worked,
    so the agent would loop or escalate even after a successful fix.

    This node therefore re-probes the real failed condition (verify_real_health)
    and, only if that probe passes, writes the root cause back to HEALTHY before
    running Q5. RESOLVED consequently means "the condition is genuinely gone",
    not "the script exited 0".

    simulation/telemetry_collector.py independently converges the graph to
    observed reality on its 5s poll; this in-loop write is what lets a single
    incident conclude without waiting for the next poll.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import docker

from core import get_logger, settings
from core.schemas import RCAReport, ResolutionStatus
from graph.graph_client import GraphClient
from agent.state import AgentState

log = get_logger(__name__)

def verify_real_health(root_cause_node: str, script_path: str, error_type: str = "") -> tuple[bool, str]:
    """Re-observe all supported signals; missing data is never recovery."""
    from core.health import observe
    client = None
    try:
        client = docker.DockerClient(base_url=settings.docker_host, timeout=5)
        result = observe(client, root_cause_node)
        return result.healthy, result.detail
    except Exception as exc:
        return False, f"Verification unavailable: {exc}"
    finally:
        if client is not None:
            client.close()


def verify_incident(services: list[str]) -> dict:
    """Require two healthy observations within a bounded settling window."""
    from core.health import Observation, observe_many
    if not services:
        return {}
    client = docker.DockerClient(base_url=settings.docker_host, timeout=5)
    deadline = time.monotonic() + settings.verification_timeout
    consecutive = {name: 0 for name in services}
    evidence = {}
    try:
        while True:
            observations = observe_many(client, services)
            evidence = {}
            for name in services:
                observation = observations.get(name, Observation("UNKNOWN", "Probe returned no observation"))
                consecutive[name] = consecutive[name] + 1 if observation.healthy else 0
                evidence[name] = {**observation.to_dict(), "healthy": consecutive[name] >= 2}
            if all(evidence[name]["healthy"] for name in services) or time.monotonic() >= deadline:
                return evidence
            time.sleep(1)
    finally:
        client.close()


def evaluate_and_route(state: AgentState) -> AgentState:
    """Evaluate only this attempt; a terminal decision can never be reopened."""
    attempt = state.get("attempt_count", 0) + 1
    current = state.get("current_skill")
    decision = state.get("llm_decision")
    visited = list(state.get("visited_skills", []))
    if current and decision in ("execute", "skip") and current not in visited:
        visited.append(current)
    history = state.get("execution_history", [])
    execution = next((r for r in reversed(history)
                      if r.attempt == attempt and r.skill_name == current), None)
    root = state.get("root_cause_node")
    condition = state.get("root_condition") or state.get("current_trigger") or state.get("alert_error_type", "")
    affected = list(dict.fromkeys([root] + state.get("dependency_chain", [])
                                  + state.get("potential_blast_radius", [])))
    affected = [s for s in affected if s]
    evidence = {}
    all_healthy = False
    terminal = None
    fallback = None
    detail = state.get("error_message") or state.get("llm_reason") or ""

    if state.get("error_message"):
        terminal = ResolutionStatus.FAILED
    elif decision == "escalate" or not current:
        terminal = ResolutionStatus.ESCALATED
    else:
        try:
            gc = GraphClient()
            if decision == "execute":
                if execution is None:
                    terminal = ResolutionStatus.FAILED
                    detail = "No execution result for the current attempt"
                else:
                    if execution.success and root:
                        evidence = verify_incident(affected)
                        all_healthy = bool(evidence) and all(evidence.get(s, {}).get("healthy", False) for s in affected)
                        detail = "; ".join(f"{s}: {v['detail']}" for s, v in evidence.items() if not v["healthy"])
                        if all_healthy:
                            for service, observation in evidence.items():
                                if observation["healthy"]:
                                    gc.update_service_status(service, "HEALTHY", None)
                        if all_healthy:
                            terminal = ResolutionStatus.RESOLVED
                        elif evidence.get(root, {}).get("healthy", False):
                            terminal = ResolutionStatus.PARTIAL
                            detail = "Root recovered, but affected services have not all recovered: " + detail
                    if not all_healthy and terminal is None:
                        candidate = gc.get_next_skill(current, root)
                        if candidate and candidate.name not in visited:
                            fallback = candidate
            elif decision != "skip":
                terminal = ResolutionStatus.ESCALATED
                detail = "No valid execution decision"
        except Exception as exc:
            terminal = ResolutionStatus.FAILED
            detail = f"Evaluation infrastructure failed: {exc}"

    if terminal is None and attempt >= state.get("max_attempts", 5):
        terminal = ResolutionStatus.ESCALATED
        detail = detail or "Attempt budget exhausted"
    attempts = list(state.get("attempts", []))
    attempts.append({
        "attempt": attempt, "target": root, "condition": condition,
        "candidates": [c["name"] for c in state.get("candidate_skills", [])],
        "decision": decision, "selected_skill": current if decision == "execute" else None,
        "reason": state.get("llm_reason") or "", "verification": evidence,
        "execution": execution.model_dump(mode="json") if execution else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    updated = {
        **state, "visited_skills": visited, "attempt_count": attempt,
        "attempts": attempts, "verification": evidence, "root_condition": condition,
        "all_healthy": all_healthy, "fallback_pending": False, "rca_report": None,
        "services_still_unhealthy": sum(not evidence.get(s, {}).get("healthy", False) for s in affected),
    }
    if terminal is not None:
        updated["rca_report"] = _make_report(updated, terminal, detail)
    elif fallback is not None:
        record = fallback.model_dump()
        updated.update({
            "candidate_skills": [record], "current_skill": fallback.name,
            "current_script": fallback.script_path, "current_script_type": fallback.script_type,
            "current_description": fallback.description, "current_risk_level": fallback.risk_level,
            "current_trigger": fallback.trigger_condition, "current_timeout": fallback.timeout_seconds,
            "fallback_pending": True,
        })
    return updated


def _make_report(state: AgentState, status: ResolutionStatus, notes: str = "") -> RCAReport:
    started = state.get("t_started")
    elapsed = round(time.monotonic() - started, 3) if started is not None else None
    history = state.get("execution_history", [])
    return RCAReport(
        alert_id=state["alert_id"], alert_service=state["alert_service"],
        alert_error_type=state["alert_error_type"],
        root_cause_node=state.get("root_cause_node") or "unknown",
        root_condition=state.get("root_condition", ""),
        candidate_roots=state.get("candidate_roots", []),
        dependency_chain=state.get("dependency_chain", []),
        potential_blast_radius=state.get("potential_blast_radius", []),
        skills_executed=[r.skill_name for r in history], execution_history=history,
        total_hops=state.get("attempt_count", 0), resolution_status=status,
        mttr_seconds=elapsed, handling_seconds=elapsed,
        tokens_used=state.get("tokens_used", 0) if state.get("token_usage_complete", True) else None,
        all_services_healthy=state.get("all_healthy", False),
        root_cause_explanation=state.get("root_cause_explanation") or "",
        candidates_considered=[c["name"] for c in state.get("candidate_skills", [])],
        llm_selection_reason=state.get("llm_reason") or "",
        attempts=state.get("attempts", []), verification=state.get("verification", {}),
        notes=notes,
    )


def generate_report(state: AgentState) -> AgentState:
    """Write one complete report atomically; durations start at agent receipt."""
    from core.audit import write_report

    report = state.get("rca_report")
    if report is None and state.get("alert_id"):
        report = _make_report(state, ResolutionStatus.FAILED,
                              state.get("error_message") or "Workflow ended without a terminal outcome")
    if report is not None:
        write_report(report)
    return {**state, "rca_report": report}
