"""Procedure intent controls, not live repair, causal accuracy or durable grants."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from core.procedure_contracts import (
    BoundProcedurePlan, NETWORK_SCRIPTS, OPERATION_TARGETS, PreflightPermissions,
    RuntimeIdentity, SCRIPT_OPERATIONS, bind_plan, check_preflight, compile_catalog, operation_policy,
    require_bundled_layout,
)
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord
from graph.procedure_catalog import capture_procedure_catalog
from graph.scripts.audit_procedures import _write_new, audit

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc) - timedelta(seconds=2)
REVISION = "1" * 64
INSTANCE = "a" * 64
SCRIPTS = tuple(sorted(NETWORK_SCRIPTS | SCRIPT_OPERATIONS.keys()))


def definition(relative="container/restart.sh", *, name="Payment_Restart", **overrides):
    policy = operation_policy(relative)
    target, condition = policy.bindings[0]
    return {"name": name, "script_path": f"/sops/{relative}", "script_type": "bash",
        "description": "Reviewed graph procedure", "params": [], "timeout_seconds": 30,
        "risk_level": policy.catalog_risk, "trigger_condition": condition, "applies_to": [target],
        "next_if_fail": [], **overrides}


def catalog(relative="container/restart.sh", **kwargs):
    return compile_catalog([definition(relative, **kwargs)], project_root=ROOT)


def observation(contract, *, now=NOW, **overrides):
    measurements = {"HIGH_CPU": {"cpu_percent": 95}, "DISK_PRESSURE": {"bytes": 110000000},
        "MEMORY_LEAK": {"bytes": 400000000}, "STALE_DATA": {"expires": 200},
        "POOL_EXHAUSTION": {"clients": 80}, "CONFIG_DRIFT": {"policy": "noeviction"},
        "OOM_KILLED": {"maxmemory": 1024}}.get(contract.trigger_condition, {})
    data = dict(schema_version=2, evidence_id="OBS-" + "b" * 32, service_id=contract.applies_to[0],
        instance_id=INSTANCE, measured_at=now - timedelta(seconds=1), collected_at=now,
        status=contract.trigger_condition, detail="Built-in probe", measurements=measurements,
        quality="observed", environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
        registry_digest=REGISTRY.digest)
    return ObservationRecord.model_validate({**data, **overrides})


def prepared(relative="container/restart.sh", **kwargs):
    cap = catalog(relative, **kwargs)
    item = observation(cap.contracts[0])
    plan = bind_plan(cap, skill_name=cap.contracts[0].name, observation=item,
                     incident_id="INC-CONTROL", revision=REVISION, now=NOW)
    runtime = RuntimeIdentity(service_id=item.service_id,
        container_name=REGISTRY.by_name[item.service_id].container_name, instance_id=INSTANCE,
        ownership_label=REGISTRY.ownership_label, ownership_value=REGISTRY.ownership_value,
        captured_at=NOW, cpu_quota=10000)
    return cap, item, plan, runtime


def preflight(cap, item, plan, runtime, **kwargs):
    args = dict(catalog=cap, observation=item, incident_id="INC-CONTROL", revision=REVISION,
                now=NOW, runtime=runtime, permissions=PreflightPermissions(**{cap.contracts[0].policy.permission: True}))
    return check_preflight(plan, **{**args, **kwargs})


@pytest.mark.parametrize("relative", SCRIPTS)
def test_all_eight_operations_have_versioned_graph_bound_implementation_and_explicit_effects(relative):
    cap, item, plan, runtime = prepared(relative)
    contract = cap.contracts[0]
    assert len(contract.digest) == len(cap.digest) == 64
    assert contract.registry_digest == REGISTRY.digest
    assert contract.policy.effects and contract.policy.preconditions and contract.policy.postconditions
    assert contract.policy.repeat_policy == "reconcile_before_retry"
    assert {row.path for row in contract.implementation} >= {f"sops/{relative}", "core/procedure_contracts.py"}
    assert preflight(cap, item, plan, runtime) == {"eligible": True, "reason": "eligible", "execution_authorized": False}
    assert not plan.execution_authorized


@pytest.mark.parametrize("relative", SCRIPTS)
def test_every_operation_requires_explicit_effect_permission(relative):
    cap, item, plan, runtime = prepared(relative)
    assert preflight(cap, item, plan, runtime, permissions=PreflightPermissions())["reason"] == "operator_permission_required"


def test_flush_is_data_loss_despite_legacy_low_catalog_label():
    cap, item, plan, runtime = prepared("redis/cache_flush.sh")
    contract = cap.contracts[0]
    assert contract.risk_level == "LOW" and contract.policy.effect_risk == "HIGH"
    assert "all_databases_key_deletion" in contract.policy.effects
    assert preflight(cap, item, plan, runtime, permissions=PreflightPermissions(disruption=True))["eligible"] is False


def test_cpu_containment_does_not_claim_durable_repair():
    contract = catalog("adservice/throttle.sh").contracts[0]
    assert contract.policy.durability == "temporary_containment"
    assert dict(contract.policy.fixed_parameters) == {"cpu_period": "100000", "cpu_quota": "50000"}
    assert "durability_not_established_by_exit_code" in contract.policy.postconditions


@pytest.mark.parametrize("target,condition", operation_policy("container/restart.sh").bindings)
def test_every_reviewed_restart_target_condition_binding_is_checked(target, condition):
    cap, item, plan, runtime = prepared(applies_to=[target], trigger_condition=condition)
    assert preflight(cap, item, plan, runtime)["eligible"]


@pytest.mark.parametrize("permission,expected", [
    ("resource_change", "Throttle"), ("disruption", "Restart"),
])
def test_same_condition_choices_change_with_explicit_disruption_resource_constraints(permission, expected):
    cap = compile_catalog([
        definition("adservice/throttle.sh", name="Throttle"),
        definition(name="Restart", applies_to=["adservice"], trigger_condition="HIGH_CPU"),
    ], project_root=ROOT)
    outcomes = {}
    for contract in cap.contracts:
        item = observation(contract)
        plan = bind_plan(cap, skill_name=contract.name, observation=item,
                         incident_id="INC-CONTROL", revision=REVISION, now=NOW)
        runtime = RuntimeIdentity(service_id="adservice", container_name="adservice", instance_id=INSTANCE,
            ownership_label=REGISTRY.ownership_label, ownership_value=REGISTRY.ownership_value,
            captured_at=NOW, cpu_quota=0)
        outcomes[contract.name] = check_preflight(plan, catalog=cap, observation=item, runtime=runtime,
            incident_id="INC-CONTROL", revision=REVISION, now=NOW,
            permissions=PreflightPermissions(**{permission: True}))["eligible"]
    assert [name for name, allowed in outcomes.items() if allowed] == [expected]


@pytest.mark.parametrize("changes", [
    {"script_path": "/sops/../core/config.py"}, {"script_path": "/etc/passwd"},
    {"script_path": "/sops/unknown.sh"}, {"script_type": "python"}, {"risk_level": "LOW"},
    {"trigger_condition": "CONFIG_DRIFT"}, {"applies_to": ["redis-cart"]},
    {"applies_to": []}, {"applies_to": ["cartservice", "cartservice"]},
    {"requires_confirmation": False}, {"timeout_seconds": 121}, {"timeout_seconds": True},
    {"timeout_seconds": "30"}, {"name": "../../bad"},
])
def test_mutated_graph_skill_cannot_expand_operation_capabilities(changes):
    with pytest.raises(ValueError):
        catalog(**changes)


def test_catalog_is_order_invariant_but_metadata_and_applicability_are_bound():
    one = definition(name="One")
    two = definition("redis/config_reset.sh", name="Two")
    a = compile_catalog([one, two], project_root=ROOT)
    assert compile_catalog([two, one], project_root=ROOT).digest == a.digest
    for field, value in (("description", "Changed"), ("timeout_seconds", 31), ("params", ["TARGET_CONTAINER"])):
        changed = {**one, field: value}
        assert compile_catalog([changed, two], project_root=ROOT).digest != a.digest
    with pytest.raises(ValueError):
        compile_catalog([one, one], project_root=ROOT)
    with pytest.raises(ValueError):
        compile_catalog([], project_root=ROOT)


def test_same_target_reviewed_redis_fallback_is_recorded_but_does_not_authorize_trigger_substitution():
    restart = definition("redis/restart.sh", name="Restart", next_if_fail=["Flush"])
    flush = definition("redis/cache_flush.sh", name="Flush")
    cap = compile_catalog([restart, flush], project_root=ROOT)
    assert cap.fallbacks == (("Restart", "Flush"),)
    with pytest.raises(ValueError):
        bind_plan(cap, skill_name="Flush", observation=observation(cap.get("Restart")),
                  incident_id="INC-EXAMPLE", revision=REVISION, now=NOW)


@pytest.mark.parametrize("next_skills", [["Missing"], ["Restart"], ["Flush", "Flush"], [42]])
def test_unresolved_cyclic_duplicate_or_untyped_fallbacks_are_rejected(next_skills):
    rows = [definition("redis/restart.sh", name="Restart", next_if_fail=next_skills),
            definition("redis/cache_flush.sh", name="Flush")]
    with pytest.raises(ValueError):
        compile_catalog(rows, project_root=ROOT)


def test_cross_target_fallback_is_rejected():
    with pytest.raises(ValueError):
        compile_catalog([definition(name="Restart", next_if_fail=["Flush"]),
            definition("redis/cache_flush.sh", name="Flush")], project_root=ROOT)


@pytest.mark.parametrize("change", [
    {"schema_version": 1}, {"registry_digest": "f" * 64}, {"registry_revision": "other"},
    {"environment_id": "other"}, {"instance_id": None}, {"instance_id": "short"},
    {"evidence_id": "unidentified"}, {"status": "UNKNOWN", "quality": "unknown"},
    {"status": "HEALTHY"}, {"status": "DEGRADED"},
    {"measured_at": NOW - timedelta(seconds=30)}, {"collected_at": NOW + timedelta(seconds=1)},
])
def test_plan_binding_requires_current_identified_matching_evidence(change):
    cap = catalog()
    with pytest.raises(ValueError):
        bind_plan(cap, skill_name=cap.contracts[0].name, observation=observation(cap.contracts[0], **change),
                  incident_id="INC-CONTROL", revision=REVISION, now=NOW)


@pytest.mark.parametrize("field,value", [("incident_id", "INC-OTHER"), ("revision", "2" * 64)])
def test_different_incident_or_plan_revision_is_not_reusable(field, value):
    cap, item, plan, runtime = prepared()
    assert preflight(cap, item, plan, runtime, **{field: value})["reason"] == "plan_superseded"


def test_graph_metadata_edit_expires_a_pending_plan():
    cap, item, plan, runtime = prepared()
    changed = catalog(description="Changed after planning")
    assert preflight(cap, item, plan, runtime, catalog=changed)["reason"] == "catalog_changed"


def test_local_script_change_is_bound_and_requires_replanning(tmp_path):
    for source in catalog().contracts[0].implementation:
        path = tmp_path / source.path
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source.path, path)
    before = compile_catalog([definition()], project_root=tmp_path)
    (tmp_path / "sops/container/restart.sh").write_text("#!/bin/bash\nexit 1\n")
    after = compile_catalog([definition()], project_root=tmp_path)
    assert before.digest != after.digest and before.contracts[0].digest != after.contracts[0].digest


@pytest.mark.parametrize("mode", ["missing", "symlink", "oversized"])
def test_unsafe_or_missing_implementation_is_rejected(tmp_path, mode):
    path = tmp_path / "sops/container/restart.sh"
    path.parent.mkdir(parents=True)
    if mode == "symlink":
        path.symlink_to(ROOT / "sops/container/restart.sh")
    elif mode == "oversized":
        path.write_bytes(b"x" * 131073)
    with pytest.raises(ValueError):
        compile_catalog([definition()], project_root=tmp_path)


@pytest.mark.parametrize("changes", [
    {"instance_id": "c" * 64}, {"ownership_label": "other"}, {"ownership_value": "other"},
    {"service_id": "paymentservice"}, {"container_name": "other"},
    {"captured_at": NOW - timedelta(seconds=5)}, {"captured_at": NOW + timedelta(seconds=1)},
])
def test_replaced_unowned_stale_or_wrong_runtime_is_denied(changes):
    cap, item, plan, runtime = prepared()
    changed = RuntimeIdentity.model_validate({**runtime.model_dump(), **changes})
    assert preflight(cap, item, plan, changed)["reason"] == "identity_or_ownership_changed"


def test_changed_condition_or_container_cannot_reuse_plan():
    cap, item, plan, runtime = prepared()
    changed = item.model_copy(update={"status": "DEGRADED"})
    assert preflight(cap, changed, plan, runtime)["reason"] == "condition_changed"
    changed = item.model_copy(update={"instance_id": "c" * 64})
    assert preflight(cap, changed, plan, runtime)["reason"] == "identity_or_ownership_changed"


def test_evidence_identity_cannot_be_rewritten_even_with_same_condition():
    cap, item, plan, runtime = prepared()
    rewritten = item.model_copy(update={"detail": "Rewritten payload"})
    assert preflight(cap, rewritten, plan, runtime)["reason"] == "evidence_identity_rewritten"


def test_fresh_new_observation_is_accepted_without_reusing_old_payload():
    cap, item, plan, runtime = prepared()
    fresh = item.model_copy(update={"evidence_id": "OBS-" + "c" * 32, "measured_at": NOW})
    assert preflight(cap, fresh, plan, runtime)["eligible"]


@pytest.mark.parametrize("value", [None, -1, 0, 79.9, True, float("nan"), float("inf"), "95"])
def test_cpu_label_without_matching_numeric_evidence_is_not_enough(value):
    cap, item, plan, runtime = prepared("adservice/throttle.sh")
    changed = item.model_copy(update={"evidence_id": "OBS-" + "c" * 32, "measurements": {"cpu_percent": value}})
    assert not preflight(cap, changed, plan, runtime)["eligible"]


@pytest.mark.parametrize("quota", [-1, 0])
def test_frontend_timeout_without_actual_cpu_quota_does_not_justify_quota_removal(quota):
    cap, item, plan, runtime = prepared("frontend/restore_cpu.sh")
    assert preflight(cap, item, plan, runtime.model_copy(update={"cpu_quota": quota}))["reason"] == "procedure_precondition_missing"


@pytest.mark.parametrize("relative,key,value", [
    ("redis/restart.sh", "maxmemory", 0), ("redis/restart.sh", "maxmemory", True),
    ("redis/restart.sh", "maxmemory", 10485761), ("redis/pool_reset.sh", "clients", 50),
    ("redis/cache_flush.sh", "expires", 199), ("redis/config_reset.sh", "policy", "allkeys-lru"),
    ("email/disk_cleanup.sh", "bytes", 104857599),
])
def test_other_resource_conditions_need_actual_supporting_measurements(relative, key, value):
    cap, item, plan, runtime = prepared(relative)
    changed = item.model_copy(update={"evidence_id": "OBS-" + "c" * 32, "measurements": {key: value}})
    assert preflight(cap, changed, plan, runtime)["reason"] == "procedure_precondition_missing"


def test_runtime_freshness_is_checked_independently_of_plan_freshness():
    cap, item, plan, runtime = prepared()
    assert preflight(cap, item, plan, runtime, now=NOW + timedelta(seconds=5))["reason"] == "identity_or_ownership_changed"


def test_older_probe_than_plan_evidence_is_not_a_new_preflight():
    cap, item, plan, runtime = prepared()
    stale = item.model_copy(update={"evidence_id": "OBS-" + "c" * 32, "measured_at": NOW - timedelta(seconds=2)})
    assert preflight(cap, stale, plan, runtime)["reason"] == "condition_changed"


def test_expired_forged_or_execution_authorizing_plan_is_rejected():
    cap, item, plan, runtime = prepared()
    assert preflight(cap, item, plan, runtime, now=NOW + timedelta(seconds=29))["reason"] == "plan_expired"
    forged = plan.model_copy(update={"execution_authorized": True})
    assert preflight(cap, item, forged, runtime)["reason"] == "invalid_or_unavailable_preflight"
    with pytest.raises(ValidationError):
        BoundProcedurePlan.model_validate({**plan.model_dump(), "command": "PRIVATE"})
    with pytest.raises(ValidationError):
        PreflightPermissions(disruption="true")


def test_existing_executor_mappings_remain_immutable_and_unchanged():
    assert len(SCRIPT_OPERATIONS) == 5 and len(NETWORK_SCRIPTS) == 3
    assert "paymentservice" in OPERATION_TARGETS["restart"]
    assert "redis-cart" not in OPERATION_TARGETS["restart"]
    with pytest.raises(TypeError):
        SCRIPT_OPERATIONS["evil.sh"] = "restart"
    with pytest.raises(AttributeError):
        OPERATION_TARGETS["restart"].add("unregistered")


def test_unfingerprinted_custom_sop_layout_is_not_silently_accepted(tmp_path):
    require_bundled_layout(ROOT / "sops")
    with pytest.raises(ValueError):
        require_bundled_layout(tmp_path)


def test_graph_capture_is_single_read_without_discarding_unknown_fields():
    data = definition()
    target, fallback = data.pop("applies_to"), data.pop("next_if_fail")
    client = Mock()
    client._run.return_value = [{"skill": data, "applies_to": target, "next_if_fail": fallback}]
    assert capture_procedure_catalog(client).contracts[0].name == data["name"]
    client._run.assert_called_once()
    query = client._run.call_args.args[0]
    assert "SET " not in query and "DELETE " not in query
    client._run.return_value[0]["skill"]["unknown_property"] = "PRIVATE"
    with pytest.raises(ValueError):
        capture_procedure_catalog(client)


def test_graph_property_cannot_shadow_relationships():
    data = definition()
    client = Mock()
    client._run.return_value = [{"skill": data, "applies_to": ["cartservice"], "next_if_fail": []}]
    with pytest.raises(ValueError):
        capture_procedure_catalog(client)


def test_audit_records_descriptions_and_digests_without_claiming_execution(tmp_path):
    data = definition()
    target, fallback = data.pop("applies_to"), data.pop("next_if_fail")
    client = Mock()
    client._run.return_value = [{"skill": data, "applies_to": target, "next_if_fail": fallback}]
    result = audit(client)
    assert result["skills"] == result["bindings"] == 1
    assert result["real_model_calls"] == 0 and not result["execution_authorized"]
    assert not result["runtime_preconditions_checked"] and not result["durability_verified"]
    path = tmp_path / "audit.json"
    _write_new(path, result)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_new(path, {})
    assert json.loads(path.read_text()) == result


def test_data_only_contract_import_has_no_provider_docker_settings_or_graph_connection():
    program = "import core.procedure_contracts, sys; assert not any(n in sys.modules for n in ('core.config','docker','openai','graph.graph_client'))"
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_audit_cli_refuses_reuse_before_connecting(tmp_path):
    path = tmp_path / "out.json"
    path.write_text("KEEP")
    result = subprocess.run([sys.executable, "-m", "graph.scripts.audit_procedures", "--output", str(path)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and path.read_text() == "KEEP"
    assert not path.with_name("out.json.started.json").exists()
