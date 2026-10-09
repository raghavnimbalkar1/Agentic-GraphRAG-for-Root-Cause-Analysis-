"""Shared, fail-closed observations for detection and post-remediation checks."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import time

import httpx
from docker.errors import NotFound

from core.config import settings

NETWORK = "boutique-sim"
PORTS = {"emailservice": 8080, "productcatalogservice": 3550, "currencyservice": 7000,
         "paymentservice": 50051, "shippingservice": 50051, "adservice": 9555,
         "cartservice": 7070, "recommendationservice": 8080, "checkoutservice": 5050}
SERVICES = ["redis-cart", *PORTS, "frontend", "loadgenerator"]
OOM_CEILING = 10 * 1024 * 1024
DISK_CEILING = 100 * 1024 * 1024
MEMORY_CEILING = 300 * 1024 * 1024
LATENCY_BUDGET = 2.0


@dataclass
class Observation:
    status: str
    detail: str
    measured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    measurements: dict = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.status == "HEALTHY"

    def to_dict(self) -> dict:
        return asdict(self)


def cpu_percent(stats: dict) -> float | None:
    try:
        cpu, previous = stats["cpu_stats"], stats["precpu_stats"]
        delta = cpu["cpu_usage"]["total_usage"] - previous["cpu_usage"]["total_usage"]
        system = cpu["system_cpu_usage"] - previous["system_cpu_usage"]
        cores = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage", []))
        if system > 0 and delta >= 0 and cores > 0:
            return delta / system * cores * 100
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _redis(container, *command: str) -> str:
    result = container.exec_run(["redis-cli", "--raw", *command], demux=False)
    if result.exit_code != 0 or result.output is None:
        raise ValueError("Redis probe command failed")
    output = result.output.decode(errors="replace").strip()
    if output.startswith(("ERR ", "NOAUTH", "(error)")):
        raise ValueError("Redis probe rejected")
    return output


def _redis_health(container) -> Observation:
    if _redis(container, "PING") != "PONG":
        return Observation("CONNECTION_REFUSED", "Redis did not answer PING")
    memory = int(_redis(container, "CONFIG", "GET", "maxmemory").splitlines()[-1])
    if memory < 0:
        raise ValueError("Invalid Redis memory sample")
    if 0 < memory <= OOM_CEILING:
        return Observation("OOM_KILLED", "Redis memory cap below baseline", measurements={"maxmemory": memory})
    info = _redis(container, "INFO", "clients")
    fields = dict(line.split(":", 1) for line in info.splitlines() if ":" in line)
    clients = int(fields["connected_clients"])
    if clients > 50:
        return Observation("POOL_EXHAUSTION", "Redis client-count anomaly", measurements={"clients": clients})
    policy = _redis(container, "CONFIG", "GET", "maxmemory-policy").splitlines()[-1]
    if policy != "allkeys-lru":
        return Observation("CONFIG_DRIFT", "Redis eviction policy differs from baseline", measurements={"policy": policy})
    keyspace = _redis(container, "INFO", "keyspace")
    if "# Keyspace" not in keyspace:
        raise ValueError("Missing Redis keyspace sample")
    expires = 0
    for line in keyspace.splitlines():
        if line.startswith("db") and ":" in line:
            values = dict(part.split("=", 1) for part in line.split(":", 1)[1].split(","))
            expires += int(values["expires"])
    if expires >= 200:
        return Observation("STALE_DATA", "Large expiring-key anomaly", measurements={"expires": expires})
    return Observation("HEALTHY", "Redis responsive; capacity, policy, clients and keyspace normal",
                       measurements={"maxmemory": memory, "clients": clients, "expires": expires})


# The approved probe program runs without a Docker socket on the simulation network.
GRPC_PROGRAM = '''
import concurrent.futures, json, sys
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
def probe(item):
    name, port = item
    try:
        with grpc.insecure_channel(f"{name}:{port}") as channel:
            response = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(), timeout=2)
            return name, {"status": "HEALTHY" if response.status == 1 else "DEGRADED", "detail": f"gRPC health status {response.status}"}
    except Exception as exc:
        return name, {"status": "DEGRADED", "detail": f"gRPC readiness failed: {type(exc).__name__}"}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    print(json.dumps(dict(pool.map(probe, json.loads(sys.argv[1]).items()))))
'''


def grpc_readiness(client, services: list[str]) -> dict[str, Observation]:
    targets = {name: PORTS[name] for name in services if name in PORTS}
    if not targets:
        return {}
    container = None
    try:
        container = client.containers.run(
            settings.sop_executor_image, ["python3", "-c", GRPC_PROGRAM, json.dumps(targets)],
            network=NETWORK, detach=True, remove=False, user="1000:1000", read_only=True,
            cap_drop=["ALL"], security_opt=["no-new-privileges"], mem_limit="128m",
            nano_cpus=250_000_000, pids_limit=32, tmpfs={"/tmp": "size=8m"},
            labels={"rca.role": "readiness-probe"},
        )
        if container.wait(timeout=12).get("StatusCode") != 0:
            raise ValueError("Readiness probe failed; rebuild the executor image")
        payload = json.loads(container.logs(stdout=True, stderr=False))
        return {name: Observation(**payload[name]) for name in targets}
    except Exception as exc:
        return {name: Observation("UNKNOWN", f"Readiness probe unavailable: {type(exc).__name__}") for name in targets}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def disk_sizes(client) -> dict[str, int]:
    result = {}
    for row in client.api.containers(all=True, size=True):
        size = row.get("SizeRw")
        if isinstance(size, int) and size >= 0:
            for name in row.get("Names", []):
                result[name.lstrip("/")] = size
    return result


def observe(client, name: str, *, readiness: dict | None = None,
            sizes: dict | None = None, http_client=None) -> Observation:
    if name not in SERVICES:
        return Observation("UNKNOWN", "Service is not in the supported deployment")
    try:
        container = client.containers.get(name)
        container.reload()
        if container.status != "running":
            return Observation("CRASH_LOOPING", f"Container state: {container.status}")
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if NETWORK not in networks:
            return Observation("CONNECTION_REFUSED", "Container disconnected from simulation network")
        if name == "redis-cart":
            return _redis_health(container)
        if name == "emailservice":
            measured = sizes if sizes is not None else disk_sizes(client)
            if name not in measured:
                return Observation("UNKNOWN", "Writable-layer measurement missing")
            if measured[name] >= DISK_CEILING:
                return Observation("DISK_PRESSURE", "Writable-layer pressure", measurements={"bytes": measured[name]})
        if name in {"adservice", "recommendationservice"}:
            stats = container.stats(stream=False)
            if name == "adservice":
                cpu = cpu_percent(stats)
                if cpu is None:
                    return Observation("UNKNOWN", "CPU sample missing")
                if cpu >= 80:
                    return Observation("HIGH_CPU", "CPU above threshold", measurements={"cpu_percent": cpu})
            else:
                memory = stats.get("memory_stats", {}).get("usage")
                if not isinstance(memory, int) or memory < 0:
                    return Observation("UNKNOWN", "Memory sample missing")
                if memory >= MEMORY_CEILING:
                    return Observation("MEMORY_LEAK", "Resident-memory pressure", measurements={"bytes": memory})
        if name == "frontend":
            start = time.monotonic()
            try:
                response = (http_client or httpx).get("http://localhost:8080/", timeout=LATENCY_BUDGET + 1)
            except httpx.HTTPError:
                return Observation("DEPENDENCY_TIMEOUT", "Frontend unavailable within deadline")
            elapsed = time.monotonic() - start
            if not 200 <= response.status_code < 400:
                return Observation("DEGRADED", f"Frontend HTTP {response.status_code}")
            if elapsed > LATENCY_BUDGET:
                return Observation("DEPENDENCY_TIMEOUT", "Frontend exceeds latency budget", measurements={"seconds": elapsed})
            return Observation("HEALTHY", "Frontend HTTP ready", measurements={"seconds": elapsed})
        if name in PORTS:
            observations = readiness if readiness is not None else grpc_readiness(client, [name])
            return observations.get(name, Observation("UNKNOWN", "Readiness sample missing"))
        return Observation("HEALTHY", "Loadgenerator process running on simulation network")
    except NotFound:
        return Observation("CRASH_LOOPING", "Container missing")
    except Exception as exc:
        return Observation("UNKNOWN", f"Probe failed: {type(exc).__name__}: {exc}")


def observe_many(client, services: list[str], *, http_client=None) -> dict[str, Observation]:
    readiness = grpc_readiness(client, services)
    try:
        sizes = disk_sizes(client) if "emailservice" in services else {}
    except Exception:
        sizes = {}
    return {name: observe(client, name, readiness=readiness, sizes=sizes, http_client=http_client)
            for name in services}
