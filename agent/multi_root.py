"""
agent/multi_root.py

Limited independent-root orchestration over the single-root agent.

The single-root agent (agent/graph.py) localises and remediates ONE root cause
per invocation. Its localisation heuristic (Q1: deepest-unhealthy on the alert's
path) assumes a single fault. This orchestrator lifts that assumption WITHOUT
touching the core loop: it reads the whole graph, finds every INDEPENDENT root
cause (an unhealthy service with no unhealthy dependency of its own), and
dispatches the agent at each one in turn. Correlated multi-fault inference is
not implemented. Manual dispatch requires fresh evidence, no automatic delivery,
and an exact-ID terminal acknowledgment; uncertain execution stops the batch.

Why an orchestration layer rather than a Q1 rewrite: the single-root path is what
every demo, benchmark, and the paper rely on. Composing it keeps that path
byte-identical and adds multi-fault handling as a thin, independently-testable
layer. Two faults in different subtrees (e.g. redis-cart OOM + adservice
HIGH_CPU) are two independent roots and are remediated independently.

Run:
    python -m agent.multi_root                 # detect + resolve all roots now
    python -m agent.multi_root --dry-run       # just list the independent roots
"""

from __future__ import annotations

import argparse
import time
from uuid import uuid4

import httpx

from core import get_logger, settings
from graph.graph_client import GraphClient
from simulation.incident_tracking import collector_ready

log = get_logger(__name__)

ALERT_URL = f"http://localhost:{settings.alert_listen_port}/alert"


def fresh_snapshot(gc: GraphClient) -> dict:
    from core.health import SERVICES
    snapshot = gc.get_service_snapshot()
    if any(name not in snapshot or snapshot[name].get("status") in (None, "UNKNOWN")
           or snapshot[name].get("age_seconds") is None
           or not 0 <= snapshot[name]["age_seconds"] <= settings.observation_max_age for name in SERVICES):
        raise ValueError("Complete fresh health evidence is required for independent-root dispatch")
    return {name: snapshot[name] for name in SERVICES}


def resolve_all_roots(gc: GraphClient, agent_url: str = ALERT_URL,
                      per_root_timeout: float = 180.0) -> dict:
    """
    Detect every independent root cause and dispatch the single-root agent at
    each. Returns a combined report. The agent's /alert is synchronous, so each
    root is fully resolved (or escalated) before the next is dispatched.
    """
    if collector_ready():
        raise ValueError("Automatic collector delivery owns incident dispatch; do not run a manual batch concurrently")
    fresh_snapshot(gc)
    roots = gc.get_independent_roots()
    log.info("multi_root_start", n_roots=len(roots),
             roots=[r["name"] for r in roots])

    incidents = []
    for r in roots:
        name, status = r["name"], r["status"]
        t0 = time.time()
        alert_id = f"INC-MANUAL-{uuid4().hex.upper()}"
        try:
            snapshot = fresh_snapshot(gc)
            if name not in snapshot or snapshot[name]["status"] != status:
                incidents.append({"root": name, "condition": status, "resolution": "SKIPPED",
                                  "error": "Root observation changed before dispatch", "elapsed_s": 0})
                continue
            resp = httpx.post(
                agent_url,
                json={"alert_id": alert_id, "service": name, "error_type": status,
                      "message": f"multi-root orchestrator: {status} on {name}"},
                timeout=per_root_timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            if (body.get("alert_id") != alert_id
                    or body.get("status") not in {"RESOLVED", "ESCALATED", "FAILED", "PARTIAL"}):
                raise ValueError("No matching terminal acknowledgment; execution may still be active")
            incidents.append({
                "alert_id": alert_id,
                "root": name, "condition": status,
                "resolution": body.get("status"),
                "skills_executed": body.get("skills_executed", []),
                "elapsed_s": round(time.time() - t0, 2),
            })
            log.info("multi_root_incident_done", root=name,
                     resolution=body.get("status"))
        except Exception as e:  # noqa: BLE001
            incidents.append({"root": name, "condition": status, "alert_id": alert_id,
                              "resolution": "ERROR", "error": str(e),
                              "elapsed_s": round(time.time() - t0, 2)})
            log.error("multi_root_incident_failed", root=name, error=str(e))
            break

    try:
        snapshot = fresh_snapshot(gc)
        remaining = sum(row["status"] != "HEALTHY" for row in snapshot.values())
    except Exception:
        remaining = None
    report = {
        "roots_detected": len(roots),
        "roots": [r["name"] for r in roots],
        "incidents": incidents,
        "resolved_count": sum(1 for i in incidents if i["resolution"] == "RESOLVED"),
        "all_healthy": remaining == 0 and not any(row["resolution"] == "ERROR" for row in incidents),
        "services_still_unhealthy": remaining,
    }
    log.info("multi_root_complete", **{k: report[k] for k in
             ("roots_detected", "resolved_count", "all_healthy",
              "services_still_unhealthy")})
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-fault RCA orchestrator")
    ap.add_argument("--dry-run", action="store_true",
                    help="only list independent roots, do not remediate")
    args = ap.parse_args()

    gc = GraphClient()
    fresh_snapshot(gc)
    roots = gc.get_independent_roots()

    if not roots:
        print("No unhealthy services — nothing to resolve.")
        return

    print(f"Independent root cause(s) detected: {len(roots)}")
    for r in roots:
        print(f"  • {r['name']:26} {r['status']}")

    if args.dry_run:
        return

    print("\nDispatching the single-root agent at each root…\n")
    report = resolve_all_roots(gc)

    print("─" * 60)
    for i in report["incidents"]:
        mark = "ok  " if i["resolution"] == "RESOLVED" else "FAIL"
        print(f"  {mark} {i['root']:26} {i['resolution']:10} "
              f"{', '.join(i.get('skills_executed', []))}  ({i['elapsed_s']}s)")
    print("─" * 60)
    print(f"Resolved {report['resolved_count']}/{report['roots_detected']} roots · "
          f"all services healthy: {report['all_healthy']}")


if __name__ == "__main__":
    main()
