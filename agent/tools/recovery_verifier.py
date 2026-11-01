"""One operator-approved recovery window; no model calls, repairs, retries or health overrides."""

from datetime import datetime
import math
from time import monotonic, sleep

from pydantic import Field, model_validator

from agent.procedure_execution import ExecutionDenied, ProcedureExecutionGrant, evidence_digest, utc_now
from core.procedure_contracts import FrozenModel
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation, TransactionStep


class RecoveryLimits(FrozenModel):
    settle_seconds: int = Field(default=60, strict=True, ge=0, le=120)
    soak_seconds: int = Field(default=120, strict=True, ge=120, le=300)
    poll_seconds: int = Field(default=5, strict=True, ge=1, le=10)
    transaction_seconds: int = Field(default=25, strict=True, ge=1, le=30)
    total_seconds: int = Field(default=300, strict=True, ge=120, le=600)
    max_rounds: int = Field(default=64, strict=True, ge=4, le=128)

    @model_validator(mode="after")
    def feasible(self):
        if self.total_seconds < self.settle_seconds + self.soak_seconds + 4 * (self.transaction_seconds + 3):
            raise ValueError("Recovery deadline cannot fit the declared phase and transaction allowances")
        if self.max_rounds < 4 + math.ceil((self.settle_seconds + self.soak_seconds) / self.poll_seconds):
            raise ValueError("Round allowance cannot fit the declared sampling schedule")
        return self


class RecoveryStopped(ValueError):
    """Fixed local evidence category, never raw SDK/application exceptions."""


def _docker_client():
    from agent.tools.bound_executor import _docker_client as create

    return create()


def _read_components(client, expected):
    from core.health import observe_many

    rows = observe_many(client, sorted(expected))
    for name, identifier in expected.items():
        container = client.containers.get(identifier)
        container.reload()
        labels = container.attrs.get("Config", {}).get("Labels", {})
        if (container.id != identifier or container.name != REGISTRY.by_name[name].container_name
                or labels.get(REGISTRY.ownership_label) != REGISTRY.ownership_value):
            raise RecoveryStopped("recovery_runtime_identity_changed")
    return rows


def _context(grant, graph, scope, *, final=False):
    from core.config import settings
    from core.telemetry import collector_status, fresh_service_statuses
    from eval.shadow_capture import _require_observe_only, declared_topology
    from graph.procedure_catalog import capture_procedure_catalog

    status = collector_status(settings.audit_dir)
    _require_observe_only(status)
    now = utc_now()
    topology = declared_topology(graph.get_dependency_topology(), captured_at=now)
    if topology.revision != grant.snapshot()["preflight"]["context_check"]["topology_revision"]:
        raise RecoveryStopped("recovery_topology_changed")
    if capture_procedure_catalog(graph).digest != grant.plan.catalog_digest:
        raise RecoveryStopped("recovery_catalog_changed")
    result = {"checked_at": now.isoformat(), "topology_revision": topology.revision,
              "catalog_digest": grant.plan.catalog_digest, "collector_observe_only": True}
    if final:
        snapshot = graph.get_service_snapshot()
        healthy = fresh_service_statuses(snapshot, max_age=30)
        elapsed = (now - datetime.fromisoformat(grant.snapshot()["effect_finished_at"])).total_seconds()
        if (status.get("sensing_ready") is not True or status.get("graph_synced") is not True
                or any(healthy.get(name) != "HEALTHY" or snapshot[name]["age_seconds"] > elapsed for name in scope)):
            raise RecoveryStopped("recovery_graph_not_fresh_healthy")
        result["graph"] = {name: {key: snapshot[name][key] for key in ("status", "age_seconds")}
                           for name in sorted(scope)}
    return result


def _transaction(seconds):
    from simulation.application_canary import check_application

    return check_application(deadline_seconds=seconds)


def _application_record(raw, started, completed):
    # Do not retain response bodies, unknown fields, credentials or probe exception text.
    if (not isinstance(raw, dict) or set(raw) != {"success", "steps"} or type(raw["success"]) is not bool
            or not isinstance(raw["steps"], list) or not 1 <= len(raw["steps"]) <= 5):
        raise RecoveryStopped("recovery_transaction_malformed")
    steps = [TransactionStep.model_validate(row) for row in raw["steps"]]
    from simulation.symptom_monitor import interpret_canary

    outcome, _ = interpret_canary(raw)
    if outcome == "healthy" and any(step.marker_found is not True for step in steps
                                    if step.step in {"add_cart", "read_cart", "checkout"}):
        raise RecoveryStopped("recovery_transaction_marker_missing")
    return SymptomObservation(environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
        registry_digest=REGISTRY.digest, started_at=started, measured_at=completed, collected_at=completed,
        outcome=outcome, quality="unknown" if outcome == "unknown" else "observed", steps=steps)


def verify_bound_recovery(grant, graph, *, approve_transaction_probes=False, limits=None):
    """Sample scoped components and four private checkouts around a >=120s soak.

    The deadline is cooperative between bounded probes, not hard cancellation of
    Docker or HTTP I/O. Expired/revoked diagnosis intent does not invalidate an
    already confirmed effect's read-only verification. No automatic fallback runs.
    """
    if type(grant) is not ProcedureExecutionGrant or approve_transaction_probes is not True:
        return {"outcome": "rejected", "reason": "owned_grant_and_explicit_transaction_approval_required"}
    if limits is None:
        limits = RecoveryLimits()
    if type(limits) is not RecoveryLimits:
        return {"outcome": "rejected", "reason": "typed_recovery_limits_required"}
    started, start_clock = utc_now(), monotonic()
    try:
        before = grant._begin_recovery(limits.model_dump())
    except ExecutionDenied as error:
        return {"outcome": "rejected", "reason": str(error)}
    report = {"outcome": "inconclusive", "reason": "recovery_verification_unavailable", "policy_version": "recovery-window-v1",
        "started_at": started.isoformat(), "policy": limits.model_dump(), "skill_name": grant.plan.skill_name,
        "target": grant.plan.target, "effect_durability": grant.contract.policy.durability,
        "scope": [], "component_rounds": [], "transactions": [], "context_checks": [],
        "initial_confirmation_at": None, "soak_observed_seconds": 0.0, "durable_repair_established": False}
    client, confirmed = None, False
    last_wall, last_clock = started, start_clock
    seen = {row["evidence_id"] for row in before["preflight"]["evidence"]}

    def tick():
        nonlocal last_wall, last_clock
        now, clock = utc_now(), monotonic()
        if now < last_wall or clock < last_clock or abs((now - started).total_seconds() - (clock - start_clock)) > 5:
            raise RecoveryStopped("recovery_clock_discontinuity")
        last_wall, last_clock = now, clock
        if clock - start_clock >= limits.total_seconds:
            raise RecoveryStopped("recovery_deadline_exhausted")
        return now, clock

    def pause():
        _, clock = tick()
        sleep(min(limits.poll_seconds, max(0, limits.total_seconds - (clock - start_clock))))
        tick()

    def components(phase):
        now, _ = tick()
        if len(report["component_rounds"]) >= limits.max_rounds:
            raise RecoveryStopped("recovery_round_budget_exhausted")
        row = {"phase": phase, "started_at": now.isoformat(), "evidence": []}
        report["component_rounds"].append(row)
        readings = _read_components(client, expected)
        end, _ = tick()
        if set(readings) != set(expected):
            raise RecoveryStopped("recovery_scope_incomplete")
        for name, reading in sorted(readings.items()):
            evidence_digest(reading.measurements)  # JSON-mode model dumping can turn NaN into null.
            item = ObservationRecord(schema_version=2, service_id=name, instance_id=reading.instance_id,
                measured_at=reading.measured_at, collected_at=end, status=reading.status,
                detail=f"Built-in recovery probe: {reading.status}", measurements=reading.measurements,
                quality="unknown" if reading.status == "UNKNOWN" else "observed",
                environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest)
            payload = item.model_dump(mode="json")
            evidence_digest(payload)  # Reject non-finite/unserializable probe values before retaining them.
            row["evidence"].append(payload)
            if (item.instance_id != expected[name] or not now <= item.measured_at <= end
                    or (end - item.measured_at).total_seconds() >= 30 or item.evidence_id in seen):
                raise RecoveryStopped("recovery_component_not_fresh_or_identified")
            seen.add(item.evidence_id)
            if item.quality != "observed":
                raise RecoveryStopped("recovery_component_unknown")
        check = _context(grant, graph, expected)
        end, _ = tick()
        if not 0 <= (end - datetime.fromisoformat(check["checked_at"])).total_seconds() < 5:
            raise RecoveryStopped("recovery_context_expired")
        report["context_checks"].append(check)
        healthy = all(item["status"] == "HEALTHY" for item in row["evidence"])
        row.update(healthy=healthy, completed_at=end.isoformat())
        return healthy

    def transaction(phase):
        now, clock = tick()
        if len(report["transactions"]) >= 4:
            raise RecoveryStopped("recovery_transaction_budget_exhausted")
        if limits.total_seconds - (clock - start_clock) < limits.transaction_seconds + 3:
            raise RecoveryStopped("recovery_transaction_deadline_unavailable")
        row = {"phase": phase, "started_at": now.isoformat(), "outcome": "unknown"}
        report["transactions"].append(row)  # Reserve before dispatch, including interrupted probes.
        raw = _transaction(limits.transaction_seconds)
        end, _ = tick()
        item = _application_record(raw, now, end)
        row.update(item.model_dump(mode="json"))
        if item.outcome != "healthy":
            if item.outcome == "unknown":
                raise RecoveryStopped("recovery_transaction_unknown")
            report["outcome"] = "recurrence_observed" if confirmed else "not_recovered"
            raise RecoveryStopped("recovery_transaction_failed")

    try:
        effect_end = datetime.fromisoformat(before["effect_finished_at"])
        if started < effect_end:
            raise RecoveryStopped("recovery_clock_precedes_effect")
        expected = {row["service_id"]: row["observation"]["instance_id"] for row in before["preflight"]["evidence"]}
        if not expected or set(expected) - set(REGISTRY.by_name) or any(not value for value in expected.values()):
            raise RecoveryStopped("recovery_preflight_scope_invalid")
        report["scope"] = sorted(expected)
        tick()
        client = _docker_client()
        consecutive = 0
        while consecutive < 2:
            healthy = components("settling")
            if healthy:
                transaction("initial_confirmation")
                consecutive += 1
            else:
                consecutive = 0
            if monotonic() - start_clock > limits.settle_seconds:
                report["outcome"] = "not_recovered"
                raise RecoveryStopped("recovery_settle_deadline_exhausted")
            if consecutive < 2:
                if monotonic() - start_clock >= limits.settle_seconds:
                    report["outcome"] = "not_recovered"
                    raise RecoveryStopped("recovery_settle_deadline_exhausted")
                pause()
        confirmed = True
        report["initial_confirmation_at"], soak_start = tick()
        report["initial_confirmation_at"] = report["initial_confirmation_at"].isoformat()
        while monotonic() - soak_start < limits.soak_seconds:
            pause()
            if not components("soak"):
                report["outcome"] = "recurrence_observed"
                raise RecoveryStopped("recovery_component_recurred")
            report["soak_observed_seconds"] = monotonic() - soak_start
        for index in range(2):
            if index:
                pause()
            if not components("final_confirmation"):
                report["outcome"] = "recurrence_observed"
                raise RecoveryStopped("recovery_component_recurred")
            transaction("final_confirmation")
        check = _context(grant, graph, expected, final=True)
        end, _ = tick()
        if not 0 <= (end - datetime.fromisoformat(check["checked_at"])).total_seconds() < 5:
            raise RecoveryStopped("recovery_context_expired")
        report["context_checks"].append(check)
        report.update(outcome="verified_mitigation" if grant.contract.policy.durability == "temporary_containment"
                      else "verified_recovery_window", reason="scoped_components_and_sampled_checkout_passed")
    except RecoveryStopped as error:
        report["reason"] = str(error)
    except Exception:
        report["reason"] = "recovery_probe_or_context_unavailable"
    except BaseException:
        report["reason"] = "recovery_verification_interrupted"
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                report.update(outcome="inconclusive", reason="recovery_client_close_unconfirmed")
        report.update(finished_at=utc_now().isoformat(), elapsed_seconds=max(0, monotonic() - start_clock))
        report.update(grant._finish_recovery(report))
    return report
