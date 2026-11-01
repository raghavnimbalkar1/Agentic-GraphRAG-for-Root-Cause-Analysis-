"""Passive current-state review. Never invokes a model, SOP, canary, probe container or cleanup."""

from datetime import datetime, timedelta
import re
from time import monotonic

from agent.evidence_policy import screening_contract
from agent.procedure_execution import evidence_digest, utc_now
from agent.shadow import ANCHORS, DependencySnapshot
from core.operation_journal import AttemptIdentity, JournalDenied, OperationJournal
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import SymptomObservation

QUIET_SECONDS = 120
DEADLINE_SECONDS = 15


class ReconciliationStopped(ValueError):
    """Fixed evidence category; do not retain SDK, database or application text."""


def _basis(snapshot):
    identity = AttemptIdentity.model_validate(snapshot["identity"])
    receipt = snapshot["receipt"]
    if (identity.environment_id != REGISTRY.environment_id or identity.registry_digest != REGISTRY.digest
            or receipt.get("execution_policy") != "operator-controlled-v2"):
        raise ReconciliationStopped("unsupported_reconciliation_provenance")
    basis = receipt.get("reconciliation_basis") or {}
    if type(basis.get("schema_version")) is not int or basis["schema_version"] != 1 or basis.get("source_kind") != "retained-observation":
        raise ReconciliationStopped("missing_retained_reconciliation_basis")
    context = basis["context"]
    topology = DependencySnapshot.model_validate(context["topology"])
    symptom = SymptomObservation.model_validate(context["symptom"])
    if (topology.source != "neo4j-export" or context["workflow_anchors"] != ANCHORS
            or context["registered_services"] != sorted(REGISTRY.by_name)
            or symptom.outcome != "failed" or symptom.quality != "observed"
            or symptom.environment_id != REGISTRY.environment_id or symptom.registry_digest != REGISTRY.digest
            or symptom.registry_revision != REGISTRY.revision):
        raise ReconciliationStopped("invalid_reconciliation_context")
    scope = screening_contract(context)["required_scope"]
    instances, prior_ids = basis["instances"], basis["component_ids"]
    if (basis["scope"] != sorted(scope) or set(instances) != set(scope) or set(prior_ids) != set(scope)
            or identity.target not in scope or instances[identity.target] != identity.instance_id
            or len(set(instances.values())) != len(instances)
            or any(re.fullmatch(r"[a-f0-9]{64}", value) is None for value in instances.values())
            or any(not isinstance(ids, list) or not ids or len(ids) > 100
                   or any(re.fullmatch(r"OBS-[a-f0-9]{32}", value) is None for value in ids) for ids in prior_ids.values())):
        raise ReconciliationStopped("invalid_reconciliation_scope")
    old_ids = {identifier for ids in prior_ids.values() for identifier in ids}
    old_ids.update(row["evidence_id"] for row in receipt.get("preflight", {}).get("evidence", []))
    return identity, {**basis, "prior_evidence_ids": sorted(old_ids)}


def _docker_client():
    from agent.tools.bound_executor import _docker_client as create

    return create()


def _graph_client():
    from graph.graph_client import GraphClient

    return GraphClient()


def _runtime(client, instances, *, deadline=None):
    rows = {}
    for name, identifier in sorted(instances.items()):
        if deadline is not None and monotonic() >= deadline:
            raise ReconciliationStopped("reconciliation_deadline_exhausted")
        target = client.containers.get(identifier)
        target.reload()
        attrs = target.attrs
        state = attrs.get("State", {})
        endpoint = attrs.get("NetworkSettings", {}).get("Networks", {}).get(REGISTRY.network)
        if (target.id != identifier or target.name != REGISTRY.by_name[name].container_name
                or attrs.get("Config", {}).get("Labels", {}).get(REGISTRY.ownership_label) != REGISTRY.ownership_value
                or state.get("Running") is not True or state.get("Paused") is not False or state.get("Restarting") is not False
                or not isinstance(endpoint, dict) or not endpoint.get("IPAddress")
                or re.fullmatch(r"[a-f0-9]{64}", endpoint.get("NetworkID", "")) is None):
            raise ReconciliationStopped("runtime_not_same_owned_running_instance")
        started = datetime.fromisoformat(state["StartedAt"])
        if started.tzinfo is None or started > utc_now():
            raise ReconciliationStopped("runtime_start_time_invalid")
        image, quota = attrs["Image"], attrs.get("HostConfig", {}).get("CpuQuota", 0)
        if not isinstance(image, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None or type(quota) is not int or quota < -1:
            raise ReconciliationStopped("runtime_inspection_incomplete")
        rows[name] = {"instance_id": identifier, "container_name": target.name, "started_at": started.isoformat(),
                      "network_id": endpoint["NetworkID"], "image_id": image, "cpu_quota": quota}
    return {"captured_at": utc_now().isoformat(), "instances": rows}


def _sandbox(client, receipt):
    from docker.errors import NotFound

    intent = receipt.get("sandbox_intent")
    if intent is None:
        return {"absent": True, "basis": "v2-no-request-create-admission"}
    if (set(intent) != {"name", "nonce", "image_id", "role"} or intent["role"] != "bound-sop-intent"
            or re.fullmatch(r"[a-f0-9]{32}", intent["nonce"]) is None
            or intent["name"] != f"sop-intent-{intent['nonce'][:12]}"
            or re.fullmatch(r"sha256:[a-f0-9]{64}", intent["image_id"]) is None):
        raise ReconciliationStopped("invalid_request_sandbox_identity")
    try:
        box = client.containers.get(intent["name"])
        box.reload()
    except NotFound:
        return {"absent": True, "basis": "exact-request-container-not-found"}
    labels = box.attrs.get("Config", {}).get("Labels", {})
    owned = (box.name == intent["name"] and box.attrs.get("Image") == intent["image_id"]
             and labels.get("rca.role") == intent["role"] and labels.get("rca.attempt") == intent["nonce"])
    # Observation only: do not remove even a positively identified orphan here.
    return {"absent": False, "basis": "owned-request-container-present" if owned else "request-container-identity-conflict"}


def _context(graph, identity, basis):
    from core.config import settings
    from core.telemetry import collector_status, fresh_service_statuses
    from eval.shadow_capture import _require_observe_only, declared_topology
    from graph.procedure_catalog import capture_procedure_catalog

    status = collector_status(settings.audit_dir, now=utc_now().timestamp())
    _require_observe_only(status)
    if status.get("sensing_ready") is not True or status.get("graph_synced") is not True:
        raise ReconciliationStopped("collector_not_fresh_observe_only")
    topology = declared_topology(graph.get_dependency_topology(), captured_at=utc_now())
    expected = {key: value for key, value in basis["context"]["topology"].items() if key != "captured_at"}
    if topology.model_dump(mode="json", exclude={"captured_at"}) != expected:
        raise ReconciliationStopped("reconciliation_topology_changed")
    catalog = capture_procedure_catalog(graph)
    contract = catalog.get(identity.skill_name)
    if (catalog.digest != identity.catalog_digest or contract.digest != identity.contract_digest
            or contract.policy.durability != basis["durability"]):
        raise ReconciliationStopped("reconciliation_catalog_changed")
    snapshot = graph.get_service_snapshot()
    healthy = fresh_service_statuses(snapshot, max_age=30)
    if any(healthy.get(name) != "HEALTHY" for name in basis["scope"]):
        raise ReconciliationStopped("reconciliation_graph_not_fresh_healthy")
    return {"checked_at": utc_now().isoformat(), "topology_revision": topology.revision,
            "catalog_digest": catalog.digest, "collector_observe_only": True,
            "graph": {name: {key: snapshot[name][key] for key in ("status", "age_seconds")} for name in basis["scope"]}}


def _retained(scope, cutoff):
    from core.config import settings
    from core.diagnostics import ComponentReader, DiagnosticRequest, TransactionReader

    path = settings.audit_dir / "evidence.sqlite"
    components = {name: ComponentReader(path).window(DiagnosticRequest(service_id=name, window_seconds=60),
                                                  now=cutoff.timestamp()) for name in scope}
    transactions = TransactionReader(path).window(DiagnosticRequest(service_id="frontend", window_seconds=300),
                                                 now=cutoff.timestamp())
    return {"components": components, "transactions": transactions, "read_at": cutoff.isoformat()}


def _assess(data, basis, floor, now, original_trigger):
    if set(data["components"]) != set(basis["scope"]):
        raise ReconciliationStopped("retained_scope_incomplete")
    identifiers, selected = set(), {}
    for name, window in sorted(data["components"].items()):
        if (window["environment_id"] != REGISTRY.environment_id or window["registry_digest"] != REGISTRY.digest
                or window["registry_revision"] != REGISTRY.revision or window["service_id"] != name
                or window["source"] != "builtin-health-probe" or window["interpretation_version"] != "health-probes-v1"
                or window["mode"] != "retained-evidence-only" or window["read_at"] != data["read_at"]
                or window["truncated"] or window["summary"]["latest_conflict"] or not window["summary"]["fresh_now"]):
            raise ReconciliationStopped("retained_component_window_unreliable")
        recent = [row for row in window["samples"] if datetime.fromisoformat(row["measured_at"]) >= max(floor, now - timedelta(seconds=30))]
        times = set()
        for row in recent:
            measured, collected = datetime.fromisoformat(row["measured_at"]), datetime.fromisoformat(row["collected_at"])
            if (row["status"] != "HEALTHY" or row["quality"] != "observed" or row["instance_id"] != basis["instances"][name]
                    or not floor <= measured <= collected <= now or (now - measured).total_seconds() > 30
                    or re.fullmatch(r"OBS-[a-f0-9]{32}", row["evidence_id"]) is None
                    or row["evidence_id"] in identifiers or row["evidence_id"] in basis["prior_evidence_ids"]):
                raise ReconciliationStopped("retained_component_not_fresh_healthy_identified")
            identifiers.add(row["evidence_id"])
            times.add(measured)
        if len(times) < 2 or (max(times) - min(times)).total_seconds() < 1:
            raise ReconciliationStopped("two_distinct_healthy_component_measurements_required")
        pair = {max(times), max(stamp for stamp in times if (max(times) - stamp).total_seconds() >= 1)}
        selected[name] = [row for row in recent if datetime.fromisoformat(row["measured_at"]) in pair]
    history = data["transactions"]
    if (history["truncated"] or history["environment_id"] != REGISTRY.environment_id
            or history["registry_digest"] != REGISTRY.digest or history["registry_revision"] != REGISTRY.revision
            or history["mode"] != "retained-evidence-only" or history["service_id"] != "frontend"
            or history["read_at"] != data["read_at"]):
        raise ReconciliationStopped("transaction_window_truncated")
    rows = [SymptomObservation.model_validate(row) for row in history["observations"]]
    recent = [row for row in rows if row.measured_at >= max(floor, now - timedelta(seconds=90))]
    if len(recent) < 2 or len({row.evidence_id for row in recent}) != len(recent):
        raise ReconciliationStopped("two_fresh_retained_checkout_transactions_required")
    for row in recent:
        if (row.outcome != "healthy" or row.quality != "observed" or row.environment_id != REGISTRY.environment_id
                or re.fullmatch(r"SYMOBS-[a-f0-9]{32}", row.evidence_id) is None
                or row.evidence_id in {original_trigger, basis["context"]["symptom"]["evidence_id"]}
                or row.registry_digest != REGISTRY.digest or row.registry_revision != REGISTRY.revision
                or not floor <= row.started_at <= row.measured_at <= row.collected_at <= now
                or (now - row.measured_at).total_seconds() > 90
                or any(step.marker_found is not True for step in row.steps if step.step in {"add_cart", "read_cart", "checkout"})):
            raise ReconciliationStopped("recent_checkout_not_fresh_healthy")
    if (len({row.started_at for row in recent}) < 2 or len({row.measured_at for row in recent}) < 2
            or (max(row.started_at for row in recent) - min(row.started_at for row in recent)).total_seconds() < 1):
        raise ReconciliationStopped("distinct_checkout_transactions_required")
    times = sorted({row.measured_at for row in recent})[-2:]
    latest = [min((row for row in recent if row.measured_at == stamp), key=lambda row: row.evidence_id) for stamp in times]
    if (latest[-1].started_at - latest[0].started_at).total_seconds() < 1:
        raise ReconciliationStopped("distinct_checkout_transactions_required")
    return {"components": selected, "transactions": [row.model_dump(mode="json") for row in latest]}


def _valid_until(report):
    times = [datetime.fromisoformat(row[key]) + timedelta(seconds=5) for group, key in
             (("runtime_inspections", "captured_at"), ("context_checks", "checked_at")) for row in report[group]]
    for facts in report["evidence"]:
        times += [datetime.fromisoformat(row["measured_at"]) + timedelta(seconds=30)
                  for rows in facts["components"].values() for row in rows]
        times += [datetime.fromisoformat(row["measured_at"]) + timedelta(seconds=90) for row in facts["transactions"]]
    return min(times)


def reconcile_attempt(journal, identifier, *, expected_version, approve_runtime_inspection=False,
                      approve_current_state_closure=False):
    """One passive assessment, optionally followed by explicit current-state closure.

    This does not prove the interrupted effect occurred, verify a recovery window,
    retry a mutation, mint a grant or clear an unresolved orphan/mitigation.
    """
    if (type(journal) is not OperationJournal or approve_runtime_inspection is not True
            or type(approve_current_state_closure) is not bool):
        return {"outcome": "rejected", "reason": "explicit_runtime_inspection_required", "execution_authorized": False}
    started, start_clock = utc_now(), monotonic()
    try:
        lease = journal.begin_reconciliation(identifier, expected_version=expected_version, approve_inspection=True)
    except JournalDenied as error:
        return {"outcome": "rejected", "reason": str(error), "execution_authorized": False}
    report = {"policy": "passive-current-state-v1", "outcome": "inconclusive", "reason": "reconciliation_unavailable",
        "started_at": started.isoformat(), "checked_at": started.isoformat(), "quiet_seconds_required": QUIET_SECONDS,
        "execution_authorized": False, "automatic_retry_allowed": False, "recovery_verified": False,
        "causal_attribution": False, "prior_effect_proven": False, "late_external_effect_excluded": False,
        "automatic_continuation_allowed": False, "new_application_probes": 0, "real_model_calls": 0,
        "scope": [], "evidence": [], "runtime_inspections": [], "context_checks": [], "sandbox_checks": []}
    client, graph, persisted = None, None, None

    def tick():
        now, elapsed = utc_now(), monotonic() - start_clock
        if elapsed < 0 or now < started or abs((now - started).total_seconds() - elapsed) > 5:
            raise ReconciliationStopped("reconciliation_clock_discontinuity")
        if elapsed >= DEADLINE_SECONDS:
            raise ReconciliationStopped("reconciliation_deadline_exhausted")
        return now

    try:
        snapshot = lease.snapshot
        identity, basis = _basis(snapshot)
        report["scope"] = basis["scope"]
        floor = datetime.fromisoformat(snapshot["last_runtime_recorded_at"]) + timedelta(seconds=QUIET_SECONDS)
        if tick() < floor:
            raise ReconciliationStopped("quiet_interval_not_elapsed")
        if basis["durability"] == "temporary_containment":
            raise ReconciliationStopped("temporary_mitigation_requires_separate_review")
        client = _docker_client()
        tick()
        graph = _graph_client()
        tick()
        for _ in range(2):
            tick()
            report["sandbox_checks"].append(_sandbox(client, snapshot["receipt"]))
            if report["sandbox_checks"][-1]["absent"] is not True:
                raise ReconciliationStopped("request_sandbox_present_or_identity_conflict")
            report["runtime_inspections"].append(_runtime(client, basis["instances"], deadline=start_clock + DEADLINE_SECONDS))
            tick()
            report["context_checks"].append(_context(graph, identity, basis))
            data = _retained(basis["scope"], tick())
            facts = _assess(data, basis, floor, tick(), identity.trigger_evidence_id)
            report["evidence"].append(facts)
            first_transaction = min(datetime.fromisoformat(row["started_at"]) for row in facts["transactions"])
            if any(datetime.fromisoformat(row["started_at"]) > first_transaction
                   for row in report["runtime_inspections"][-1]["instances"].values()):
                raise ReconciliationStopped("runtime_changed_after_healthy_transactions")
        if evidence_digest(report["runtime_inspections"][0]["instances"]) != evidence_digest(report["runtime_inspections"][1]["instances"]):
            raise ReconciliationStopped("runtime_changed_during_reconciliation")
        now = tick()
        until = _valid_until(report)
        if now >= until:
            raise ReconciliationStopped("reconciliation_context_expired")
        report.update(outcome="observed_healthy", reason="fresh_scoped_current_state_observed", checked_at=now.isoformat(),
                      valid_until=until.isoformat())
    except ReconciliationStopped as error:
        report["reason"] = str(error)
    except Exception:
        report["reason"] = "reconciliation_evidence_or_runtime_unavailable"
    except BaseException:
        report["reason"] = "reconciliation_interrupted"
        raise
    finally:
        for resource in (graph, client):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    report.update(outcome="inconclusive", reason="reconciliation_client_close_unconfirmed")
        try:
            now = tick()
            if report["outcome"] == "observed_healthy" and now >= _valid_until(report):
                raise ReconciliationStopped("reconciliation_context_expired")
        except ReconciliationStopped as error:
            report.update(outcome="inconclusive", reason=str(error))
        report.update(finished_at=utc_now().isoformat(), elapsed_seconds=max(0, monotonic() - start_clock))
        try:
            persisted = lease.finish(report, approve_current_state_closure=approve_current_state_closure)
        except JournalDenied:
            report.update(outcome="inconclusive", reason="reconciliation_persistence_unconfirmed")
        finally:
            try:
                lease.close()
            except Exception:
                report.update(outcome="inconclusive", reason="reconciliation_owner_close_unconfirmed")
                persisted = None
    return {**report, "journal": persisted, "current_state_closed": persisted["current_state_closed"] if persisted
            else None if approve_current_state_closure else False}
