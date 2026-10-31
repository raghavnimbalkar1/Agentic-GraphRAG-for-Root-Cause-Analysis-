"""Opaque, process-local grants for one explicitly approved controlled operation."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from agent.evidence_policy import _scope_views
from core.procedure_contracts import BoundProcedurePlan, PreflightPermissions, ProcedureContract, check_preflight
from agent.procedure_binding import ProcedureOffer
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord


def utc_now():
    return datetime.now(timezone.utc)


def evidence_digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ExecutionDenied(PermissionError):
    """Only fixed categories, never raw probe, model or vendor exceptions."""


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ProcedureExecutionGrant:
    """Object identity is checked by its issuer. JSON receipts cannot recreate it.

    Privileged Python code is trusted; this is not a same-process security boundary,
    durable authorization, signed credential or restart-safe exactly-once protocol.
    """

    _issuer: object
    offer: ProcedureOffer
    plan: BoundProcedurePlan
    contract: ProcedureContract
    _source: str
    _selection: int
    _permissions: PreflightPermissions
    _event: dict = field(init=False, repr=False)

    def __post_init__(self):
        plan, offer = self.plan, self.offer
        object.__setattr__(self, "_event", {"kind": "operator-controlled-procedure", "execution_policy": "operator-controlled-v1",
            "state": "issued", "reason": "operator_approved",
            "offer_digest": offer.digest, "selection_index": self._selection, "incident_id": plan.incident_id,
            "plan_revision": plan.revision, "skill_name": plan.skill_name, "target": plan.target,
            "instance_id": plan.instance_id, "condition": plan.condition, "catalog_digest": plan.catalog_digest,
            "contract_digest": plan.contract_digest, "source_kind": offer.source_kind,
            "issued_at": utc_now().isoformat(), "expires_at": offer.expires_at.isoformat(),
            "revoked": False, "action_dispatched": False, "operation_confirmed": False,
            "recovery_verified": False, "execution_authorized": True, "sandbox_cleaned": None})

    def __repr__(self):
        return "<ProcedureExecutionGrant: opaque, process-local>"

    def __reduce__(self):
        raise TypeError("Execution grants cannot be serialized or copied")

    def __copy__(self):
        raise TypeError("Execution grants cannot be serialized or copied")

    def __deepcopy__(self, memo):
        raise TypeError("Execution grants cannot be serialized or copied")

    def snapshot(self):
        with self._issuer._lock:
            return json.loads(json.dumps(self._event))

    def revoke(self, reason="operator_revoked"):
        with self._issuer._lock:
            self._event.update(revoked=True, revocation_reason=reason)
            if self._event["state"] == "issued":
                self._event.update(state="revoked", reason=reason)

    def _current(self):
        owner = self._issuer
        owner._expire()
        if owner._execution_grant is not self or self._event["revoked"]:
            raise ExecutionDenied("grant_revoked_or_not_owned")
        selected = owner._selected_procedure
        if (selected is None or selected[0] != self._selection or selected[1] is not self.offer
                or selected[3] is not self.plan):
            raise ExecutionDenied("selection_superseded")
        inputs, started, _, deadline, key, context, turns, _ = owner._procedure_source(self.offer.candidate_revision)
        now = utc_now()
        if (key != self._source or owner._clock() >= deadline
                or not self.offer.created_at <= now < self.offer.expires_at
                or now < inputs.as_of + timedelta(seconds=owner._clock() - started)):
            raise ExecutionDenied("execution_evidence_expired_or_changed")
        return context, turns

    def remaining_seconds(self):
        with self._issuer._lock:
            self._current()
            return max(0, (self.offer.expires_at - utc_now()).total_seconds())

    def begin(self):
        """Consume the dispatch attempt before opening Docker or creating a sandbox."""
        with self._issuer._lock:
            if self._event["state"] != "issued":
                raise ExecutionDenied("grant_already_started_or_closed")
            try:
                self._current()
            except BaseException:
                self._event.update(state="refused", reason="grant_not_current")
                raise
            self._event.update(state="preparing", started_at=utc_now().isoformat())

    def record_sandbox(self, *, cleaned, image_id=None):
        with self._issuer._lock:
            self._event.update(sandbox_cleaned=cleaned, image_id=image_id)

    def finish(self, reason):
        with self._issuer._lock:
            if self._event["state"] not in {"operation_confirmed", "effect_uncertain"}:
                self._event.update(state="not_dispatched", reason=reason)
            self._event["finished_at"] = utc_now().isoformat()

    @contextmanager
    def operation(self, *, catalog, observations, runtime, context_check):
        """Trusted executor supplies independent fresh probes. No model fields here.

        The issuer lock spans final validation and the fixed operation so another
        local epoch cannot supersede a plan between check and mutation. External
        Docker/state changes remain a race; this is not a distributed fence.
        """
        with self._issuer._lock:
            if self._event["state"] != "preparing":
                raise ExecutionDenied("grant_already_consumed_or_closed")
            self._event.update(state="checking", reason="preflight_started")
            try:
                context, turns = self._current()
                scope, views, _, _ = _scope_views(context, turns)
                if set(observations) != set(scope):
                    raise ExecutionDenied("execution_scope_incomplete")
                now = utc_now()
                if not 0 <= (now - datetime.fromisoformat(context_check["checked_at"])).total_seconds() < 5:
                    raise ExecutionDenied("execution_context_check_expired")
                rows = {name: ObservationRecord.model_validate(row.model_dump(mode="json"))
                        for name, row in observations.items()}
                identifiers = [row.evidence_id for row in rows.values()]
                if len(set(identifiers)) != len(identifiers):
                    raise ExecutionDenied("execution_evidence_identity_conflict")
                for name, row in rows.items():
                    if (row.schema_version != 2 or row.service_id != name or row.quality != "observed"
                            or row.environment_id != REGISTRY.environment_id or row.registry_revision != REGISTRY.revision
                            or row.registry_digest != REGISTRY.digest or row.status != views[name]["status"]
                            or row.instance_id != views[name]["signature"][2]
                            or re.fullmatch(r"OBS-[a-f0-9]{32}", row.evidence_id) is None
                            or row.evidence_id in self._issuer._seen or row.evidence_id in self._issuer._procedure_evidence
                            or not datetime.fromisoformat(self._event["started_at"]) <= row.measured_at <= row.collected_at <= now
                            or (now - row.measured_at).total_seconds() >= 30):
                        raise ExecutionDenied("execution_scope_changed_or_not_fresh")
                check = check_preflight(self.plan, catalog=catalog, observation=rows[self.plan.target], runtime=runtime,
                    incident_id=self.plan.incident_id, revision=self.plan.revision, now=now, permissions=self._permissions)
                self._event["preflight"] = {**check, "checked_at": now.isoformat(), "catalog_digest": catalog.digest,
                    "context_check": context_check,
                    "evidence": [{"service_id": name, "evidence_id": row.evidence_id,
                                  "sha256": evidence_digest(row.model_dump(mode="json")),
                                  "observation": row.model_dump(mode="json")} for name, row in sorted(rows.items())],
                    "runtime": runtime.model_dump(mode="json"), "runtime_digest": evidence_digest(runtime.model_dump(mode="json"))}
                if not check["eligible"]:
                    raise ExecutionDenied(check["reason"])
                self._current()
                if self._event["sandbox_cleaned"] is not True:
                    raise ExecutionDenied("sandbox_cleanup_not_confirmed")
                self._issuer._procedure_evidence.update({row.evidence_id: evidence_digest(row.model_dump(mode="json"))
                                                         for row in rows.values()})
            except BaseException:
                self._event.update(state="refused", reason="execution_preflight_refused")
                raise
            self._event.update(state="effect_attempted", reason="fixed_operation_started", action_dispatched=True,
                               effect_started_at=utc_now().isoformat())
            try:
                yield
            except BaseException:
                self._event.update(state="effect_uncertain", reason="reconcile_before_retry")
                raise
            else:
                self._event.update(state="operation_confirmed", reason="verification_required", operation_confirmed=True)
