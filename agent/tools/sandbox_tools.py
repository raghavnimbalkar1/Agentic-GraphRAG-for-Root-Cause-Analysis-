"""Run curated SOPs as non-root containers with restricted per-attempt control."""

from contextlib import nullcontext
from pathlib import Path
import time
from uuid import uuid4

import docker

from agent.tools.control_gateway import ControlSession, SCRIPT_OPERATIONS
from core import get_logger, settings
from core.schemas import ExecutionResult

log = get_logger(__name__)
NETWORK_SCRIPTS = {"redis/cache_flush.sh", "redis/config_reset.sh", "redis/pool_reset.sh"}
SANDBOX_NETWORK = "boutique-sim"


def _docker_client():
    return docker.DockerClient(base_url=settings.docker_host, timeout=10)


def execute_sop(script_path: str, script_type: str, risk_level: str = "LOW",
                env_vars: dict | None = None, timeout: int = 30) -> ExecutionResult:
    started = time.monotonic()
    client = container = None
    exit_code = 1
    stdout = stderr = ""
    cleaned = True
    script = Path(script_path).resolve()
    try:
        root = settings.sops_dir.resolve()
        relative = script.relative_to(root).as_posix()
        if not script.is_file() or relative not in NETWORK_SCRIPTS | SCRIPT_OPERATIONS.keys():
            raise ValueError("Script is not in the approved executable catalog")
        if script_type != "bash" or risk_level not in {"LOW", "MEDIUM"} or not 1 <= timeout <= 120:
            raise ValueError("Unsupported execution policy")
        environment = dict(env_vars or {})
        target = environment.get("TARGET_CONTAINER")
        needs_control = relative in SCRIPT_OPERATIONS
        if not needs_control and target != "redis-cart":
            raise ValueError("Redis SOP is not authorized for another service")
        if not needs_control:
            environment.update({"REDIS_HOST": "redis-cart", "REDIS_PORT": "6379"})
        if needs_control and risk_level != "MEDIUM":
            raise ValueError("Control operations require the configured MEDIUM policy")
        client = _docker_client()
        owned = client.containers.get(target)
        owned.reload()
        if owned.attrs.get("Config", {}).get("Labels", {}).get("rca.managed") != "online-boutique":
            raise ValueError("Target is not owned by the simulation")
        authorization = ControlSession(client, script, target, timeout) if needs_control else nullcontext({})
        with authorization as control:
            environment.update(control)
            volumes = {str(script): {"bind": "/script/sop.sh", "mode": "ro"}}
            if needs_control:
                volumes[str(root / "control_client.py")] = {"bind": "/script/control_client.py", "mode": "ro"}
            container = client.containers.run(
                image=settings.sop_executor_image, command=["bash", "/script/sop.sh"],
                name=f"sop-run-{uuid4().hex[:12]}", network=SANDBOX_NETWORK,
                user="1000:1000", cap_drop=["ALL"], security_opt=["no-new-privileges"],
                read_only=True, tmpfs={"/tmp": "size=64m"}, mem_limit="256m",
                memswap_limit="256m", nano_cpus=500_000_000, pids_limit=50,
                volumes=volumes, environment=environment, detach=True, remove=False,
                extra_hosts={"host.docker.internal": "host-gateway"},
                labels={"rca.role": "sop-executor"},
            )
            try:
                exit_code = container.wait(timeout=timeout).get("StatusCode", 1)
            except Exception as exc:
                exit_code = -1
                stderr = f"Sandbox wait failed: {type(exc).__name__}"
                container.kill()
            stdout = container.logs(stdout=True, stderr=False, tail=200).decode(errors="replace")[-65536:]
            stderr += container.logs(stdout=False, stderr=True, tail=200).decode(errors="replace")[-65536:]
    except Exception as exc:
        stderr = f"{stderr} {type(exc).__name__}: {exc}".strip()
        exit_code = 1
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception as exc:
                cleaned = False
                stderr += f" Sandbox cleanup failed: {type(exc).__name__}"
        if client is not None:
            try:
                client.close()
            except Exception:
                log.warning("docker_client_close_failed")
    return ExecutionResult(
        skill_name=script.name, script_path=str(script), exit_code=exit_code,
        stdout=stdout, stderr=stderr, duration_s=round(time.monotonic() - started, 3),
        success=exit_code == 0 and cleaned, sandbox_cleaned=cleaned,
    )
