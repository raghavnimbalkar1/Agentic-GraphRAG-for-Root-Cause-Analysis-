"""Scheduled, shadow-only checkout observations for the local mock deployment."""

import argparse
import fcntl
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from core.audit import write_json_atomic
from core.config import settings
from core.evidence import EvidenceStore
from core.registry import DEFAULT_DEPLOYMENT
from core.schemas import SymptomObservation, TransactionStep
from core.telemetry import collector_status
from simulation.application_canary import check_application


def interpret_canary(result: dict) -> tuple[str, list[TransactionStep]]:
    if not isinstance(result["steps"], list) or len(result["steps"]) > 5:
        raise ValueError("Invalid transaction step count")
    steps = [TransactionStep.model_validate({k: v for k, v in row.items() if k in TransactionStep.model_fields})
             for row in result["steps"]]
    names = [step.step for step in steps]
    application_steps = names[:-1] if names and names[-1] == "cleanup_cart" else names
    if application_steps != ["home", "add_cart", "read_cart", "checkout"][:len(application_steps)]:
        raise ValueError("Invalid transaction step order")
    probe_errors = {"ProbeBudgetExceeded", "ResponseTooLarge", "UnsafeRedirect", "ProbeError"}
    if any(s.error in probe_errors for s in steps):
        return "unknown", steps
    if any(not s.success and s.step != "cleanup_cart" for s in steps):
        return "failed", steps
    if (result.get("success") is True
            and [s.step for s in steps] == ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]
            and all(s.success for s in steps)):
        return "healthy", steps
    return "unknown", steps


class SymptomMonitor:
    def __init__(self, directory: Path, *, interval: float = 30, canary=None, store=None, clock=None):
        if not 30 <= interval <= 300:
            raise ValueError("Symptom interval must be between 30 and 300 seconds")
        self.directory, self.interval = directory, interval
        self.canary, self.clock = canary or check_application, clock or time.monotonic
        self.store = store or EvidenceStore(directory / "evidence.sqlite")
        self.stop = threading.Event()
        self.next_due = 0.0
        self.state = {"schema_version": 1, "mode": "shadow", "delivery_enabled": False,
                      "environment_id": DEFAULT_DEPLOYMENT.environment_id,
                      "registry_digest": DEFAULT_DEPLOYMENT.digest,
                      "pid": os.getpid(), "interval_seconds": interval, "last_observation": None}

    def publish(self, state: str):
        self.state.update(state=state, timestamp=time.time())
        write_json_atomic(self.directory / "symptom_monitor_status.json", self.state)

    def poll_once(self):
        if self.stop.is_set() or self.clock() < self.next_due:
            return None
        try:
            return self._collect()
        finally:
            # All failures, including status writes and validation, get a delay.
            # Never catch up in a burst after a slow probe or process suspension.
            self.next_due = self.clock() + self.interval

    def _collect(self):
        started_at = datetime.now(timezone.utc)
        started = self.clock()
        self.publish("polling")
        outcome, steps = "unknown", []
        try:
            if collector_status(self.directory)["delivery_enabled"]:
                self.state["blocked_reason"] = "automatic_component_delivery_enabled"
            else:
                self.state.pop("blocked_reason", None)
                outcome, steps = interpret_canary(self.canary())
        except Exception as exc:
            self.state["probe_error_kind"] = type(exc).__name__
        else:
            self.state.pop("probe_error_kind", None)
        record = SymptomObservation(
            environment_id=DEFAULT_DEPLOYMENT.environment_id, registry_revision=DEFAULT_DEPLOYMENT.revision,
            registry_digest=DEFAULT_DEPLOYMENT.digest, started_at=started_at,
            measured_at=datetime.now(timezone.utc), outcome=outcome,
            quality="unknown" if outcome == "unknown" else "observed", steps=steps)
        try:
            state = self.store.record_symptom(record, max_gap=2 * self.interval + 35)
            self.state.update(last_observation=record.model_dump(mode="json"), active_incident_id=state["active_id"],
                              poll_seconds=self.clock() - started, evidence_store="ready")
            self.state.pop("error_kind", None)
            self.publish("ready" if outcome != "unknown" else "degraded")
        except Exception as exc:
            self.state.update(evidence_store="unavailable", error_kind=type(exc).__name__)
            self.publish("degraded")
            raise
        return record

    def run(self, *, once: bool = False):
        try:
            while not self.stop.is_set():
                try:
                    self.poll_once()
                except Exception:
                    if once:
                        raise
                if once:
                    break
                self.stop.wait(max(0, self.next_due - self.clock()))
        finally:
            self.publish("stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Record one shadow observation and exit")
    parser.add_argument("--interval", type=float, default=30, help="Delay after each probe, 30-300 seconds")
    args = parser.parse_args()
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    with (settings.audit_dir / "symptom_monitor.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("A symptom monitor already owns this audit directory")
        monitor = SymptomMonitor(settings.audit_dir, interval=args.interval)
        handlers = {sig: signal.signal(sig, lambda *_: monitor.stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            monitor.run(once=args.once)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    main()
