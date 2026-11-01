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


def _operation_journal():
    from core.config import settings
    from core.operation_journal import OperationJournal

    return OperationJournal(settings.audit_dir / "controlled_operations.sqlite")


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
    _lease: object = field(init=False, repr=False, default=None)
    _journal_healthy: bool = field(init=False, repr=False, default=True)

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
            from core.operation_journal import AttemptIdentity, JournalDenied

            try:
                identity = AttemptIdentity(environment_id=REGISTRY.environment_id, registry_digest=REGISTRY.digest,
                    incident_id=self.plan.incident_id, trigger_evidence_id=self._issuer._origin["evidence_id"],
                    plan_revision=self.plan.revision, offer_digest=self.offer.digest, catalog_digest=self.plan.catalog_digest,
                    contract_digest=self.contract.digest, target=self.plan.target, instance_id=self.plan.instance_id,
                    skill_name=self.plan.skill_name, condition=self.plan.condition)
                object.__setattr__(self, "_lease", _operation_journal().reserve(identity, self._event))
                self._event.update(durable_attempt_id=self._lease.identifier, journal_persistence="confirmed")
                self._persist("preparing")
                self._current()  # Durable I/O must not extend the evidence lifetime.
            except BaseException as error:
                self._event.update(state="refused", reason="durable_attempt_reservation_denied",
                                   journal_denial=str(error) if isinstance(error, JournalDenied) else "unavailable")
                self._close_journal()
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise ExecutionDenied("durable_attempt_reservation_denied") from None

    def _persist(self, stage, receipt=None):
        try:
            if self._lease is None or not self._journal_healthy:
                raise ExecutionDenied("durable_attempt_record_unavailable")
            self._lease.record(stage, self._event if receipt is None else receipt)
        except BaseException as error:
            object.__setattr__(self, "_journal_healthy", False)
            self._event.update(journal_persistence="unconfirmed", reason="durable_attempt_record_unconfirmed",
                               recovery_verified=False)
            if self._event["action_dispatched"] and stage in {"effect_attempted", "operation_confirmed", "effect_uncertain"}:
                self._event.update(state="effect_uncertain", operation_confirmed=False)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ExecutionDenied("durable_attempt_record_unconfirmed") from None

    def _close_journal(self):
        if self._lease is not None:
            try:
                self._lease.close()
            except Exception:
                object.__setattr__(self, "_journal_healthy", False)
                self._event.update(journal_persistence="unconfirmed", recovery_verified=False)
                if self._event["action_dispatched"] and "recovery" not in self._event:
                    self._event.update(state="effect_uncertain", reason="journal_close_unconfirmed", operation_confirmed=False)
                return False
        return True

    def record_sandbox(self, *, cleaned, image_id=None):
        with self._issuer._lock:
            self._event.update(sandbox_cleaned=cleaned, image_id=image_id)
            self._persist("preparing")

    def finish(self, reason):
        with self._issuer._lock:
            if self._event["state"] not in {"operation_confirmed", "effect_uncertain"}:
                self._event.update(state="not_dispatched", reason=reason)
            self._event["finished_at"] = utc_now().isoformat()
            try:
                if self._journal_healthy:
                    self._persist(self._event["state"])
            except ExecutionDenied:
                pass
            finally:
                self._close_journal()

    def _begin_recovery(self, policy):
        """Consume one read-only verification attempt, independent of diagnosis TTL."""
        with self._issuer._lock:
            if self._issuer._execution_grant is not self:
                raise ExecutionDenied("owned_execution_grant_required")
            if (self._event["state"] != "operation_confirmed" or not self._event["operation_confirmed"]
                    or self._event["sandbox_cleaned"] is not True):
                raise ExecutionDenied("confirmed_cleaned_operation_required")
            if "recovery" in self._event:
                raise ExecutionDenied("recovery_attempt_already_reserved")
            self._event["recovery"] = {"outcome": "checking", "started_at": utc_now().isoformat(), "policy": policy}
            try:
                if self._lease is None or not self._journal_healthy:
                    raise ExecutionDenied("durable_attempt_record_unavailable")
                object.__setattr__(self, "_lease", self._lease.journal.resume_verification(self._lease.identifier, self._lease._token))
                self._persist("verifying")
            except BaseException as error:
                self._event["recovery"].update(outcome="inconclusive", reason="durable_verification_start_unconfirmed")
                self._close_journal()
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise ExecutionDenied("durable_verification_start_unconfirmed") from None
            return self.snapshot()

    def _finish_recovery(self, report):
        with self._issuer._lock:
            if (self._issuer._execution_grant is not self
                    or self._event.get("recovery", {}).get("outcome") != "checking"):
                raise ExecutionDenied("owned_active_recovery_required")
            self._event["recovery"] = json.loads(json.dumps(report, allow_nan=False))
            self._event["recovery_verified"] = report["outcome"] in {"verified_recovery_window", "verified_mitigation"}
            try:
                self._persist("verification_inconclusive" if report["outcome"] == "inconclusive" else report["outcome"])
            except ExecutionDenied:
                report = {**report, "outcome": "inconclusive", "reason": "durable_verification_finish_unconfirmed"}
                self._event["recovery"] = json.loads(json.dumps(report))
                self._event["recovery_verified"] = False
            finally:
                if not self._close_journal():
                    report = {**report, "outcome": "inconclusive", "reason": "durable_verification_close_unconfirmed"}
                    self._event["recovery"] = json.loads(json.dumps(report))
            return report

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
            # Commit admission before the external effect. A crash in between is
            # deliberately uncertain, never permission to repeat the operation.
            admission = {**self._event, "state": "effect_attempted", "reason": "fixed_operation_started",
                         "action_dispatched": True, "effect_started_at": utc_now().isoformat()}
            self._persist("effect_attempted", admission)
            self._event.update(admission)
            try:
                self._current()
                now = utc_now()
                if not 0 <= (now - datetime.fromisoformat(context_check["checked_at"])).total_seconds() < 5:
                    raise ExecutionDenied("execution_context_check_expired")
                final_check = check_preflight(self.plan, catalog=catalog, observation=rows[self.plan.target], runtime=runtime,
                    incident_id=self.plan.incident_id, revision=self.plan.revision, now=now, permissions=self._permissions)
                if not final_check["eligible"]:
                    raise ExecutionDenied("post_journal_preflight_expired")
                yield
            except BaseException:
                self._event.update(state="effect_uncertain", reason="reconcile_before_retry")
                try:
                    self._persist("effect_uncertain")
                except ExecutionDenied:
                    pass
                raise
            else:
                self._event.update(state="operation_confirmed", reason="verification_required", operation_confirmed=True,
                                   effect_finished_at=utc_now().isoformat())
                self._persist("operation_confirmed")
