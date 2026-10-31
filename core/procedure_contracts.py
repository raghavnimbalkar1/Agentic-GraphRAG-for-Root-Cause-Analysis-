"""Versioned procedure descriptions and read-only preflight. Never grants execution."""

from datetime import timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SkillNode

POLICY_VERSION = "boutique-procedures-v1"
SCRIPT_OPERATIONS = MappingProxyType({
    "container/restart.sh": "restart", "redis/restart.sh": "restart_redis",
    "email/disk_cleanup.sh": "cleanup_disk", "adservice/throttle.sh": "throttle_cpu",
    "frontend/restore_cpu.sh": "restore_cpu",
})
OPERATION_TARGETS = MappingProxyType({
    "restart": frozenset({"emailservice", "productcatalogservice", "currencyservice", "paymentservice",
        "shippingservice", "adservice", "cartservice", "recommendationservice", "checkoutservice", "frontend"}),
    "restart_redis": frozenset({"redis-cart"}), "cleanup_disk": frozenset({"emailservice"}),
    "throttle_cpu": frozenset({"adservice"}), "restore_cpu": frozenset({"frontend"}),
})
NETWORK_SCRIPTS = frozenset({"redis/cache_flush.sh", "redis/config_reset.sh", "redis/pool_reset.sh"})


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require_bundled_layout(sops_dir):
    root = Path(__file__).resolve().parents[1]
    if Path(sops_dir).resolve() != (root / "sops").resolve():
        raise ValueError("Procedure contracts support only the bundled SOP layout")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OperationPolicy(FrozenModel):
    operation: Literal["restart", "restart_redis", "cleanup_disk", "throttle_cpu", "restore_cpu",
                       "redis_flush", "redis_pool_reset", "redis_config_reset"]
    bindings: tuple[tuple[str, str], ...]
    catalog_risk: Literal["LOW", "MEDIUM"]
    effect_risk: Literal["LOW", "MEDIUM", "HIGH"]
    effects: tuple[str, ...]
    fixed_parameters: tuple[tuple[str, str], ...] = ()
    durability: Literal["temporary_containment", "process_reset", "runtime_configuration", "targeted_cleanup"]
    permission: Literal["disruption", "resource_change", "runtime_configuration", "disposable_data", "fixed_cleanup"]
    compensation: Literal["operator_baseline_restore", "not_automatically_reversible"]
    repeat_policy: Literal["reconcile_before_retry"] = "reconcile_before_retry"
    preconditions: tuple[str, ...] = ("fresh_identified_condition", "unchanged_catalog_and_implementation",
        "same_owned_runtime_instance", "current_plan_revision", "explicit_effect_permission")
    postconditions: tuple[str, ...] = ("fresh_condition_absent_twice", "affected_transaction_passes_twice",
                                      "durability_not_established_by_exit_code")


_RESTART_BINDINGS = (
    ("cartservice", "CONNECTION_REFUSED"), ("paymentservice", "CONNECTION_REFUSED"),
    ("productcatalogservice", "CRASH_LOOPING"), ("checkoutservice", "DEGRADED"),
    ("frontend", "DEGRADED"), ("frontend", "CRASH_LOOPING"), ("currencyservice", "CRASH_LOOPING"),
    ("emailservice", "CRASH_LOOPING"), ("shippingservice", "CRASH_LOOPING"),
    ("recommendationservice", "CRASH_LOOPING"), ("recommendationservice", "MEMORY_LEAK"),
    ("adservice", "HIGH_CPU"),
)


def operation_policy(relative: str) -> OperationPolicy:
    """Local operation capabilities, not a second list of graph skill names."""
    if relative in {"container/restart.sh", "redis/restart.sh"}:
        redis = relative.startswith("redis/")
        return OperationPolicy(operation="restart_redis" if redis else "restart",
            bindings=(("redis-cart", "OOM_KILLED"),) if redis else _RESTART_BINDINGS,
            catalog_risk="MEDIUM", effect_risk="MEDIUM", effects=("process_restart", "inflight_work_loss", "network_reconnect"),
            durability="process_reset", permission="disruption", compensation="not_automatically_reversible")
    policies = {
        "adservice/throttle.sh": dict(operation="throttle_cpu", bindings=(("adservice", "HIGH_CPU"),),
            catalog_risk="MEDIUM", effect_risk="MEDIUM", effects=("cpu_quota_change",),
            fixed_parameters=(("cpu_period", "100000"), ("cpu_quota", "50000")),
            durability="temporary_containment", permission="resource_change", compensation="operator_baseline_restore"),
        "frontend/restore_cpu.sh": dict(operation="restore_cpu", bindings=(("frontend", "DEPENDENCY_TIMEOUT"),),
            catalog_risk="MEDIUM", effect_risk="MEDIUM", effects=("cpu_quota_change",),
            fixed_parameters=(("cpu_quota", "-1"),), durability="runtime_configuration",
            permission="resource_change", compensation="operator_baseline_restore"),
        "email/disk_cleanup.sh": dict(operation="cleanup_disk", bindings=(("emailservice", "DISK_PRESSURE"),),
            catalog_risk="MEDIUM", effect_risk="MEDIUM", effects=("fixed_file_deletion",),
            fixed_parameters=(("path", "/tmp/diskfill.bin"),), durability="targeted_cleanup",
            permission="fixed_cleanup", compensation="not_automatically_reversible"),
        "redis/cache_flush.sh": dict(operation="redis_flush", bindings=(("redis-cart", "STALE_DATA"),),
            catalog_risk="LOW", effect_risk="HIGH", effects=("all_databases_key_deletion", "runtime_configuration_change"),
            fixed_parameters=(("maxmemory", "268435456"), ("maxmemory-policy", "allkeys-lru")),
            durability="runtime_configuration", permission="disposable_data", compensation="not_automatically_reversible"),
        "redis/config_reset.sh": dict(operation="redis_config_reset", bindings=(("redis-cart", "CONFIG_DRIFT"),),
            catalog_risk="LOW", effect_risk="MEDIUM", effects=("runtime_configuration_change",),
            fixed_parameters=(("maxmemory-policy", "allkeys-lru"),), durability="runtime_configuration",
            permission="runtime_configuration", compensation="operator_baseline_restore"),
        "redis/pool_reset.sh": dict(operation="redis_pool_reset", bindings=(("redis-cart", "POOL_EXHAUSTION"),),
            catalog_risk="LOW", effect_risk="MEDIUM", effects=("normal_client_disconnection",),
            durability="temporary_containment", permission="disruption", compensation="not_automatically_reversible"),
    }
    if relative not in policies:
        raise ValueError("Unreviewed operation")
    return OperationPolicy(**policies[relative])


class SourceDigest(FrozenModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProcedureContract(FrozenModel):
    schema_version: Literal[1] = 1
    policy_version: Literal["boutique-procedures-v1"] = POLICY_VERSION
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,95}$")
    script_path: str
    script_type: Literal["bash"]
    description: str = Field(max_length=4096)
    params: tuple[str, ...]
    timeout_seconds: int = Field(strict=True, ge=1, le=120)
    risk_level: Literal["LOW", "MEDIUM"]
    trigger_condition: str
    applies_to: tuple[str, ...] = Field(min_length=1, max_length=1000)
    policy: OperationPolicy
    implementation: tuple[SourceDigest, ...]
    registry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @property
    def digest(self):
        return _digest(self.model_dump(mode="json"))


class ProcedureCatalog(FrozenModel):
    contracts: tuple[ProcedureContract, ...] = Field(min_length=1, max_length=128)
    fallbacks: tuple[tuple[str, str], ...] = ()

    @property
    def digest(self):
        return _digest(self.model_dump(mode="json"))

    def get(self, name):
        matches = [row for row in self.contracts if row.name == name]
        if len(matches) != 1:
            raise ValueError("Procedure is not uniquely registered")
        return matches[0]


def _sources(root: Path, relative: str) -> tuple[SourceDigest, ...]:
    paths = [f"sops/{relative}", "core/procedure_contracts.py", "core/health.py", "agent/tools/sandbox_tools.py"]
    if relative in SCRIPT_OPERATIONS:
        paths += ["sops/control_client.py", "agent/tools/control_gateway.py", "agent/procedure_execution.py",
                  "agent/tools/bound_executor.py", "eval/shadow_replan.py", "eval/shadow_capture.py",
                  "core/telemetry.py", "core/diagnostics.py", "graph/procedure_catalog.py", "graph/schema_definitions.py"]
    rows = []
    for name in sorted(paths):
        path = root / name
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
            raise ValueError("Implementation symlinks are unsupported")
        if not path.is_file() or path.stat().st_size > 131072:
            raise ValueError("Implementation is missing or oversized")
        rows.append(SourceDigest(path=name, sha256=sha256(path.read_bytes()).hexdigest()))
    return tuple(rows)


def compile_catalog(definitions: list[dict], *, project_root: Path) -> ProcedureCatalog:
    """Compile one captured graph result. Unknown fields/operations fail closed."""
    if not 1 <= len(definitions) <= 128:
        raise ValueError("Catalog scope is invalid")
    contracts, edges, names = [], [], set()
    for source in definitions:
        data = dict(source)
        next_skills = data.pop("next_if_fail", [])
        if set(data) - set(SkillNode.model_fields) or not isinstance(next_skills, (tuple, list)):
            raise ValueError("Unknown catalog fields")
        if type(data.get("timeout_seconds")) is not int:
            raise ValueError("Procedure timeout requires an explicit integer")
        skill = SkillNode.model_validate(data)
        if skill.name in names or not skill.applies_to or len(set(skill.applies_to)) != len(skill.applies_to):
            raise ValueError("Duplicate or missing catalog identity")
        names.add(skill.name)
        if not skill.script_path.startswith("/sops/"):
            raise ValueError("Noncanonical procedure path")
        relative = skill.script_path.removeprefix("/sops/")
        policy = operation_policy(relative)
        if (skill.script_type != "bash" or skill.risk_level != policy.catalog_risk
                or any((target, skill.trigger_condition) not in policy.bindings for target in skill.applies_to)):
            raise ValueError("Graph skill exceeds reviewed operation capabilities")
        if any(not isinstance(name, str) for name in next_skills) or len(set(next_skills)) != len(next_skills):
            raise ValueError("Invalid fallback identity")
        edges.extend((skill.name, name) for name in next_skills)
        contracts.append(ProcedureContract(**{**skill.model_dump(), "params": tuple(skill.params),
            "applies_to": tuple(sorted(skill.applies_to)), "policy": policy,
            "implementation": _sources(project_root, relative), "registry_digest": REGISTRY.digest}))
    catalog = ProcedureCatalog(contracts=tuple(sorted(contracts, key=lambda row: row.name)), fallbacks=tuple(sorted(edges)))
    for source, target in catalog.fallbacks:
        a, b = catalog.get(source), catalog.get(target)
        # Only the existing reviewed same-target Redis transition changes trigger.
        if (a.policy.operation, b.policy.operation, a.applies_to, b.applies_to) != (
                "restart_redis", "redis_flush", ("redis-cart",), ("redis-cart",)):
            raise ValueError("Unreviewed fallback transition")
    return catalog


class BoundProcedurePlan(FrozenModel):
    schema_version: Literal[1] = 1
    incident_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    skill_name: str
    target: str
    condition: str
    instance_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_id: str = Field(pattern=r"^OBS-[a-f0-9]{32}$")
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: AwareDatetime
    created_at: AwareDatetime
    expires_at: AwareDatetime
    execution_authorized: Literal[False] = False


class PreflightPermissions(FrozenModel):
    disruption: bool = Field(default=False, strict=True)
    resource_change: bool = Field(default=False, strict=True)
    runtime_configuration: bool = Field(default=False, strict=True)
    disposable_data: bool = Field(default=False, strict=True)
    fixed_cleanup: bool = Field(default=False, strict=True)


class RuntimeIdentity(FrozenModel):
    """Trusted adapter's inspect result, not a model-supplied identity assertion."""

    service_id: str
    container_name: str
    instance_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    ownership_label: str
    ownership_value: str
    captured_at: AwareDatetime
    cpu_quota: int = Field(strict=True, ge=-1)


def _numeric_clue(item, operation):
    measurements = item.measurements
    thresholds = {"HIGH_CPU": ("cpu_percent", 80), "DISK_PRESSURE": ("bytes", 104857600),
                  "MEMORY_LEAK": ("bytes", 314572800), "STALE_DATA": ("expires", 200),
                  "POOL_EXHAUSTION": ("clients", 51)}
    if item.status in thresholds:
        key, minimum = thresholds[item.status]
        value = measurements.get(key)
        return type(value) in (int, float) and math.isfinite(value) and value >= minimum
    if item.status == "OOM_KILLED":
        value = measurements.get("maxmemory")
        return type(value) is int and 0 < value <= 10485760
    if operation == "redis_config_reset":
        return measurements.get("policy") in {"noeviction", "allkeys-lfu", "allkeys-random",
            "volatile-lru", "volatile-lfu", "volatile-random", "volatile-ttl"}
    return True


def _observation(observation, target, now):
    item = ObservationRecord.model_validate(observation.model_dump(mode="json"))
    if (now.tzinfo is None or item.schema_version != 2 or item.environment_id != REGISTRY.environment_id
            or item.registry_revision != REGISTRY.revision or item.registry_digest != REGISTRY.digest
            or item.service_id != target or item.quality != "observed" or item.status == "HEALTHY"
            or not re.fullmatch(r"OBS-[a-f0-9]{32}", item.evidence_id)
            or not re.fullmatch(r"[a-f0-9]{64}", item.instance_id or "")
            or not item.measured_at <= item.collected_at <= now
            or not 0 <= (now - item.measured_at).total_seconds() < 30):
        raise ValueError("Fresh identified component evidence is required")
    return item


def bind_plan(catalog, *, skill_name, observation, incident_id, revision, now):
    """A typed intent only: no fallback proof, permissions, lease or action token."""
    contract = catalog.get(skill_name)
    item = _observation(observation, observation.service_id, now)
    if item.service_id not in contract.applies_to or item.status != contract.trigger_condition:
        raise ValueError("Procedure does not match the observed target and condition")
    return BoundProcedurePlan(incident_id=incident_id, revision=revision, catalog_digest=catalog.digest,
        contract_digest=contract.digest, skill_name=skill_name, target=item.service_id, condition=item.status,
        instance_id=item.instance_id, evidence_id=item.evidence_id, evidence_digest=_digest(item.model_dump(mode="json")),
        observed_at=item.measured_at, created_at=now, expires_at=item.measured_at + timedelta(seconds=30))


def check_preflight(plan, *, catalog, observation, incident_id, revision, now,
                    runtime=None, permissions=None):
    """Trusted caller supplies a recaptured catalog, ownership and fresh probe.

    Eligibility is NOT execution authority. No adapter consumes it to mutate Docker.
    """
    def result(reason):
        return {"eligible": reason == "eligible", "reason": reason, "execution_authorized": False}
    try:
        plan = BoundProcedurePlan.model_validate(plan.model_dump(mode="json"))
        permissions = PreflightPermissions.model_validate((permissions or PreflightPermissions()).model_dump())
        if (plan.incident_id, plan.revision) != (incident_id, revision):
            return result("plan_superseded")
        if (catalog.digest != plan.catalog_digest or catalog.get(plan.skill_name).digest != plan.contract_digest):
            return result("catalog_changed")
        contract = catalog.get(plan.skill_name)
        if not plan.observed_at <= plan.created_at <= now < plan.expires_at <= plan.observed_at + timedelta(seconds=30):
            return result("plan_expired")
        item = _observation(observation, plan.target, now)
        runtime = RuntimeIdentity.model_validate(runtime.model_dump())
        if (item.instance_id != plan.instance_id or runtime.instance_id != plan.instance_id
                or runtime.service_id != plan.target
                or runtime.container_name != REGISTRY.by_name[plan.target].container_name
                or (runtime.ownership_label, runtime.ownership_value) != (REGISTRY.ownership_label, REGISTRY.ownership_value)
                or not plan.created_at <= runtime.captured_at <= now
                or not 0 <= (now - runtime.captured_at).total_seconds() < 5):
            return result("identity_or_ownership_changed")
        if item.measured_at < plan.observed_at or item.status != plan.condition:
            return result("condition_changed")
        if item.evidence_id == plan.evidence_id and _digest(item.model_dump(mode="json")) != plan.evidence_digest:
            return result("evidence_identity_rewritten")
        if (plan.target not in contract.applies_to or plan.condition != contract.trigger_condition):
            return result("procedure_mismatch")
        if not _numeric_clue(item, contract.policy.operation):
            return result("procedure_precondition_missing")
        if contract.policy.operation == "restore_cpu" and runtime.cpu_quota <= 0:
            return result("procedure_precondition_missing")
        if not getattr(permissions, contract.policy.permission):
            return result("operator_permission_required")
        return result("eligible")
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        return result("invalid_or_unavailable_preflight")
