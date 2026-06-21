"""
agent/tools/sandbox_tools.py

Docker SDK integration for executing SOP remediation scripts inside
isolated sandbox containers.

Privilege scoping model:
    Scripts that need to control sibling containers (e.g. restart.sh,
    which calls `docker restart redis-cart`) require the Docker socket
    mounted into the sandbox. This is a deliberate, narrow exception to
    full isolation -- the sandbox can manage containers, nothing else.

    Scripts that only need network access to a service (e.g.
    cache_flush.sh, which talks to redis-cart:6379) get NO socket mount.
    Same base image, strictly less privilege at runtime.

    This is decided per-SOP using the Skill node's `risk_level` field
    already present in the Neo4j graph:
        LOW    -> network-only sandbox, non-root user, no Docker socket
        MEDIUM -> Docker socket mounted, runs as root inside container
                  (required to reach the host socket's permissions --
                  root-in-container is NOT root-on-host; --cap-drop ALL
                  and all other constraints still apply)
        HIGH   -> not currently used; reserved for scripts requiring
                  explicit human confirmation in a future phase

Security constraints applied to every sandbox container regardless of
privilege tier:
    --cap-drop ALL --cap-add NET_BIND_SERVICE
    --security-opt no-new-privileges
    --read-only --tmpfs /tmp:size=64m
    --memory limit, --cpus limit, --pids-limit
    --network sim-net (boutique-sim) -- no internet access
    --rm -- ephemeral, no state carry-over
    hard stop_timeout -- force-killed if it hangs

Note on user scoping:
    The sop-executor image creates a non-root `sopuser` (UID 1000) as
    its default user. That works fine for network-only SOPs. But the
    Docker socket on the host is owned by root/docker-group, and a
    non-root user inside the container has no matching group membership
    to read it -- this caused a "permission denied" connecting to the
    socket even with the volume correctly mounted. Rather than try to
    match host socket GIDs (fragile, differs across Docker Desktop
    versions and OSes), socket-requiring SOPs simply run as root inside
    the sandbox. Every other hardening flag (cap-drop, read-only,
    resource limits, network isolation) still fully applies -- root
    inside a capability-stripped, read-only, network-isolated container
    is not equivalent to host root.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import docker
from docker.errors import ContainerError, ImageNotFound, APIError

from core import get_logger, settings
from core.schemas import ExecutionResult
from core.exceptions import SOPExecutionError, ContainerTimeoutError, SandboxError

log = get_logger(__name__)

SANDBOX_IMAGE   = "sop-executor:latest"
SANDBOX_NETWORK = "boutique-sim"
DOCKER_SOCKET   = "/var/run/docker.sock"

# Risk levels that require Docker socket access (container control SOPs)
SOCKET_REQUIRED_RISK_LEVELS = {"MEDIUM", "HIGH"}


def _docker_client() -> docker.DockerClient:
    """Returns a Docker client for the HOST daemon (not the sandbox)."""
    return docker.from_env()


def execute_sop(
    script_path: str,
    script_type: str,
    risk_level: str = "LOW",
    env_vars: dict | None = None,
    timeout: int = 30,
) -> ExecutionResult:
    """
    Execute a SOP script inside an isolated sandbox container.

    Args:
        script_path:  absolute path to the script ON THE HOST, e.g.
                       "/Users/.../sops/redis/restart.sh"
        script_type:  "bash" or "python"
        risk_level:   from the Skill node -- determines socket mount + user
        env_vars:     environment variables passed into the sandbox
        timeout:      hard kill timeout in seconds

    Returns:
        ExecutionResult with stdout, stderr, exit_code, success, duration_s
    """
    env_vars = env_vars or {}
    script_file = Path(script_path)

    if not script_file.exists():
        log.error("sop_script_not_found", path=script_path)
        return ExecutionResult(
            skill_name="unknown",
            script_path=script_path,
            exit_code=127,
            stderr=f"Script not found on host: {script_path}",
            success=False,
        )

    container_name = f"sop-run-{uuid.uuid4().hex[:8]}"
    script_name    = script_file.name
    mount_target   = f"/script/{script_name}"

    cmd = (
        ["bash", mount_target] if script_type == "bash"
        else ["python3", mount_target]
    )

    needs_socket = risk_level.upper() in SOCKET_REQUIRED_RISK_LEVELS

    # ── Volume mounts ───────────────────────────────────────────────────────
    volumes = {
        str(script_file.resolve()): {"bind": mount_target, "mode": "ro"},
    }
    if needs_socket:
        volumes[DOCKER_SOCKET] = {"bind": DOCKER_SOCKET, "mode": "rw"}

    # ── User scoping ───────────────────────────────────────────────────────
    # Root only when the Docker socket is mounted (needed to read it).
    # Non-root (matches sop-executor's built-in sopuser, UID 1000) otherwise.
    container_user = "root" if needs_socket else "1000:1000"

    log.info(
        "sandbox_execution_start",
        container=container_name,
        script=script_name,
        risk_level=risk_level,
        docker_socket_mounted=needs_socket,
        container_user=container_user,
        timeout=timeout,
    )

    client = _docker_client()
    t_start = time.time()

    try:
        container = client.containers.run(
            image=SANDBOX_IMAGE,
            command=cmd,
            name=container_name,
            network=SANDBOX_NETWORK,
            user=container_user,

            # ── Hard security constraints — always applied ─────────────────
            cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE"],
            security_opt=["no-new-privileges"],
            read_only=True,
            tmpfs={"/tmp": "size=64m"},
            mem_limit="256m",
            memswap_limit="256m",
            nano_cpus=500_000_000,      # 0.5 CPU
            pids_limit=50,
            stop_signal="SIGKILL",

            volumes=volumes,
            environment=env_vars,
            detach=True,
            remove=False,               # remove manually after log capture
        )

        # ── Wait with hard timeout ──────────────────────────────────────────
        try:
            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", 1)
        except Exception:
            log.warning("sandbox_timeout_killing_container",
                       container=container_name, timeout=timeout)
            container.kill()
            duration = round(time.time() - t_start, 2)
            stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
            container.remove(force=True)
            return ExecutionResult(
                skill_name=script_name,
                script_path=script_path,
                exit_code=-1,
                stdout=stdout,
                stderr=stderr or f"Killed after {timeout}s timeout",
                duration_s=duration,
                success=False,
            )

        stdout   = container.logs(stdout=True, stderr=False).decode(errors="replace")
        stderr   = container.logs(stdout=False, stderr=True).decode(errors="replace")
        duration = round(time.time() - t_start, 2)

        container.remove(force=True)

        success = exit_code == 0

        log.info(
            "sandbox_execution_complete",
            container=container_name,
            exit_code=exit_code,
            success=success,
            duration_s=duration,
        )

        return ExecutionResult(
            skill_name=script_name,
            script_path=script_path,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration,
            success=success,
        )

    except ImageNotFound:
        log.error("sandbox_image_not_found", image=SANDBOX_IMAGE,
                  hint="Run: docker build -t sop-executor:latest sop-executor/")
        return ExecutionResult(
            skill_name=script_name, script_path=script_path,
            exit_code=127, stderr=f"Image not found: {SANDBOX_IMAGE}",
            success=False,
        )

    except APIError as e:
        log.error("sandbox_docker_api_error", error=str(e))
        return ExecutionResult(
            skill_name=script_name, script_path=script_path,
            exit_code=1, stderr=f"Docker API error: {e}",
            success=False,
        )