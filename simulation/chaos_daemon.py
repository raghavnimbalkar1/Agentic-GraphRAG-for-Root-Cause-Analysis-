"""
simulation/chaos_daemon.py — unattended chaos engineering autonomy run.

This is the AUTONOMY PROOF. When enabled it injects real faults on random
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
from graph.graph_client import GraphClient
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
#   - redis_oom (basic): its 500-key synchronous fill makes injection slow;
#     redis is still exercised via stale_data / config_drift / pool exhaustion.
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


def _wait_all_healthy(gc: GraphClient, timeout: float = 50.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            bad = {s: v for s, v in gc.get_all_service_statuses().items() if v != "HEALTHY"}
            if not bad:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
    return False


def run_incident(fault: str, target: Optional[str], gc: GraphClient,
                 emit) -> Incident:
    """Inject one fault, let the collector+agent handle it, record the lifecycle."""
    inject_fn, reset_fn, default_target = FAULTS[fault]
    service = target or default_target or FAULT_TARGET.get(fault, "redis-cart")

    audit_before = {p.name for p in AUDIT_DIR.glob("rca_*.json")}
    agent_mark = _line_count(AGENT_LOG)
    coll_mark = _line_count(COLLECTOR_LOG)

    inc = Incident(fault=fault, service=service, t_inject=_now())
    emit(f"[{_hhmmss(inc.t_inject)}] CHAOS    injected {fault} on {service} (no alert fired)")

    # Inject (the injector never alerts). Some block briefly (e.g. dd).
    try:
        if default_target is not None or target is not None:
            inject_fn(service)
        else:
            inject_fn()
    except Exception as e:  # noqa: BLE001
        inc.reason = f"injection error: {e}"
        emit(f"[{_hhmmss(_now())}] ERROR    injection failed: {e}")
        return inc

    # Wait for the agent to write a new audit report (resolution/escalation).
    report = None
    t0 = time.time()
    while time.time() - t0 < 160 and _running:
        new = {p.name for p in AUDIT_DIR.glob("rca_*.json")} - audit_before
        if new:
            newest = max(new, key=lambda n: (AUDIT_DIR / n).stat().st_mtime)
            try:
                report = json.loads((AUDIT_DIR / newest).read_text())
                break
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.5)

    # Correlate from the agent's JSON log (a single, consistent UTC source).
    # The daemon never fires alerts, so any alert_received MUST be the incident
    # the collector raised — its timestamp is the detection-manifested moment.
    agent_events = _read_json_events(AGENT_LOG, agent_mark)
    for ev in agent_events:
        if ev.get("event") == "alert_received" and ev.get("service") == service:
            inc.detected = True
            inc.condition = ev.get("error_type", "")
            inc.t_detect = _parse_ts(ev.get("timestamp", "")) or _now()
            break

    if inc.detected:
        emit(f"[{_hhmmss(inc.t_detect)}] COLLECTOR detected {inc.condition} on {service} "
             f"(detect latency +{inc.detect_latency:.1f}s from injection)")

    # Agent: Q1 root/depth (from audit) + resolution.
    if report is not None:
        inc.root = report.get("root_cause_node", "")
        chain = report.get("dependency_chain", []) or []
        inc.depth = max(len(chain) - 1, 0)
        inc.sop = report.get("skills_executed", []) or []
        status = report.get("resolution_status", "")
        inc.resolved = status == "RESOLVED"
        inc.escalated = status == "ESCALATED"
        for ev in agent_events:
            if ev.get("event") == "alert_handled" and ev.get("root_cause") == inc.root:
                inc.t_resolve = _parse_ts(ev.get("timestamp", ""))
        if inc.t_resolve is None:
            inc.t_resolve = inc.t_detect

        recv_dt = inc.t_detect or inc.t_inject
        emit(f"[{_hhmmss(recv_dt)}] AGENT    alert received, Q1 root={inc.root} depth={inc.depth}")
        mttr_str = f"{inc.mttr:.1f}s" if inc.mttr is not None else "n/a"
        verb = "RESOLVED" if inc.resolved else (status or "?")
        emit(f"[{_hhmmss(inc.t_resolve)}] AGENT    executed {','.join(inc.sop) or '(none)'}, "
             f"{verb} (MTTR {mttr_str} from detection)")
    else:
        inc.reason = ("self-healed before detection" if not inc.detected
                      else "detected but no resolution within 160s")
        emit(f"[{_hhmmss(_now())}] MISS     {fault} on {service} not resolved — {inc.reason}")

    # ── Cleanup to a known-clean baseline before the next round ──────────────
    try:
        if fault in NEEDS_RESET:
            reset_fn(service) if (default_target is not None) else reset_fn()
    except Exception:  # noqa: BLE001
        pass
    if not _wait_all_healthy(gc, 45):
        try:   # force-clean if the agent left residue
            reset_fn(service) if (default_target is not None) else reset_fn()
        except Exception:  # noqa: BLE001
            pass
        _wait_all_healthy(gc, 45)

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

    def mean(xs): return sum(xs) / len(xs) if xs else 0.0

    lines = [
        "", "=" * 72,
        "  CHAOS AUTONOMY RUN — SUMMARY",
        "=" * 72,
        f"  Window:                  {_hhmmss(started)} → {_hhmmss(ended)} "
        f"({(ended - started).total_seconds()/60:.1f} min)",
        f"  Total faults injected:   {n}",
        f"  Detected autonomously:   {len(detected)}  "
        f"({(100*len(detected)/n if n else 0):.0f}% detection rate)",
        f"  Resolved:                {len(resolved)}",
        f"  Escalated:               {len(escalated)}",
        f"  Mean detection latency:  {mean(det_lat):.1f}s   (injection → collector detection)",
        f"  Mean MTTR:               {mean(mttrs):.1f}s   (detection → resolution)",
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
        "  races the 5s poll) and basic redis_oom (slow synchronous key-fill);",
        "  redis is still exercised via stale_data / config_drift / pool exhaustion.",
        "=" * 72,
    ]
    return "\n".join(lines)


def run(duration: float, min_incidents: int) -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    log_path = RESULTS_DIR / f"chaos_run_{stamp}.log"
    fh = open(log_path, "w")

    def emit(line: str):
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    gc = GraphClient()
    started = _now()
    emit("=" * 72)
    emit("  AGENTIC GraphRAG — UNATTENDED CHAOS AUTONOMY RUN")
    emit(f"  started {started.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} · "
         f"target {duration/60:.0f} min · min incidents {min_incidents}")
    emit("  The daemon injects faults and NEVER fires an alert. Detection and")
    emit("  resolution below are performed autonomously by the collector + agent.")
    emit("=" * 72)
    emit("")

    incidents: list[Incident] = []
    _wait_all_healthy(gc, 30)

    while _running:
        elapsed = (_now() - started).total_seconds()
        if elapsed >= duration and len(incidents) >= min_incidents:
            break
        if elapsed >= duration * 2:   # hard cap to avoid runaway
            break

        fault, target = random.choice(CHAOS_FAULTS)
        inc = run_incident(fault, target, gc, emit)
        incidents.append(inc)

        gap = random.uniform(5, 18)
        emit(f"           … next fault in {gap:.0f}s\n")
        t0 = time.time()
        while _running and time.time() - t0 < gap:
            time.sleep(0.5)

    ended = _now()
    emit(_summary(incidents, manual_alerts=0, started=started, ended=ended))
    fh.close()

    # Machine-readable sidecar (citable / dashboard-consumable).
    detected = [i for i in incidents if i.detected]
    resolved = [i for i in incidents if i.resolved]
    det_lat = [i.detect_latency for i in detected if i.detect_latency is not None]
    mttrs = [i.mttr for i in resolved if i.mttr is not None]
    json_path = RESULTS_DIR / f"chaos_run_{stamp}.json"
    with open(json_path, "w") as jf:
        json.dump({
            "started": started.isoformat(), "ended": ended.isoformat(),
            "duration_min": round((ended - started).total_seconds() / 60, 1),
            "manual_alerts_fired": 0,
            "total_injected": len(incidents),
            "detected": len(detected),
            "detection_rate_pct": round(100 * len(detected) / len(incidents), 1) if incidents else 0,
            "resolved": len(resolved),
            "escalated": sum(1 for i in incidents if i.escalated),
            "mean_detect_latency_s": round(sum(det_lat) / len(det_lat), 1) if det_lat else 0,
            "mean_mttr_s": round(sum(mttrs) / len(mttrs), 1) if mttrs else 0,
            "incidents": [{
                "fault": i.fault, "service": i.service, "condition": i.condition,
                "detected": i.detected, "detect_latency_s": round(i.detect_latency, 1) if i.detect_latency else None,
                "root": i.root, "depth": i.depth, "sop": i.sop,
                "status": "RESOLVED" if i.resolved else ("ESCALATED" if i.escalated else "MISS"),
                "mttr_s": round(i.mttr, 1) if i.mttr is not None else None,
                "reason": i.reason,
            } for i in incidents],
        }, jf, indent=2)

    print(f"\nFull log written to: {log_path}")
    print(f"JSON summary written to: {json_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Unattended chaos autonomy run")
    ap.add_argument("--duration", type=float, default=600.0, help="seconds (default 600)")
    ap.add_argument("--min-incidents", type=int, default=15,
                    help="keep running past --duration until this many incidents")
    args = ap.parse_args()
    run(args.duration, args.min_incidents)
