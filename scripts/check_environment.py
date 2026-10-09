"""Read-only local preflight; --live adds dependency and denied-control probes."""

import argparse
import json
from pathlib import Path
import sys


def control_reachability(client) -> bool:
    from agent.tools.control_gateway import ControlSession
    from core.config import settings
    from core.health import NETWORK
    probe = None
    program = (
        "import os,urllib.request,urllib.error; "
        "r=urllib.request.Request(os.environ['RCA_CONTROL_URL'],data=b'{\"operation\":\"restart\"}',"
        "headers={'Authorization':'Bearer invalid-preflight-token'},method='POST'); "
        "\ntry: urllib.request.urlopen(r,timeout=5); raise SystemExit(1)"
        "\nexcept urllib.error.HTTPError as e: raise SystemExit(0 if e.code==403 else 1)"
    )
    try:
        with ControlSession(client, settings.sops_dir / "container/restart.sh", "frontend", 20) as control:
            try:
                probe = client.containers.run(
                    settings.sop_executor_image, ["python3", "-c", program],
                    environment={"RCA_CONTROL_URL": control["RCA_CONTROL_URL"]}, network=NETWORK,
                    extra_hosts={"host.docker.internal": "host-gateway"},
                    detach=True, remove=False, user="1000:1000", read_only=True,
                    cap_drop=["ALL"], security_opt=["no-new-privileges"], mem_limit="128m",
                    pids_limit=32, nano_cpus=250_000_000, labels={"rca.role": "preflight"},
                )
                return probe.wait(timeout=10).get("StatusCode") == 0
            finally:
                if probe is not None:
                    probe.remove(force=True)
    except Exception:
        return False


def check(live: bool) -> dict:
    root = Path(__file__).resolve().parents[1]
    result = {"python_compatible": sys.version_info >= (3, 11), "env_file": (root / ".env").is_file(),
              "executor_definition": (root / "sop-executor" / "Dockerfile").is_file()}
    if not live:
        result["live_checks"] = "not_requested"
        return result
    import docker
    from core.config import settings
    from core.health import SERVICES, observe_many
    from graph.graph_client import GraphClient
    from simulation.incident_tracking import collector_ready
    client = None
    try:
        client = docker.DockerClient(base_url=settings.docker_host, timeout=5)
        result["docker"] = bool(client.ping())
        observations = observe_many(client, SERVICES)
        result["service_health"] = {name: item.status for name, item in observations.items()}
        result["all_services_healthy"] = len(observations) == len(SERVICES) and all(item.healthy for item in observations.values())
        result["managed_targets"] = all(client.containers.get(name).attrs.get("Config", {}).get("Labels", {}).get("rca.managed") == "online-boutique" for name in SERVICES)
        result["restricted_control_reachable"] = control_reachability(client)
    except Exception as exc:
        result.update(docker=False, docker_error=type(exc).__name__, restricted_control_reachable=False)
    finally:
        if client is not None:
            client.close()
    try:
        graph = GraphClient()
        result["neo4j"] = graph.health_check()
        result["catalog_counts"] = graph.node_counts()
        graph.close()
    except Exception as exc:
        result.update(neo4j=False, graph_error=type(exc).__name__)
    result["collector_fresh"] = collector_ready()
    result["llm_connectivity"] = "not_probed_no_paid_call"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    result = check(args.live)
    print(json.dumps(result, indent=2))
    required = ["python_compatible", "env_file", "executor_definition"]
    if args.live:
        required += ["docker", "neo4j", "all_services_healthy", "managed_targets", "restricted_control_reachable", "collector_fresh"]
    raise SystemExit(0 if all(result.get(key, False) for key in required) else 1)


if __name__ == "__main__":
    main()
