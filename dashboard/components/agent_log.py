"""
dashboard/components/agent_log.py

Drives a live RCA scenario from the dashboard and reconstructs the agent's
ReAct pipeline as a step-by-step timeline.

Design
------
Fault injection changes only the simulation. The independent collector updates
Neo4j and delivers the alert. The dashboard runs injection in a background thread
while polling observed graph health, then correlates the collector's incident ID
with the agent's completed report to render the resolution timeline.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from core.config import settings

ALERT_ENDPOINT = f"http://localhost:{settings.alert_listen_port}/alert"


# ── Agent-server reachability ────────────────────────────────────────────────

def agent_alive() -> bool:
    try:
        r = httpx.get(
            f"http://localhost:{settings.alert_listen_port}/health", timeout=2.0
        )
        return r.status_code == 200
    except Exception:
        return False


# ── Background fault runner ──────────────────────────────────────────────────

@dataclass
class RunHandle:
    """Tracks a background inject+resolve run."""
    fault: str
    target: Optional[str]
    thread: threading.Thread
    started_at: float
    error: Optional[str] = None
    done: bool = field(default=False)

    def is_alive(self) -> bool:
        return self.thread.is_alive()


def start_scenario(fault: str, target: Optional[str]) -> RunHandle:
    """
    Launch physical injection on a background thread, without raising an alert.
    Returns immediately with a handle to poll for injection errors.
    """
    from simulation.fault_injector import FAULTS

    inject_fn, _reset_fn, default_target = FAULTS[fault]
    # Mirror the CLI: a None default_target means the inject function takes no
    # argument (e.g. inject_redis_oom() has a hard-coded target). Only pass a
    # target when the fault actually accepts one.
    takes_target = default_target is not None
    use_target = (target or default_target) if takes_target else None

    handle = RunHandle(fault=fault, target=use_target, thread=None,  # type: ignore
                       started_at=time.time())

    def _run():
        try:
            if takes_target:
                inject_fn(use_target)
            else:
                inject_fn()
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            handle.error = str(exc)
        finally:
            handle.done = True

    t = threading.Thread(target=_run, daemon=True)
    handle.thread = t
    t.start()
    return handle


def reset_scenario(fault: str, target: Optional[str]) -> Optional[str]:
    """Reset a fault synchronously. Returns an error string or None."""
    from simulation.fault_injector import FAULTS

    _inject_fn, reset_fn, default_target = FAULTS[fault]
    takes_target = default_target is not None
    use_target = (target or default_target) if takes_target else None
    try:
        if takes_target:
            reset_fn(use_target)
        else:
            reset_fn()
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


# ── Timeline reconstruction from an audit report ─────────────────────────────

def build_timeline(report: dict) -> list[dict]:
    """
    Turn a finished RCA report into an ordered list of pipeline stages for
    display. Each stage: {icon, title, detail}.
    """
    chain = report.get("dependency_chain", [])
    depth = max(len(chain) - 1, 0)
    history = report.get("execution_history", [])
    skills = report.get("skills_executed", [])

    stages = [
        {
            "icon": "1.",
            "title": "1. Alert ingested",
            "detail": f"{report.get('alert_service', '?')} / "
                      f"{report.get('alert_error_type', '?')}",
        },
        {
            "icon": "2.",
            "title": "2. Root cause located (Neo4j Q1 traversal)",
            "detail": f"{report.get('root_cause_node', '?')}  "
                      f"(depth {depth} via DEPENDS_ON)",
        },
        {
            "icon": "3.",
            "title": "3. SOP retrieved (Q2 — Progressive Context Injection)",
            "detail": ", ".join(skills) if skills else "no skill matched",
        },
        {
            "icon": "4.",
            "title": "4. LLM reasoning",
            "detail": (", ".join(str(attempt.get("decision")) for attempt in report.get("attempts", []))
                       or "Historical decision not recorded")
                      + (f"  ({report['tokens_used']} tokens)"
                         if report.get("tokens_used") is not None else ""),
        },
    ]

    for i, ex in enumerate(history, 1):
        ok = ex.get("success")
        stages.append({
            "icon": "ok" if ok else "failed",
            "title": f"5.{i} Sandbox execution — {ex.get('skill_name', '?')}",
            "detail": f"exit {ex.get('exit_code')} · {ex.get('duration_s', 0):.2f}s "
                      f"· isolated Docker container",
        })

    stages.append({
        "icon": "6.",
        "title": "6. Health verified (Q5)",
        "detail": "all services HEALTHY" if report.get("all_services_healthy")
                  else f"{report.get('services_still_unhealthy', '?')} still unhealthy",
    })
    stages.append({
        "icon": "7.",
        "title": f"7. {report.get('resolution_status', 'UNKNOWN')}",
        "detail": f"Agent handling {report.get('handling_seconds', report.get('mttr_seconds')):.2f}s"
                  if report.get("handling_seconds", report.get("mttr_seconds")) is not None else "Timing unavailable",
    })
    return stages
