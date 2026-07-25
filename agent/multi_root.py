"""
agent/multi_root.py

Multi-fault orchestration — an outer loop over the proven single-root agent.

The single-root agent (agent/graph.py) localises and remediates ONE root cause
per invocation. Its localisation heuristic (Q1: deepest-unhealthy on the alert's
path) assumes a single fault. This orchestrator lifts that assumption WITHOUT
touching the core loop: it reads the whole graph, finds every INDEPENDENT root
cause (an unhealthy service with no unhealthy dependency of its own), and
dispatches the agent at each one in turn. Each root is resolved by the same
verified detect→localise→decide→sandbox→verify→fallback machinery.

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

import httpx

from core import get_logger, settings
from graph.graph_client import GraphClient

log = get_logger(__name__)

ALERT_URL = f"http://localhost:{settings.alert_listen_port}/alert"


def resolve_all_roots(gc: GraphClient, agent_url: str = ALERT_URL,
                      per_root_timeout: float = 180.0) -> dict:
    """
    Detect every independent root cause and dispatch the single-root agent at
    each. Returns a combined report. The agent's /alert is synchronous, so each
    root is fully resolved (or escalated) before the next is dispatched.
    """
    roots = gc.get_independent_roots()
    log.info("multi_root_start", n_roots=len(roots),
             roots=[r["name"] for r in roots])

    incidents = []
    for r in roots:
        name, status = r["name"], r["status"]
        t0 = time.time()
        try:
            resp = httpx.post(
                agent_url,
                json={"service": name, "error_type": status,
                      "message": f"multi-root orchestrator: {status} on {name}"},
                timeout=per_root_timeout,
            )
            body = resp.json()
            incidents.append({
                "root": name, "condition": status,
                "resolution": body.get("status"),
                "skills_executed": body.get("skills_executed", []),
                "elapsed_s": round(time.time() - t0, 2),
            })
            log.info("multi_root_incident_done", root=name,
                     resolution=body.get("status"))
        except Exception as e:  # noqa: BLE001
            incidents.append({"root": name, "condition": status,
                              "resolution": "ERROR", "error": str(e),
                              "elapsed_s": round(time.time() - t0, 2)})
            log.error("multi_root_incident_failed", root=name, error=str(e))

    remaining = gc.count_all_unhealthy()
    report = {
        "roots_detected": len(roots),
        "roots": [r["name"] for r in roots],
        "incidents": incidents,
        "resolved_count": sum(1 for i in incidents if i["resolution"] == "RESOLVED"),
        "all_healthy": remaining == 0,
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
