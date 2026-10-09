"""Persistent episode identities with acknowledgment-aware alert delivery."""

import json
from pathlib import Path
import threading
import time
from uuid import uuid4

import httpx

from core.audit import write_json_atomic
from core.health import Observation


class IncidentDelivery:
    def __init__(self, path: Path, endpoint: str):
        self.path, self.endpoint = path, endpoint
        self.lock = threading.RLock()
        self.episodes = json.loads(path.read_text()) if path.exists() else {}
        self.previous = {}
        self.inflight = set()
        self.next_attempt = {}

    def observe(self, service: str, observation: Observation, eligible: bool = True) -> None:
        with self.lock:
            status = observation.status
            if status == "HEALTHY":
                self.episodes.pop(service, None)
            elif status != "UNKNOWN" and eligible and self.previous.get(service) == status and service not in self.episodes:
                self.episodes[service] = {
                    "payload": {"alert_id": f"INC-{uuid4().hex.upper()}", "service": service,
                                "error_type": status, "message": observation.detail,
                                "timestamp": observation.measured_at,
                                "metadata": {"source": "telemetry", "observation": observation.to_dict()}},
                    "state": "pending", "attempts": 0,
                }
            self.previous[service] = status
            self._save()

    def _save(self):
        write_json_atomic(self.path, self.episodes)

    def send_one(self, service: str, post=httpx.post) -> None:
        with self.lock:
            episode = self.episodes.get(service)
            if not episode or episode["state"] != "pending":
                return
            payload = dict(episode["payload"])
        try:
            response = post(self.endpoint, json=payload, timeout=180)
            response.raise_for_status()
            body = response.json()
            if body.get("alert_id") != payload["alert_id"] or body.get("status") not in {"RESOLVED", "ESCALATED", "FAILED", "PARTIAL"}:
                raise ValueError("Agent did not acknowledge this incident")
            with self.lock:
                if self.episodes.get(service) is episode:
                    episode.update(state="acknowledged", outcome=body["status"])
                    self._save()
        except Exception as exc:
            with self.lock:
                if self.episodes.get(service) is episode:
                    episode["attempts"] += 1
                    episode["error"] = type(exc).__name__
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {409, 422}:
                        episode["state"] = "attention_required"
                    self.next_attempt[service] = time.monotonic() + min(60, 2 ** min(episode["attempts"], 6))
                    self._save()

    def dispatch(self) -> None:
        with self.lock:
            for service, episode in list(self.episodes.items()):
                if (episode["state"] != "pending" or service in self.inflight
                        or time.monotonic() < self.next_attempt.get(service, 0)):
                    continue
                self.inflight.add(service)

                def worker(name=service):
                    try:
                        self.send_one(name)
                    finally:
                        with self.lock:
                            self.inflight.discard(name)

                threading.Thread(target=worker, daemon=True).start()
