"""
agent/nodes/executor.py

Layer 4: Secure Execution Sandbox

Phase 4 STUB — simulates script execution so the full agent loop
(ingest → retrieve → reason → execute → evaluate → report) can be
tested end-to-end without the Docker sandbox infrastructure.

Phase 5 replaces this with real Docker SDK calls:
    - Mounts SOP script read-only into ephemeral container
    - Runs with --cap-drop ALL, --memory=256m, --network sim-net
    - Captures stdout/stderr/exit_code
    - Returns actual ExecutionResult

The stub always returns success=True so the evaluator's Q5 health
check becomes the real test of whether remediation worked — which
it won't until Phase 5 writes real scripts. That's intentional:
Phase 4 proves the loop logic; Phase 5 proves the execution.
"""

from __future__ import annotations

from core import get_logger
from core.schemas import ExecutionResult
from agent.state import AgentState

log = get_logger(__name__)


def run_sop(state: AgentState) -> AgentState:
    """
    Phase 4 stub: logs the execution intent, returns mock success result.
    Real Docker sandbox execution implemented in Phase 5.
    """
    skill  = state.get("current_skill", "unknown")
    script = state.get("current_script", "unknown")
    stype  = state.get("current_script_type", "unknown")

    log.info(
        "executor_stub_called",
        skill=skill,
        script=script,
        script_type=stype,
        attempt=state.get("attempt_count", 0) + 1,
        note="Phase 4 stub — replace with Docker sandbox in Phase 5",
    )

    result = ExecutionResult(
        skill_name  = skill,
        script_path = script,
        exit_code   = 0,
        stdout      = '{"success": true, "note": "Phase 4 stub — no real execution"}',
        stderr      = "",
        duration_s  = 0.0,
        success     = True,
    )

    history = list(state.get("execution_history", []))
    history.append(result)

    return {
        **state,
        "execution_history": history,
    }