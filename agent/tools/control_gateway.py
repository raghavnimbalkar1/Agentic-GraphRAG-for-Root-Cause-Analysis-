"""Per-attempt authorization for fixed operations on one managed container."""

from contextlib import AbstractContextManager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

from core.config import settings
from core.health import NETWORK, PORTS

SCRIPT_OPERATIONS = {
    "container/restart.sh": "restart", "redis/restart.sh": "restart_redis",
    "email/disk_cleanup.sh": "cleanup_disk", "adservice/throttle.sh": "throttle_cpu",
    "frontend/restore_cpu.sh": "restore_cpu",
}
OPERATION_TARGETS = {
    "restart": {*PORTS, "frontend"}, "restart_redis": {"redis-cart"},
    "cleanup_disk": {"emailservice"}, "throttle_cpu": {"adservice"}, "restore_cpu": {"frontend"},
}


class ControlSession(AbstractContextManager):
    def __init__(self, client, script: Path, target_name: str, timeout: int):
        root = settings.sops_dir.resolve()
        relative = script.resolve().relative_to(root).as_posix()
        self.operation = SCRIPT_OPERATIONS[relative]
        if target_name not in OPERATION_TARGETS[self.operation]:
            raise ValueError("SOP is not authorized for this target")
        self.client = client
        self.target = client.containers.get(target_name)
        self.target.reload()
        if self.target.attrs.get("Config", {}).get("Labels", {}).get("rca.managed") != "online-boutique":
            raise ValueError("Target is not owned by the RCA simulation")
        self.container_id = self.target.id
        self.deadline = time.monotonic() + timeout
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.result = None
        self.revoked = False
        self.server = None

    def authorize(self, token: str, operation: str) -> dict:
        with self.lock:
            if (self.revoked or time.monotonic() >= self.deadline
                    or not hmac.compare_digest(token, self.token) or operation != self.operation):
                raise PermissionError("Operation is not authorized for this attempt")
            if self.result is None:
                target = self.client.containers.get(self.container_id)
                target.reload()
                if target.attrs.get("Config", {}).get("Labels", {}).get("rca.managed") != "online-boutique":
                    raise PermissionError("Container ownership changed")
                try:
                    self.result = self._perform(target)
                except Exception:
                    self.result = {"success": False, "error": "Operation failed; verify before retrying"}
                    raise
            return self.result

    def _perform(self, target) -> dict:
        if self.operation in {"restart", "restart_redis"}:
            networks = target.attrs.get("NetworkSettings", {}).get("Networks", {})
            if NETWORK not in networks:
                self.client.networks.get(NETWORK).connect(target)
            if target.attrs.get("State", {}).get("Paused"):
                target.unpause()
            target.restart(timeout=5)
        elif self.operation == "cleanup_disk":
            result = target.exec_run(["rm", "-f", "/tmp/diskfill.bin"])
            if result.exit_code != 0:
                raise RuntimeError("Fixed cleanup operation failed")
        elif self.operation == "throttle_cpu":
            target.update(nano_cpus=100_000_000)
        elif self.operation == "restore_cpu":
            target.update(cpu_quota=-1)
        return {"success": True, "action": self.operation, "target": target.name}

    def __enter__(self):
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                code = 200
                try:
                    self.connection.settimeout(5)
                    size = int(self.headers.get("Content-Length", "0"))
                    if self.path != "/operate" or not 0 < size <= 512:
                        raise PermissionError("Invalid request")
                    request = json.loads(self.rfile.read(size))
                    if set(request) != {"operation"}:
                        raise PermissionError("Unexpected operation parameters")
                    authorization = self.headers.get("Authorization", "")
                    result = session.authorize(authorization.removeprefix("Bearer "), request["operation"])
                except PermissionError:
                    code, result = 403, {"success": False, "error": "Operation denied"}
                except Exception:
                    code, result = 500, {"success": False, "error": "Authorized operation failed"}
                body = json.dumps(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer((settings.control_bind, 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return {
            "RCA_CONTROL_URL": f"http://{settings.control_container_host}:{self.server.server_port}/operate",
            "RCA_CONTROL_TOKEN": self.token,
        }

    def __exit__(self, *args):
        with self.lock:
            self.revoked = True
        if self.server:
            self.server.shutdown()
            self.server.server_close()
