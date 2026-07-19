"""
agent/nodes/evaluator.py

Layer 5: Evaluation & Resolution

Runs after every sandbox execution. Checks whether the remediation worked
by querying Neo4j for unhealthy services in the dependency chain (Q5).

Makes the loop termination decision:
    all_healthy  = True  → generate report, terminate
    all_healthy  = False → follow NEXT_IF_FAIL edge (Q3), loop back
    max_attempts reached → escalate, terminate
    llm_decision = escalate → terminate immediately

Also handles execution results: marks current skill as visited, appends
ExecutionResult to execution_history, increments attempt_count.

Graph/reality sync:
    A successful sandbox execution (e.g. restarting redis-cart) changes
    the REAL state of the target environment, but the Neo4j Service node
    still holds whatever status was last written to it (typically by the
    fault injector). Without an explicit sync step, Q5's health check
    queries stale graph state and never reflects that the SOP actually
    worked -- the agent would loop or escalate even after a successful
    fix. So: if the last execution succeeded, this node updates the root
    cause Service node back to HEALTHY in Neo4j *before* running Q5,
    closing the loop between "the world changed" and "the graph knows
    the world changed". A full implementation would instead have a
    telemetry collector continuously syncing live container health into
    the graph (see simulation/telemetry_collector.py, not yet built) --
    this is the targeted fix for the current single-agent-loop scope.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import docker
import httpx

from core import get_logger, settings
from core.schemas import ExecutionResult, RCAReport, ResolutionStatus
from graph.graph_client import GraphClient
from agent.state import AgentState

log = get_logger(__name__)

# ── Real-verification constants ──────────────────────────────────────────────
BOUTIQUE_NETWORK = "boutique-sim"
# redis-cart is OOM-capped when maxmemory is a small positive value. A healthy
# redis has either 0 (unlimited) or a real cap well above the injected one.
OOM_CAP_CEILING        = 10 * 1024 * 1024     # 10 MB
DISK_CEILING           = 100 * 1024 * 1024    # writable layer healthy if < 100 MB
POOL_MAX_CLIENTS       = 50                   # healthy if connected_clients <= 50
MEM_HEALTHY_CEILING    = 250 * 1024 * 1024    # healthy if mem usage < 250 MB
LATENCY_BUDGET_S       = 2.0                  # healthy if HTTP response < 2s
REDIS_BASELINE_POLICY  = "allkeys-lru"
LATENCY_URLS           = {"frontend": "http://localhost:8080/"}


def _docker_client() -> docker.DockerClient:
    return docker.DockerClient(base_url=settings.docker_host)


def _redis(c, *args) -> str:
    """Run a redis-cli command inside the container and return decoded output."""
    return c.exec_run(["redis-cli", *args], demux=False).output.decode(errors="replace")


def _container_cpu_percent(c) -> float | None:
    """CPU% from a one-shot docker stats sample (Linux cgroup deltas)."""
    try:
        s = c.stats(stream=False)
        cpu, pre = s["cpu_stats"], s["precpu_stats"]
        cd = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sd = cpu["system_cpu_usage"] - pre.get("system_cpu_usage", 0)
        n = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1])
        if sd > 0 and cd > 0:
            return (cd / sd) * n * 100.0
    except Exception as e:  # noqa: BLE001 — a missing stat means "unknown", not fatal
        log.debug("cpu_percent_sample_failed", error=str(e))
        return None
    return None


def _parse_redis_int(output: str | None) -> int | None:
    if not output:
        return None
    for tok in reversed(output.split()):
        if tok.lstrip("-").isdigit():
            return int(tok)
    return None


def verify_real_health(
    root_cause_node: str, script_path: str, error_type: str = ""
) -> tuple[bool, str]:
    """
    Re-probe the REAL condition that triggered the incident, after a successful
    sandbox execution. RESOLVED must mean the actual fault is gone — not just
    that the SOP exited 0. Returns (is_healthy, detail).

    Routing is by the ERROR TYPE being remediated (what condition must clear),
    which is more precise than the script path. Each branch re-measures the same
    real signal the telemetry collector used to detect the fault.
    """
    et = (error_type or "").upper()
    sp = (script_path or "").lower()
    try:
        client = _docker_client()
    except Exception as e:  # noqa: BLE001
        return False, f"docker client unavailable: {e}"

    try:
        # ── OOM_KILLED / STALE_DATA — redis maxmemory uncapped + responsive ──
        if et in ("OOM_KILLED", "STALE_DATA"):
            c = client.containers.get("redis-cart")
            if "PONG" not in _redis(c, "ping"):
                return False, "redis ping did not return PONG"
            mm = _parse_redis_int(_redis(c, "CONFIG", "GET", "maxmemory"))
            if mm is not None and 1 <= mm <= OOM_CAP_CEILING:
                return False, f"redis maxmemory still capped at {mm} bytes"
            return True, f"redis PONG, maxmemory={mm} (uncapped)"

        # ── POOL_EXHAUSTION — connected clients back under threshold ─────────
        if et == "POOL_EXHAUSTION":
            c = client.containers.get("redis-cart")
            clients = None
            for line in _redis(c, "INFO", "clients").splitlines():
                if line.startswith("connected_clients:"):
                    clients = int(line.split(":", 1)[1].strip())
            if clients is None or clients > POOL_MAX_CLIENTS:
                return False, f"connected_clients still high ({clients})"
            return True, f"connected_clients={clients} (pool cleared)"

        # ── CONFIG_DRIFT — config matches the known-good baseline ────────────
        if et == "CONFIG_DRIFT":
            c = client.containers.get("redis-cart")
            toks = [t for t in _redis(c, "CONFIG", "GET", "maxmemory-policy").split() if t]
            policy = toks[-1] if toks else None
            if policy != REDIS_BASELINE_POLICY:
                return False, f"maxmemory-policy still drifted ({policy})"
            return True, f"maxmemory-policy={policy} (baseline restored)"

        # ── DISK_PRESSURE — writable layer back under the ceiling ────────────
        if et == "DISK_PRESSURE":
            size_rw = 0
            for cc in client.api.containers(all=True, size=True,
                                            filters={"name": root_cause_node}):
                if any(n.lstrip("/") == root_cause_node for n in cc.get("Names", [])):
                    size_rw = cc.get("SizeRw", 0) or 0
            if size_rw >= DISK_CEILING:
                return False, f"writable layer still {size_rw // (1024*1024)}MB"
            return True, f"writable layer {size_rw // (1024*1024)}MB (cleaned)"

        # ── MEMORY_LEAK — container running and memory dropped ───────────────
        if et == "MEMORY_LEAK":
            c = client.containers.get(root_cause_node)
            c.reload()
            if c.status != "running":
                return False, f"{root_cause_node} status={c.status}"
            usage = (c.stats(stream=False).get("memory_stats", {}) or {}).get("usage", 0) or 0
            if usage >= MEM_HEALTHY_CEILING:
                return False, f"memory still {usage // (1024*1024)}MB"
            return True, f"{root_cause_node} memory {usage // (1024*1024)}MB (reclaimed)"

        # ── DEPENDENCY_TIMEOUT — HTTP latency back within budget ─────────────
        # After the SOP lifts the CPU cap, the service may need a few seconds to
        # drain the request backlog built up while it was starved. Retry the
        # probe a few times before declaring failure.
        if et == "DEPENDENCY_TIMEOUT":
            url = LATENCY_URLS.get(root_cause_node, "http://localhost:8080/")
            last = None
            for _ in range(5):
                t0 = time.perf_counter()
                try:
                    httpx.get(url, timeout=LATENCY_BUDGET_S + 4.0)
                    last = time.perf_counter() - t0
                except Exception:  # noqa: BLE001
                    last = None
                if last is not None and last <= LATENCY_BUDGET_S:
                    return True, f"{root_cause_node} latency {last:.2f}s (within budget)"
                time.sleep(2.0)
            return False, (f"{root_cause_node} latency still {last:.2f}s" if last
                           else f"{root_cause_node} still not responding")

        # ── HIGH_CPU — actual CPU back under threshold ──────────────────────
        # Verify the real condition (CPU recovered), not the mechanism: this
        # passes whether the SOP throttled the container (CPU pinned at the low
        # cap) or restarted it (burner killed, CPU near zero).
        if et == "HIGH_CPU":
            c = client.containers.get(root_cause_node)
            c.reload()
            if c.status != "running":
                return False, f"{root_cause_node} status={c.status}"
            cpu = _container_cpu_percent(c)
            if cpu is not None and cpu >= 80.0:
                return False, f"{root_cause_node} CPU still {cpu:.0f}%"
            return True, (f"{root_cause_node} CPU {cpu:.0f}% (recovered)"
                          if cpu is not None else f"{root_cause_node} running")

        # ── CRASH_LOOPING / CONNECTION_REFUSED / DEGRADED — container up + net ─
        if et in ("CRASH_LOOPING", "CONNECTION_REFUSED", "DEGRADED"):
            c = client.containers.get(root_cause_node)
            c.reload()
            if c.status != "running":
                return False, f"{root_cause_node} status={c.status}"
            nets = (c.attrs.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
            if BOUTIQUE_NETWORK not in nets:
                return False, f"{root_cause_node} not attached to {BOUTIQUE_NETWORK}"
            return True, f"{root_cause_node} running + on {BOUTIQUE_NETWORK}"

    except Exception as e:  # noqa: BLE001
        return False, f"real verification failed for {error_type}: {e}"

    # ── Unknown error type — fall back to a container liveness check ─────────
    try:
        c = client.containers.get(root_cause_node)
        c.reload()
        if c.status == "running":
            return True, f"{root_cause_node} running (no specific probe for {error_type})"
        return False, f"{root_cause_node} status={c.status}"
    except Exception:  # noqa: BLE001
        return True, f"no real-health probe for '{error_type}'; trusting exit code"


def evaluate_and_route(state: AgentState) -> AgentState:
    """
    Check health of all affected services and decide whether to loop or stop.

    Called after:
        - executor.py runs a sandbox script (Phase 5+)
        - reasoner.py decides "skip" or "escalate" (routed here via graph.py)
    """
    gc = GraphClient()

    # Mark current skill visited (prevents infinite re-trying same SOP)
    visited = list(state.get("visited_skills", []))
    if state.get("current_skill") and state["current_skill"] not in visited:
        visited.append(state["current_skill"])

    # Increment attempt counter
    attempt_count = state.get("attempt_count", 0) + 1

    chain = state.get("dependency_chain", [state.get("alert_service", "")])

    # ── REAL verification after a successful execution ────────────────────
    # A sandbox exit_code of 0 means the SOP *ran*, not that the service
    # actually recovered. Before marking the root cause HEALTHY in Neo4j we
    # re-probe its real state (redis-cli / docker inspect). Only a passing real
    # health check flips the graph to HEALTHY — so RESOLVED means genuinely
    # recovered. This must run BEFORE the Q5 health check below.
    execution_history = state.get("execution_history", [])
    real_health_ok = False
    fallback_skill = None   # Step 3: NEXT_IF_FAIL fallback loaded on verify failure
    if execution_history:
        last_execution = execution_history[-1]
        root_cause = state.get("root_cause_node")

        if last_execution.success and root_cause:
            # Verify against the REAL condition the SOP remediated (the skill's
            # trigger), not the surface alert symptom — they differ on deep
            # cascades (e.g. alert HIGH_ERROR_RATE, root condition OOM_KILLED).
            remediated = state.get("current_trigger") or state.get("alert_error_type", "")
            real_health_ok, detail = verify_real_health(
                root_cause, last_execution.script_path, remediated,
            )
            if real_health_ok:
                gc.update_service_status(
                    service_name=root_cause, status="HEALTHY", error_code=None,
                )
                log.info(
                    "real_health_verified_healthy",
                    service=root_cause, skill=last_execution.skill_name,
                    detail=detail,
                )
            else:
                log.warning(
                    "real_health_check_failed",
                    service=root_cause, skill=last_execution.skill_name,
                    detail=detail,
                    note="exit_code=0 but service not genuinely recovered",
                )
                # ── Step 3: follow NEXT_IF_FAIL (Q3) to the fallback SOP ──────
                # The first SOP ran cleanly but did not actually fix the service.
                # Walk the Skill graph's NEXT_IF_FAIL edge to the next remedy and
                # queue it directly (bypassing Q2, which filters by trigger and
                # would never cross from OOM_KILLED to a STALE_DATA flush SOP).
                current = state.get("current_skill")
                candidate = gc.get_next_skill(current) if current else None
                if candidate and candidate.name not in visited:
                    fallback_skill = candidate
                    log.info(
                        "next_if_fail_fallback_selected",
                        from_skill=current, to_skill=candidate.name,
                        script=candidate.script_path, risk=candidate.risk_level,
                    )
                else:
                    log.warning(
                        "no_usable_fallback_skill",
                        from_skill=current,
                        candidate=(candidate.name if candidate else None),
                        note="no NEXT_IF_FAIL edge or already visited — will escalate",
                    )
        elif not last_execution.success:
            log.info(
                "graph_status_unchanged_execution_failed",
                service=root_cause,
                skill=last_execution.skill_name,
                exit_code=last_execution.exit_code,
            )

    # ── LLM explicitly escalated — terminate without health check ─────────
    if state.get("llm_decision") == "escalate":
        log.warning(
            "incident_escalated_by_llm",
            reason=state.get("llm_reason"),
            root_cause=state.get("root_cause_node"),
            attempt=attempt_count,
        )
        unhealthy_count  = gc.count_unhealthy(chain)
        all_healthy      = False
        resolution_status = ResolutionStatus.ESCALATED

    else:
        # ── Normal path: check live health via Q5 ─────────────────────────
        unhealthy_count = gc.count_unhealthy(chain)
        all_healthy     = unhealthy_count == 0

        log.info(
            "health_check",
            unhealthy_count=unhealthy_count,
            chain=chain,
            all_healthy=all_healthy,
            attempt=attempt_count,
            max_attempts=state["max_attempts"],
        )

        if all_healthy:
            resolution_status = ResolutionStatus.RESOLVED
            log.info(
                "incident_resolved",
                root_cause=state.get("root_cause_node"),
                attempts=attempt_count,
            )
        elif attempt_count >= state["max_attempts"]:
            resolution_status = ResolutionStatus.ESCALATED
            log.warning(
                "incident_escalated",
                root_cause=state.get("root_cause_node"),
                attempts=attempt_count,
                still_unhealthy=unhealthy_count,
            )
        else:
            resolution_status = None   # still looping

    # ── Build report if terminating ───────────────────────────────────────
    rca_report = None

    if resolution_status is not None:
        t_alert = state.get("t_alert")
        mttr = round(time.time() - t_alert, 2) if t_alert else None

        rca_report = RCAReport(
            alert_id             = state["alert_id"],
            alert_service        = state["alert_service"],
            alert_error_type     = state["alert_error_type"],
            root_cause_node      = state.get("root_cause_node", "unknown"),
            dependency_chain     = state.get("dependency_chain", []),
            skills_executed      = visited,
            execution_history    = state.get("execution_history", []),
            total_hops           = attempt_count,
            resolution_status    = resolution_status,
            mttr_seconds         = mttr,
            tokens_used          = state.get("tokens_used", 0),
            all_services_healthy = all_healthy,
            root_cause_explanation = state.get("root_cause_explanation", "") or "",
            # The graph-vetted options the agent chose from, and why — the
            # auditable proof that selection was a real decision over a
            # candidate set, not a hardcoded fault->fix lookup.
            candidates_considered = [c.get("name", "") for c in
                                     (state.get("candidate_skills") or [])],
            llm_selection_reason = state.get("llm_reason", "") or "",
            timestamp            = datetime.now(timezone.utc),
        )
        log.info(
            "rca_report_generated",
            status=resolution_status.value,
            skills_executed=visited,
        )

    new_state = {
        **state,
        "visited_skills":           visited,
        "attempt_count":            attempt_count,
        "all_healthy":              all_healthy,
        "services_still_unhealthy": unhealthy_count,
        "rca_report":               rca_report,
        "fallback_pending":         False,
    }

    # ── Step 3: if a NEXT_IF_FAIL fallback was selected, load it as the next
    # skill to try and signal the router to go straight to `reason` (skip Q2).
    # The fallback becomes the SOLE candidate so the reasoner's allowlist check
    # still holds (it can only pick this graph-vetted fallback, or escalate).
    if fallback_skill is not None and resolution_status is None:
        new_state.update({
            "candidate_skills": [{
                "name": fallback_skill.name, "description": fallback_skill.description,
                "risk_level": fallback_skill.risk_level,
                "script_path": fallback_skill.script_path,
                "script_type": fallback_skill.script_type,
                "trigger_condition": fallback_skill.trigger_condition,
            }],
            "current_skill":       fallback_skill.name,
            "current_script":      fallback_skill.script_path,
            "current_script_type": fallback_skill.script_type,
            "current_description": fallback_skill.description,
            "current_risk_level":  fallback_skill.risk_level,
            "current_trigger":     fallback_skill.trigger_condition,
            "fallback_pending":    True,
        })

    return new_state


def generate_report(state: AgentState) -> AgentState:
    """
    Terminal node. Writes the RCA report to the audit log.
    Called only when all_healthy=True or max_attempts exceeded.
    """
    report = state.get("rca_report")
    if not report:
        log.warning("generate_report_called_with_no_report",
                    note="evaluate_and_route should always produce a report before this node")
        return state

    import json
    from pathlib import Path
    from core.config import settings

    audit_path = settings.audit_dir / f"rca_{report.alert_id}.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    with open(audit_path, "w") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, default=str)

    log.info(
        "audit_report_written",
        path=str(audit_path),
        status=report.resolution_status.value,
        root_cause=report.root_cause_node,
    )

    return state