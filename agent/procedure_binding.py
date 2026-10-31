"""Bind screened evidence to read-only procedure choices, never action authority."""

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from agent.evidence_policy import _scope_views, assess_dependencies, scope_evidence_deadline
from core.procedure_contracts import (
    BoundProcedurePlan, FrozenModel, PreflightPermissions, _numeric_clue, bind_plan,
)
from core.schemas import ObservationRecord


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ProcedureBindingRejected(ValueError):
    """Fixed diagnostic categories only, not arbitrary graph or runtime messages."""


class ProcedureChoice(FrozenModel):
    plan: BoundProcedurePlan
    description: str
    effects: tuple[str, ...]
    effect_risk: Literal["LOW", "MEDIUM", "HIGH"]
    required_permission: str
    durability: str


class ProcedureOffer(FrozenModel):
    """Controller-owned snapshot. Its digest is a correlation ID, not a capability."""

    schema_version: Literal[1] = 1
    candidate_revision: str = Field(pattern=r"^CAND-[a-f0-9]{64}$")
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_kind: Literal["synthetic", "retained-observation"]
    evidence_basis: Literal["screened_candidate", "consistency_check"]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    permissions: PreflightPermissions
    choices: tuple[ProcedureChoice, ...] = Field(min_length=1, max_length=128)
    execution_authorized: Literal[False] = False

    @property
    def digest(self):
        return _digest(self.model_dump(mode="json"))

    def selection_context(self):
        """Only decision values/effects, not paths, code, credentials or runtime grants."""
        plan = self.choices[0].plan
        return {"offer_digest": self.digest, "candidate_revision": self.candidate_revision,
                "target": plan.target, "observed_condition": plan.condition,
                "expires_at": self.expires_at.isoformat(), "execution_authorized": False,
                "choices": [{"skill_name": row.plan.skill_name, "description": row.description,
                             "effects": list(row.effects), "effect_risk": row.effect_risk,
                             "required_permission": row.required_permission, "durability": row.durability}
                            for row in self.choices]}


class ProcedureDecision(FrozenModel):
    """Untrusted selection can name one offered skill or abstain; nothing executable."""

    offer_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["select", "abstain"]
    skill_name: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,95}$")

    @model_validator(mode="after")
    def exact_choice(self):
        if (self.action == "select") != (self.skill_name is not None):
            raise ValueError("Select one skill or abstain without a skill")
        return self


def bind_procedure_offer(catalog, *, incident_id, candidate_revision, input_digest, source_kind,
                         evidence_basis, context, turns, proposal, observations, permissions, now):
    """Trusted controller supplies its own report and frozen evidence, not a saved receipt.

    Broad diagnostic categories never substitute for exact procedure triggers.
    Matching does not prove causality, safety of all effects, or permission to act.
    """
    if assess_dependencies(context, turns, proposal)["verdict"] != "candidate_supported":
        raise ProcedureBindingRejected("candidate_not_supported")
    cutoff = datetime.fromisoformat(context["availability_cutoff"])
    until = scope_evidence_deadline(context, turns)
    if now.tzinfo is None or not cutoff <= now < until:
        raise ProcedureBindingRejected("evidence_expired")
    permissions = PreflightPermissions.model_validate(permissions.model_dump())
    _, views, _, _ = _scope_views(context, turns)
    target = proposal["service_id"]
    identifiers = views[target]["ids"]
    samples = [ObservationRecord.model_validate(row.model_dump(mode="json")) for row in observations
               if row.evidence_id in identifiers]
    if len(samples) != len(identifiers) or {row.evidence_id for row in samples} != identifiers:
        raise ProcedureBindingRejected("target_evidence_unavailable")
    signature = views[target]["signature"]
    if any((row.measured_at, row.status, row.instance_id) != (datetime.fromisoformat(signature[0]), *signature[1:])
           or row.service_id != target for row in samples):
        raise ProcedureBindingRejected("target_evidence_mismatch")
    # Same-time peers may agree on a label but disagree on the numeric precondition.
    if len({_digest(row.measurements) for row in samples}) != 1:
        raise ProcedureBindingRejected("conflicting_target_measurements")
    item = min(samples, key=lambda row: row.evidence_id)
    matching = [row for row in catalog.contracts
                if target in row.applies_to and row.trigger_condition == item.status]
    if not matching:
        raise ProcedureBindingRejected("no_matching_observed_condition")
    revision = _digest({"incident_id": incident_id, "candidate_revision": candidate_revision,
        "input_digest": input_digest, "catalog_digest": catalog.digest, "evidence_basis": evidence_basis,
        "permissions": permissions.model_dump(), "created_at": now.isoformat()})
    choices = []
    for contract in matching:
        if not getattr(permissions, contract.policy.permission) or not _numeric_clue(item, contract.policy.operation):
            continue
        plan = bind_plan(catalog, skill_name=contract.name, observation=item,
                         incident_id=incident_id, revision=revision, now=now)
        choices.append(ProcedureChoice(plan=plan, description=contract.description,
            effects=contract.policy.effects, effect_risk=contract.policy.effect_risk,
            required_permission=contract.policy.permission, durability=contract.policy.durability))
    if not choices:
        raise ProcedureBindingRejected("no_eligible_procedure")
    return ProcedureOffer(candidate_revision=candidate_revision, input_digest=input_digest,
        source_kind=source_kind, evidence_basis=evidence_basis, created_at=now,
        expires_at=min(until, *(row.plan.expires_at for row in choices)),
        permissions=permissions, choices=tuple(sorted(choices, key=lambda row: row.plan.skill_name)))
