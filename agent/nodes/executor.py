"""
agent/nodes/executor.py

Layer 4: Secure Execution Sandbox

Phase 5 — real execution. Replaces the Phase 4 stub.

Resolves the SOP script's path to an absolute host path (Neo4j stores
container-relative paths like "/sops/redis/restart.sh"; this gets mapped
to the actual project directory on the host running the agent), then
calls sandbox_tools.execute_sop() to run it in an isolated container.
"""

from __future__ import annotations

from pathlib import Path

from core import get_logger
from agent.state import AgentState
from agent.tools.sandbox_tools import execute_sop

log = get_logger(__name__)

# Neo4j stores paths like "/sops/redis/restart.sh" (container-style).
# Map that prefix to the actual project sops/ directory on the host.
PROJECT_ROOT = Path(__file__).resolve().parents[2]   # agent/nodes/ -> project root
SOPS_ROOT    = PROJECT_ROOT / "sops"


def _resolve_host_path(neo4j_script_path: str) -> str:
    """
    Converts a Neo4j-stored path like "/sops/redis/restart.sh" into the
    actual absolute path on the host filesystem.
    """
    relative = neo4j_script_path.lstrip("/")
    if relative.startswith("sops/"):
        relative = relative[len("sops/"):]
    return str(SOPS_ROOT / relative)


def run_sop(state: AgentState) -> AgentState:
    """
    Execute the currently retrieved SOP script inside the Docker sandbox.
    """
    skill       = state.get("current_skill", "unknown")
    script_path = state.get("current_script", "")
    script_type = state.get("current_script_type", "bash")

    if not script_path:
        log.error("executor_no_script_path", skill=skill)
        history = list(state.get("execution_history", []))
        return {**state, "execution_history": history}

    host_path = _resolve_host_path(script_path)

    log.info(
        "executor_invoking_sandbox",
        skill=skill,
        neo4j_path=script_path,
        host_path=host_path,
        script_type=script_type,
    )

    # Build env vars the script needs. For redis scripts, point at the
    # actual running container/network names from the simulation stack.
    env_vars = {
        "TARGET_CONTAINER": state.get("root_cause_node", ""),
        "REDIS_HOST":        state.get("root_cause_node", "redis-cart"),
        "REDIS_PORT":        "6379",
    }

    result = execute_sop(
        script_path=host_path,
        script_type=script_type,
        risk_level="MEDIUM" if "restart" in skill.lower() else "LOW",
        env_vars=env_vars,
        timeout=30,
    )

    # Attach the actual skill name (sandbox_tools doesn't know it)
    result.skill_name = skill

    log.info(
        "executor_sandbox_result",
        skill=skill,
        exit_code=result.exit_code,
        success=result.success,
        duration_s=result.duration_s,
    )

    history = list(state.get("execution_history", []))
    history.append(result)

    return {
        **state,
        "execution_history": history,
    }