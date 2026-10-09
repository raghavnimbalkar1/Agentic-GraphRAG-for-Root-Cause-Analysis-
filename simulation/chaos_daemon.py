"""
simulation/chaos_daemon.py — unattended chaos engineering autonomy run.

This is a validation harness, not evidence until an identified run completes.
When enabled it injects real faults on seeded schedules of
eligible services at random intervals and then DOES NOTHING ELSE — it never
fires an alert. The running telemetry_collector must detect each fault
organically through its normal polling and raise the incident; the agent then
resolves it. The daemon only observes and records the full lifecycle of each
incident from the agent and collector logs.

It runs serially (one fault in flight at a time) so each incident's timeline is
unambiguous, and writes a presentation-grade log + summary to
eval/results/chaos_run_<timestamp>.log — a citable thesis artifact.

Prereqs (must already be running):
    docker compose stacks up · python -m agent.main · python -m simulation.telemetry_collector

Run:
    python -m simulation.chaos_daemon --duration 600        # 10 minutes
    python -m simulation.chaos_daemon --duration 600 --min-incidents 15
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import get_logger
from core.audit import write_json_atomic
from eval.research_benchmark import metadata
from simulation.fault_injector import FAULTS

log = get_logger(__name__)

PROJECT_ROOT  = Path(__file__).resolve().parents[1]
AUDIT_DIR     = PROJECT_ROOT / "audit"
RESULTS_DIR   = PROJECT_ROOT / "eval" / "results"
AGENT_LOG     = Path("/tmp/agent_server.log")
COLLECTOR_LOG = Path("/tmp/telemetry.log")

# Eligible chaos faults: (fault_name, target). All are fast to inject, reliably
# detectable by the collector, and resolvable by the agent. Excluded on purpose:
#   - service_crash: `restart: unless-stopped` can revive the container faster
#     than the 5s poll, so detection is racy (documented honestly).
# Persistent Redis fallback and the crash race require dedicated coverage runs.
CHAOS_FAULTS: list[tuple[str, Optional[str]]] = [
    ("stale_data", None),
    ("config_drift", None),
    ("connection_pool_exhaustion", None),
    ("disk_pressure", "emailservice"),
    ("memory_leak", "recommendationservice"),
    ("high_cpu", "adservice"),
    ("dependency_timeout", "frontend"),
    ("network_partition", "paymentservice"),
]
# Faults whose agent remediation leaves residue (a capped burner) and so need an
# explicit reset before the next round. Others self-clean via the agent's fix.
NEEDS_RESET = {"high_cpu"}

# Map fault -> the service the collector will see as unhealthy (for log correlation).
FAULT_TARGET = {f: (t or "redis-cart") for f, t in CHAOS_FAULTS}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hhmmss(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M:%S")


def _parse_ts(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _read_json_events(path: Path, since_line: int) -> list[dict]:
    """Return parsed JSON log records appended after `since_line`."""
    if not path.exists():
        return []
    out = []
    with open(path, errors="replace") as f:
        for i, line in enumerate(f):
            if i < since_line:
                continue
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    return out


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, errors="replace") as f:
        return sum(1 for _ in f)


@dataclass
class Incident:
    fault: str
    service: str
    t_inject: datetime
    detected: bool = False
    resolved: bool = False
    escalated: bool = False
    condition: str = ""
    t_detect: Optional[datetime] = None
    root: str = ""
    depth: int = 0
    sop: list[str] = field(default_factory=list)
    t_resolve: Optional[datetime] = None
    reason: str = ""
    alert_id: str = ""
    injection_succeeded: bool = False
    cleanup_verified: bool = False

    @property
    def detect_latency(self) -> Optional[float]:
        if self.t_detect:
            return (self.t_detect - self.t_inject).total_seconds()
        return None

    @property
    def mttr(self) -> Optional[float]:
        if self.t_detect and self.t_resolve:
            return (self.t_resolve - self.t_detect).total_seconds()
        return None


_running = True


def _stop(*_a):
    global _running
    _running = False


def run_incident(fault: str, target: Optional[str], emit) -> Incident:
    """Follow the exact collector ID; confirm recovery independently of the report."""
    from agent.nodes.evaluator import verify_incident
    from core.health import SERVICES
    from simulation.incident_tracking import collector_ready, matching_incident

    inject_fn, reset_fn, default_target = FAULTS[fault]
    service = target or default_target or "redis-cart"
    inc = Incident(fault=fault, service=service, t_inject=_now())
    try:
        baseline = verify_incident(SERVICES)
    except Exception as exc:
        inc.reason = f"Baseline unavailable: {type(exc).__name__}"
        return inc
    if not collector_ready() or len(baseline) != len(SERVICES) or not all(row["healthy"] for row in baseline.values()):
        inc.reason = "No fresh healthy baseline / collector; injection not attempted"
        return inc
    inc.t_inject = _now()
    since = inc.t_inject.timestamp()
    try:
        inject_fn(service) if default_target is not None else inject_fn()
        inc.injection_succeeded = True
        emit(f"Injected {fault} on {service}; waiting for its collector incident")
        deadline = time.monotonic() + 180
        report = None
        while _running and time.monotonic() < deadline:
            event, report = matching_incident(service, since)
            if event:
                inc.alert_id = event["alert_id"]
                inc.detected = True
                inc.condition = event["condition"]
                inc.t_detect = datetime.fromtimestamp(event["detected_at"], timezone.utc)
            if report:
                break
            time.sleep(0.5)
        if report:
            inc.root = report["root_cause_node"]
            inc.depth = max(len(report["dependency_chain"]) - 1, 0)
            inc.sop = report["skills_executed"]
            inc.escalated = report["resolution_status"] == "ESCALATED"
            fresh = verify_incident(SERVICES)
            inc.resolved = (report.get("schema_version", 0) >= 2
                            and report["resolution_status"] == "RESOLVED"
                            and report.get("all_services_healthy", False)
                            and len(fresh) == len(SERVICES)
                            and all(row["healthy"] for row in fresh.values()))
            if inc.resolved:
                inc.t_resolve = _now()
            else:
                inc.reason = report.get("notes") or "No independently confirmed full recovery"
        else:
            inc.reason = "No matching terminal report before timeout"
    except Exception as exc:
        inc.reason = f"Trial error: {type(exc).__name__}: {exc}"
    finally:
        try:
            reset_fn(service) if default_target is not None else reset_fn()
            fresh = verify_incident(SERVICES)
            inc.cleanup_verified = len(fresh) == len(SERVICES) and all(row["healthy"] for row in fresh.values())
        except Exception as exc:
            inc.reason += f" Cleanup failed: {type(exc).__name__}"
    emit(f"Incident {inc.alert_id or '(none)'}: {'RESOLVED' if inc.resolved else 'UNRESOLVED'}; {inc.reason}")
    return inc


def _summary(incidents: list[Incident], manual_alerts: int,
             started: datetime, ended: datetime) -> str:
    n = len(incidents)
    detected = [i for i in incidents if i.detected]
    resolved = [i for i in incidents if i.resolved]
    escalated = [i for i in incidents if i.escalated]
    misses = [i for i in incidents if not i.detected or (not i.resolved and not i.escalated)]
    det_lat = [i.detect_latency for i in detected if i.detect_latency is not None]
    mttrs = [i.mttr for i in resolved if i.mttr is not None]

    def mean_text(xs): return f"{sum(xs) / len(xs):.1f}s" if xs else "unavailable"

    lines = [
        "", "=" * 72,
        "  CHAOS AUTONOMY RUN — SUMMARY",
        "=" * 72,
        f"  Window:                  {_hhmmss(started)} → {_hhmmss(ended)} "
        f"({(ended - started).total_seconds()/60:.1f} min)",
        f"  Total attempted:         {n}",
        f"  Injection commands OK:   {sum(i.injection_succeeded for i in incidents)}",
        f"  Detected autonomously:   {len(detected)}  "
        f"({(100*len(detected)/n):.0f}% of attempted)" if n else "  No trials measured",
        f"  Resolved:                {len(resolved)}",
        f"  Escalated:               {len(escalated)}",
        f"  Mean detection latency:  {mean_text(det_lat)}   (injection → collector detection)",
        f"  Mean recovery delay:     {mean_text(mttrs)}   (detection → independent confirmation)",
        "",
        f"  >>> MANUAL ALERTS FIRED BY THE DAEMON: {manual_alerts}  "
        f"(every incident was raised by the collector alone) <<<",
        "",
    ]
    if misses:
        lines.append("  Undetected / unresolved:")
        for i in misses:
            lines.append(f"    - {i.fault} on {i.service}: {i.reason or 'see above'}")
    else:
        lines.append("  Undetected / unresolved: NONE")
    lines += [
        "",
        "  Excluded from the chaos set (documented): service_crash (auto-restart",
        "  races the poll), Redis cap/fallback cases (dedicated coverage runs).",
        "  Command success does not independently prove application failure.",
        "=" * 72,
    ]
    return "\n".join(lines)


def campaign_record(incidents: list[Incident], started: datetime, ended: datetime) -> dict:
    detected = [i for i in incidents if i.detected]
    resolved = [i for i in incidents if i.resolved]
    det_lat = [i.detect_latency for i in detected if i.detect_latency is not None]
    recovery = [i.mttr for i in resolved if i.mttr is not None]
    return {
        "schema_version": 2, "kind": "collector_driven_recovery",
        "started": started.isoformat(), "ended": ended.isoformat(),
        "duration_min": round((ended - started).total_seconds() / 60, 1),
        "manual_alerts_fired": 0, "total_attempted": len(incidents),
        "total_injected": sum(i.injection_succeeded for i in incidents),
        "detected": len(detected),
        "detection_rate_pct": round(100 * len(detected) / len(incidents), 1) if incidents else None,
        "rate_denominator": "all attempted trials; injection_succeeded means command completion, not proven application failure",
        "resolved": len(resolved), "escalated": sum(i.escalated for i in incidents),
        "mean_detect_latency_s": sum(det_lat) / len(det_lat) if det_lat else None,
        "mean_mttr_s": sum(recovery) / len(recovery) if recovery else None,
        "timing_note": "Legacy mttr_s here means collector detection to independent recovery confirmation; not agent handling time",
        "incidents": [{
            "fault": i.fault, "service": i.service, "condition": i.condition,
            "alert_id": i.alert_id, "injection_succeeded": i.injection_succeeded,
            "cleanup_verified": i.cleanup_verified,
            "injection_at": i.t_inject.isoformat(),
            "detection_at": i.t_detect.isoformat() if i.t_detect else None,
            "recovery_confirmed_at": i.t_resolve.isoformat() if i.t_resolve else None,
            "injection_to_recovery_s": (i.t_resolve - i.t_inject).total_seconds() if i.t_resolve else None,
            "detected": i.detected, "detect_latency_s": i.detect_latency,
            "root": i.root, "depth": i.depth, "sop": i.sop,
            "status": ("INVALID" if not i.injection_succeeded else "RESOLVED" if i.resolved
                       else "ESCALATED" if i.escalated else "UNRESOLVED" if i.detected else "MISS"),
            "mttr_s": i.mttr, "reason": i.reason,
        } for i in incidents],
    }


def run(duration: float, min_incidents: int, seed: int = 42) -> None:
    global _running
    _running = True
    if duration <= 0 or min_incidents < 1:
        raise ValueError("duration must be positive and min_incidents must be at least one")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    rng = random.Random(seed)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    log_path = RESULTS_DIR / f"chaos_run_{stamp}.log"
    fh = open(log_path, "w")

    def emit(line: str):
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    started = _now()
    started_clock = time.monotonic()
    run_metadata = {**metadata("collector_driven_recovery"), "seed": seed,
                    "eligible_faults": CHAOS_FAULTS, "target_duration_s": duration,
                    "target_min_incidents": min_incidents}
    json_path = RESULTS_DIR / f"chaos_run_{stamp}.json"
    emit("=" * 72)
    emit("  AGENTIC GraphRAG — UNATTENDED CHAOS AUTONOMY RUN")
    emit(f"  started {started.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} · "
         f"target {duration/60:.0f} min · min incidents {min_incidents}")
    emit("  The daemon injects faults and NEVER fires an alert. Detection and")
    emit("  resolution below are performed autonomously by the collector + agent.")
    emit("=" * 72)
    emit("")

    incidents: list[Incident] = []
    schedule = []
    try:
        while _running:
            elapsed = time.monotonic() - started_clock
            if (elapsed >= duration and len(incidents) >= min_incidents) or elapsed >= duration * 2:
                break
            if not schedule:
                schedule = list(CHAOS_FAULTS)
                rng.shuffle(schedule)
            fault, target = schedule.pop()
            inc = run_incident(fault, target, emit)
            incidents.append(inc)
            write_json_atomic(json_path, {**campaign_record(incidents, started, _now()), "metadata": run_metadata})
            if not inc.injection_succeeded or not inc.cleanup_verified:
                emit("Stopping campaign: injection/baseline cleanup did not pass")
                break
            gap = rng.uniform(5, 18)
            emit(f"           … next fault in {gap:.0f}s\n")
            until = time.monotonic() + gap
            while _running and time.monotonic() < until:
                time.sleep(0.5)
    finally:
        ended = _now()
        try:
            write_json_atomic(json_path, {**campaign_record(incidents, started, ended), "metadata": run_metadata})
            emit(_summary(incidents, manual_alerts=0, started=started, ended=ended))
        finally:
            fh.close()

    print(f"\nFull log written to: {log_path}")
    print(f"JSON summary written to: {json_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Unattended chaos autonomy run")
    ap.add_argument("--duration", type=float, default=600.0, help="seconds (default 600)")
    ap.add_argument("--min-incidents", type=int, default=15,
                    help="keep running past --duration until this many incidents")
    ap.add_argument("--seed", type=int, default=42, help="seeded order within each eligible-fault cycle")
    args = ap.parse_args()
    run(args.duration, args.min_incidents, args.seed)
