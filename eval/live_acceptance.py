"""Opt-in physical fault/executor checks, explicitly not autonomous LLM trials."""

import argparse
import json
import time

import docker

from agent.tools.sandbox_tools import execute_sop
from agent.nodes.executor import _resolve_host_path
from core import settings
from core.audit import write_json_atomic
from core.health import SERVICES, observe, observe_many
from eval.research_benchmark import metadata, result_path
from graph.graph_client import GraphClient
from simulation import fault_injector
from simulation.application_canary import check_application
from simulation.incident_tracking import collector_ready, matching_incident

CASES = {
    "redis_oom": ("redis-cart", "OOM_KILLED", "Redis_Restart_SOP"),
    "redis_oom_persistent": ("redis-cart", "OOM_KILLED", "Redis_Restart_SOP"),
    "network_partition": ("paymentservice", "CONNECTION_REFUSED", "Payment_Restart_SOP"),
    "external_pause": ("productcatalogservice", "CRASH_LOOPING", "ProductCatalog_Restart_SOP"),
}


def wait_healthy(client, timeout: float = 90) -> dict:
    deadline, rounds, snapshot = time.monotonic() + timeout, 0, {}
    while time.monotonic() < deadline:
        snapshot = observe_many(client, SERVICES)
        rounds = rounds + 1 if len(snapshot) == len(SERVICES) and all(row.healthy for row in snapshot.values()) else 0
        if rounds >= 2:
            return {name: row.to_dict() for name, row in snapshot.items()}
        time.sleep(2)
    raise RuntimeError("Complete two-round healthy observation was not obtained")


def wait_detected(target: str, condition: str, since: float, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event, report = matching_incident(target, since)
        if report is not None:
            raise RuntimeError("An active remediator interfered with this observe-only trial")
        if event is not None:
            if event.get("condition") != condition:
                raise RuntimeError("Collector detected a different condition")
            return event
        time.sleep(1)
    raise RuntimeError("Collector did not independently create a matching incident")


def inject(case: str, client):
    if case == "external_pause":
        fault_injector._get_container(client, CASES[case][0]).pause()
    elif case == "network_partition":
        fault_injector.inject_network_partition("paymentservice")
    elif case == "redis_oom":
        fault_injector.inject_redis_oom()
    else:
        fault_injector.inject_persistent_redis_oom()


def reset(case: str, client):
    if case == "external_pause":
        container = fault_injector._get_container(client, CASES[case][0])
        if container.status == "paused":
            container.unpause()
    elif case == "network_partition":
        fault_injector.reset_network_partition("paymentservice")
    elif case == "redis_oom":
        fault_injector.reset_redis_oom()
    else:
        fault_injector.reset_persistent_redis_oom()
        # Restore the exact Compose command, limits and healthcheck after the special recreate.
        import subprocess
        subprocess.run(["docker", "compose", "-f", str(fault_injector.COMPOSE_FILE),
                        "up", "-d", "--force-recreate", "redis-cart"], check=True, timeout=90)


def run(cases: list[str]):
    if not cases or any(case not in CASES for case in cases):
        raise ValueError("Choose supported live acceptance cases")
    state = json.loads((settings.audit_dir / "collector_status.json").read_text())
    if state.get("delivery_enabled") is not False or not collector_ready(require_delivery=False):
        raise RuntimeError("A fresh --observe-only collector is required; automatic delivery must be disabled")
    client = docker.DockerClient(base_url=settings.docker_host, timeout=10)
    graph = GraphClient()
    path = result_path("live_executor_v1")
    output = {"metadata": metadata("live_executor_acceptance"),
              "methodology": "Fixed operator-selected SOPs, no LLM or API delivery. Collector detection, "
                             "real application canaries and two-round independent probes. "
                             "Not an autonomous-agent success-rate or MTTR experiment.", "trials": []}
    try:
        image = client.images.get(settings.sop_executor_image)
        output["executor_image"] = {"id": image.id, "architecture": image.attrs.get("Architecture")}
        for case in cases:
            target, condition, skill_name = CASES[case]
            trial = {"case": case, "target": target, "condition": condition, "executions": [], "passed": False}
            output["trials"].append(trial)
            touched = False
            try:
                trial["before"] = wait_healthy(client)
                trial["application_before"] = check_application()
                if not trial["application_before"]["success"]:
                    raise RuntimeError("Application baseline canary failed; no fault injected")
                # Let the collector observe the baseline and re-arm any prior episode.
                time.sleep(12)
                started = time.time()
                touched = True
                inject(case, client)
                trial["injected_at"] = started
                observation = observe(client, target)
                trial["fault_observation"] = observation.to_dict()
                if observation.status != condition:
                    raise RuntimeError("Independent probe did not confirm the intended fault")
                trial["collector_event"] = wait_detected(target, condition, started)
                trial["application_during"] = check_application()
                result = graph.get_root_cause("frontend", "DEP_ERROR")
                trial["graph_result"] = result.model_dump(mode="json")
                if result.candidate_roots != [target]:
                    raise RuntimeError("Live graph root is ambiguous or does not match the observed target")
                skill = next((item for item in graph.get_skills(target, condition) if item.name == skill_name), None)
                if skill is None:
                    raise RuntimeError("Fixed acceptance SOP is not applicable in the current catalog")
                for _ in range(2):
                    execution = execute_sop(_resolve_host_path(skill.script_path),
                                            skill.script_type, skill.risk_level,
                                            {"TARGET_CONTAINER": target}, skill.timeout_seconds)
                    trial["executions"].append({"skill": skill.name, **execution.model_dump(mode="json")})
                    if not execution.success:
                        raise RuntimeError("Curated executor operation failed or cleanup was incomplete")
                    time.sleep(3)
                    root_after = observe(client, target)
                    trial["executions"][-1]["observation_after"] = root_after.to_dict()
                    if root_after.healthy:
                        break
                    if case != "redis_oom_persistent" or root_after.status != condition:
                        raise RuntimeError("Root did not recover after the fixed SOP")
                    skill = graph.get_next_skill(skill.name, target)
                    if skill is None:
                        raise RuntimeError("No valid same-target fallback")
                trial["after_remediation"] = wait_healthy(client)
                trial["application_after"] = check_application()
                if not trial["application_after"]["success"]:
                    raise RuntimeError("Application did not recover after remediation")
                trial["passed"] = True
            except Exception as exc:
                trial["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                if touched:
                    try:
                        reset(case, client)
                        trial["after_reset"] = wait_healthy(client)
                    except Exception as exc:
                        trial.update(passed=False, reset_error=f"{type(exc).__name__}: {exc}")
                trial["remaining_sop_containers"] = [item.name for item in client.containers.list(
                    all=True, filters={"label": "rca.role=sop-executor"})]
                if trial["remaining_sop_containers"]:
                    trial["passed"] = False
                write_json_atomic(path, output)
            if trial.get("reset_error"):
                break
        output["all_passed"] = len(output["trials"]) == len(cases) and all(row["passed"] for row in output["trials"])
        write_json_atomic(path, output)
    finally:
        graph.close()
        client.close()
    return path, output["all_passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), action="append", required=True)
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize physical injection and SOP execution")
    args = parser.parse_args()
    if not args.execute:
        print(f"Planned cases: {args.case}. No operations performed; add --execute to run.")
        return
    path, passed = run(args.case)
    print(path)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
