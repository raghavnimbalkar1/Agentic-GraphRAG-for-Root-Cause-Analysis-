"""Offline failure-driven replay epochs; provisional candidates only, never repairs."""

from copy import deepcopy
import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
import time

from agent.investigation import ContinuationContext, InvestigationLimits, InvestigationSession
from agent.candidate_recheck import RECHECK_LIMITS, recheck_evidence
from agent.evidence_policy import scope_evidence_deadline
from agent.procedure_binding import ProcedureBindingRejected, ProcedureDecision, bind_procedure_offer
from agent.shadow import ShadowLimits, ShadowRunner
from agent.shadow_budget import PROFILES, SharedShadowBudget, epoch_limits
from agent.shadow_context import SUPPORTED_EVIDENCE_LAYOUTS
from agent.shadow_protocol import INVESTIGATION_PROTOCOLS
from core.procedure_contracts import PreflightPermissions, check_preflight
from core.schemas import ObservationRecord
from eval.shadow_replay import FrozenInputs, implementation_manifest, materialize


class ReplanRejected(ValueError):
    """Fixed categories only; do not retain untrusted data or exception messages."""


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ReplanningInvestigation:
    """One operator-associated incident, at most four epochs, one process-local allowance."""

    def __init__(self, origin: FrozenInputs, *, evidence_limits=None, shadow_limits=None,
                 max_epochs=None, profile="legacy", evidence_layout="legacy-v1",
                 investigation_protocol="model-directed-v1", clock=time.monotonic):
        origin = FrozenInputs.model_validate(origin.model_dump(mode="json"))
        if evidence_layout not in SUPPORTED_EVIDENCE_LAYOUTS:
            raise ValueError("Unknown evidence layout")
        if investigation_protocol not in INVESTIGATION_PROTOCOLS:
            raise ValueError("Unknown investigation protocol")
        reviewed_evidence, reviewed_shadow = epoch_limits(profile)
        if max_epochs is None:
            max_epochs = 2 if profile == "two-epoch-v1" else 4
        if type(max_epochs) is not int or not 1 <= max_epochs <= 4:
            raise ValueError("One to four observation epochs are supported")
        if profile == "two-epoch-v1" and max_epochs != 2:
            raise ValueError("The two-epoch profile permits exactly two epoch attempts")
        self.profile = profile
        self.evidence_layout = evidence_layout
        self.investigation_protocol = investigation_protocol
        self.evidence_limits = InvestigationLimits.model_validate((evidence_limits or reviewed_evidence).model_dump())
        self.shadow_limits = ShadowLimits.model_validate((shadow_limits or reviewed_shadow).model_dump())
        self.budget = SharedShadowBudget(self.evidence_limits, self.shadow_limits, profile=profile, clock=clock)
        self._origin = next(row.payload for row in origin.records if row.payload["evidence_id"] == origin.symptom_evidence_id)
        self._origin = deepcopy(self._origin)
        self._origin_digest, self._origin_cutoff = origin.digest, origin.as_of
        self._source_kind, self._schema_version = origin.source_kind, origin.schema_version
        self.incident_id = "RPL-" + _hash([origin.digest, origin.symptom_evidence_id])
        self.max_epochs, self._clock = max_epochs, clock
        self._events, self._candidates, self._seen, self._investigated = [], [], {}, set()
        self._last_cutoff, self._last_topology = None, None
        self._current, self._candidate_until, self._last_guard = None, None, None
        self._candidate_origins, self._rechecks = {}, []
        self._current_recheck, self._recheck_until = None, None
        self._recheck_inputs = None
        self._procedure_offers, self._procedure_selections, self._offered_sources = [], [], set()
        self._procedure_evidence = {}
        self._selected_procedure, self._execution_grant = None, None
        self._active_offer, self._offer_index, self._offer_source, self._offer_until = None, None, None, None
        self._lock = threading.RLock()
        self._implementation = implementation_manifest()

    def _retire(self, state, reason, cutoff=None):
        if self._current is not None:
            self._withdraw_execution(reason)
            self._withdraw_procedure_offer(reason)
            row = self._candidates[self._current]
            row.update(state=state, retirement_reason=reason,
                       retired_at_cutoff=cutoff.isoformat() if cutoff is not None else None)
        self._current, self._candidate_until = None, None

    def _expire(self):
        if reason := self.budget.blocked_reason():
            self._retire("expired", "time_exhausted" if reason == "time_exhausted" else "incident_stopped")
        elif self._candidate_until is not None and self._clock() >= self._candidate_until:
            self._retire("expired", "freshness_expired")
        if self._current_recheck is not None:
            reason = self.budget.blocked_reason()
            if reason or self._clock() >= self._recheck_until:
                self._withdraw_recheck("incident_stopped" if reason else "freshness_expired")
        if self._active_offer is not None and self._clock() >= self._offer_until:
            self._withdraw_procedure_offer("freshness_expired")

    def _withdraw_recheck(self, reason):
        if self._current_recheck is not None:
            self._withdraw_execution(reason)
            self._withdraw_procedure_offer(reason)
            self._rechecks[self._current_recheck].update(outcome="withdrawn", reason=reason)
        self._current_recheck, self._recheck_until = None, None
        self._recheck_inputs = None

    def _withdraw_procedure_offer(self, reason):
        if self._active_offer is not None:
            self._procedure_offers[self._offer_index].update(state="withdrawn", withdrawal_reason=reason)
        self._active_offer, self._offer_index, self._offer_source, self._offer_until = None, None, None, None

    def _withdraw_execution(self, reason):
        self._selected_procedure = None
        if self._execution_grant is not None:
            self._execution_grant.revoke(reason)

    def _facts(self, inputs):
        if any(row.payload["evidence_id"] in self._procedure_evidence
               and _hash(row.payload) != self._procedure_evidence[row.payload["evidence_id"]] for row in inputs.records):
            raise ReplanRejected("evidence_identity_rewritten")
        facts = {row.payload["evidence_id"]: _hash(row.model_dump(mode="json")) for row in inputs.records}
        facts.update({row.snapshot_id: _hash(row.model_dump(mode="json")) for row in inputs.trace_records})
        if any(identifier in self._seen and self._seen[identifier] != digest for identifier, digest in facts.items()):
            raise ReplanRejected("evidence_identity_rewritten")
        return facts

    def _latest_trigger(self, inputs):
        rows = [row.payload for row in inputs.records if row.kind == "symptom"]
        latest = max(datetime.fromisoformat(row["measured_at"]) for row in rows)
        current = [row for row in rows if datetime.fromisoformat(row["measured_at"]) == latest]
        if len(current) != 1 or current[0]["evidence_id"] != inputs.symptom_evidence_id:
            raise ReplanRejected("latest_failed_trigger_required")
        if (current[0]["workflow"] != self._origin["workflow"]
                or current[0]["environment_id"] != self._origin["environment_id"]):
            raise ReplanRejected("incident_scope_mismatch")

    def _admit(self, inputs, driver, approve_external, approve_trace_transfer):
        if type(approve_external) is not bool or type(approve_trace_transfer) is not bool:
            raise ReplanRejected("explicit_transfer_flags_required")
        if getattr(driver, "kind", None) == "model" and (
                not approve_external or (inputs.schema_version == 2 and not approve_trace_transfer)):
            raise ReplanRejected("external_transfer_not_approved")
        if inputs.source_kind != self._source_kind or inputs.schema_version != self._schema_version:
            raise ReplanRejected("input_profile_changed")
        if self._last_cutoff is None:
            if inputs.digest != self._origin_digest:
                raise ReplanRejected("origin_epoch_required")
        elif inputs.as_of <= self._last_cutoff:
            raise ReplanRejected("cutoff_not_advanced")
        self._latest_trigger(inputs)
        facts = self._facts(inputs)
        topology_changed = self._last_topology is not None and inputs.topology.edges != self._last_topology.edges
        if topology_changed and (inputs.topology.revision == self._last_topology.revision
                                 or inputs.topology.captured_at <= self._last_cutoff):
            raise ReplanRejected("topology_revision_required")
        if self._last_cutoff is not None and not (set(facts) - self._investigated) and not topology_changed:
            raise ReplanRejected("no_new_evidence")
        elapsed = (inputs.as_of - self._origin_cutoff).total_seconds()
        if reason := self.budget.advance_evidence_time(elapsed):
            raise ReplanRejected(reason)
        return facts

    def run_epoch(self, inputs: FrozenInputs, driver, *, approve_external=False, approve_trace_transfer=False):
        """Caller supplies an immutable epoch/driver; this method constructs no provider."""
        with self._lock:
            epoch_started = self._clock()
            self._expire()
            if len(self._events) >= self.max_epochs:
                self._retire("expired", "budget_exhausted")
                self._withdraw_recheck("refresh_rejected")
                self._last_guard = "epoch_budget_exhausted"
                return {"outcome": "rejected", "reason": self._last_guard, "execution_authorized": False}
            retired_index = self._current
            self._retire("superseded", "fresh_epoch")
            self._withdraw_recheck("fresh_epoch")
            event = {"epoch": len(self._events) + 1, "input_digest": None, "cutoff": None,
                     "outcome": "rejected", "reason": None, "report": None, "execution_authorized": False}
            self._events.append(event)
            try:
                inputs = FrozenInputs.model_validate(inputs.model_dump(mode="json"))
                event.update(input_digest=inputs.digest, cutoff=inputs.as_of.isoformat())
                if retired_index is not None:
                    self._candidates[retired_index]["retired_at_cutoff"] = inputs.as_of.isoformat()
                facts = self._admit(inputs, driver, approve_external, approve_trace_transfer)
                context = ContinuationContext(incident_id=self.incident_id, origin_input_digest=self._origin_digest,
                    original_symptom_id=self._origin["evidence_id"], original_measured_at=self._origin["measured_at"],
                    epoch=event["epoch"], cutoff=inputs.as_of,
                    prior_candidates=[{"service_id": row["proposal"]["service_id"], "condition": row["proposal"]["condition"],
                        "epoch": row["epoch"], "state": row["state"], "reason": row["retirement_reason"]}
                        for row in self._candidates[-3:]])
                self._seen.update(facts)
                self._investigated.update(facts)
                self._last_cutoff, self._last_topology = inputs.as_of, inputs.topology
                with materialize(inputs) as path:
                    session = InvestigationSession(path, symptom_evidence_id=inputs.symptom_evidence_id,
                        as_of=inputs.as_of, limits=self.evidence_limits, include_traces=inputs.schema_version == 2,
                        budget=self.budget, continuation=context, clock=self._clock)
                    report = ShadowRunner(session, inputs.topology, driver, limits=self.shadow_limits,
                        screening="dependency", context_profile="structured", evidence_layout=self.evidence_layout,
                        investigation_protocol=self.investigation_protocol).run()
                event.update(outcome="investigated", reason=report["termination_reason"], report=report)
                if report["candidate_screened"]:
                    proposal = report["result"]["proposal"]
                    until = scope_evidence_deadline(report["initial_context"], report["turns"])
                    valid_seconds = max(0, (until - inputs.as_of).total_seconds())
                    self._candidates.append({"revision": "CAND-" + _hash([inputs.digest, proposal]),
                        "epoch": event["epoch"], "input_digest": inputs.digest, "state": "provisional",
                        "proposal": deepcopy(proposal), "valid_until": until.isoformat(),
                        "retirement_reason": None, "retired_at_cutoff": None, "execution_authorized": False})
                    self._current = len(self._candidates) - 1
                    self._candidate_until = epoch_started + valid_seconds
                    self._candidate_origins[self._current] = (inputs, epoch_started)
                self._expire()
            except (ValueError, TypeError, AttributeError, OSError, sqlite3.Error) as error:
                event.update(outcome="rejected", reason=str(error) if isinstance(error, ReplanRejected)
                             else "invalid_or_unavailable_epoch")
                self._last_guard = event["reason"]
                if retired_index is not None:
                    reason = ("time_exhausted" if event["reason"] == "time_exhausted" else "refresh_rejected")
                    self._candidates[retired_index].update(state="expired", retirement_reason=reason)
            except BaseException:
                event.update(outcome="interrupted", reason="epoch_interrupted")
                self._retire("expired", "refresh_rejected")
                raise
            finally:
                self.budget.abandon_in_flight()
                event["incident_budget"] = self.budget.snapshot()
            return deepcopy(event)

    def recheck_candidate(self, inputs: FrozenInputs, *, candidate_revision: str):
        """One explicit local recheck per latest candidate; no model, probe or executor.

        A receipt is separate from the historical proposal and can never revive it.
        Reads use a disclosed, fixed 8-read/10-second verification-stage allowance.
        """
        with self._lock:
            started = self._clock()
            self._expire()
            self._withdraw_recheck("recheck_requested")
            base = {"outcome": "rejected", "reason": None, "execution_authorized": False}
            if not self._candidates or candidate_revision != self._candidates[-1]["revision"]:
                return {**base, "reason": "latest_candidate_required"}
            if any(row["candidate_revision"] == candidate_revision for row in self._rechecks):
                return {**base, "reason": "recheck_already_attempted"}
            candidate = self._candidates[-1]
            if candidate["epoch"] != len(self._events):
                return {**base, "reason": "candidate_superseded"}
            event = {**base, "candidate_revision": candidate_revision, "input_digest": None,
                     "source_kind": self._source_kind, "report": None, "read_budget": None}
            self._rechecks.append(event)
            self._retire("expired", "recheck_requested")
            try:
                if self.budget.blocked_reason():
                    raise ReplanRejected("incident_stopped")
                inputs = FrozenInputs.model_validate(inputs.model_dump(mode="json"))
                event["input_digest"] = inputs.digest
                origin, epoch_started = self._candidate_origins[len(self._candidates) - 1]
                if inputs.source_kind != self._source_kind or inputs.schema_version != self._schema_version:
                    raise ReplanRejected("input_profile_changed")
                if inputs.as_of <= origin.as_of or inputs.as_of < origin.as_of + timedelta(seconds=started - epoch_started):
                    raise ReplanRejected("recheck_cutoff_behind_clock")
                old_topology = origin.topology.model_dump(exclude={"captured_at"})
                if (inputs.topology.model_dump(exclude={"captured_at"}) != old_topology
                        or inputs.topology.captured_at < origin.topology.captured_at):
                    raise ReplanRejected("topology_changed")
                if self.budget.advance_evidence_time((inputs.as_of - self._origin_cutoff).total_seconds()):
                    raise ReplanRejected("incident_stopped")
                facts = self._facts(inputs)
                self._seen.update(facts)
                self._latest_trigger(inputs)
                trigger = next(row.payload for row in inputs.records if row.payload["evidence_id"] == inputs.symptom_evidence_id)
                report = self._events[candidate["epoch"] - 1]["report"]
                original_trigger = report["initial_context"]["symptom"]
                if (trigger["evidence_id"] == original_trigger["evidence_id"]
                        or datetime.fromisoformat(trigger["measured_at"]) <= origin.as_of):
                    raise ReplanRejected("new_failed_transaction_required")
                if (trigger["quality"] != "observed" or trigger["workflow"] != original_trigger["workflow"]
                        or [(s["step"], s["success"]) for s in trigger["steps"]] !=
                           [(s["step"], s["success"]) for s in original_trigger["steps"]]):
                    raise ReplanRejected("transaction_symptom_changed")
                with materialize(inputs) as path:
                    session = InvestigationSession(path, symptom_evidence_id=inputs.symptom_evidence_id,
                        as_of=inputs.as_of, limits=RECHECK_LIMITS, clock=self._clock)
                    try:
                        event["report"] = recheck_evidence(session, inputs.topology, report, origin.as_of)
                    finally:
                        event["read_budget"] = {"limits": RECHECK_LIMITS.model_dump(), "session": session.snapshot()}
                event.update(outcome=event["report"]["outcome"], reason=event["report"]["reason"])
                if self._clock() - started >= RECHECK_LIMITS.max_seconds:
                    event.update(outcome="rejected", reason="recheck_time_exhausted")
                elif self.budget.blocked_reason():
                    event.update(outcome="rejected", reason="incident_stopped")
                elif event["outcome"] == "consistent":
                    until = datetime.fromisoformat(event["report"]["valid_until"])
                    remaining = (until - inputs.as_of).total_seconds() - (self._clock() - started)
                    if remaining <= 0:
                        event.update(outcome="rejected", reason="freshness_expired")
                    else:
                        self._current_recheck = len(self._rechecks) - 1
                        self._recheck_until = started + (until - inputs.as_of).total_seconds()
                        self._recheck_inputs = (inputs, started)
            except (ValueError, TypeError, AttributeError, OSError, sqlite3.Error) as error:
                event.update(outcome="rejected", reason=str(error) if isinstance(error, ReplanRejected)
                             else "invalid_or_unavailable_recheck")
            except BaseException:
                event.update(outcome="interrupted", reason="recheck_interrupted")
                raise
            return deepcopy(event)

    def _procedure_source(self, candidate_revision):
        """Only this controller's current evidence can supply a procedure binding."""
        if not self._candidates or candidate_revision != self._candidates[-1]["revision"]:
            raise ProcedureBindingRejected("latest_candidate_required")
        candidate = self._candidates[-1]
        report = self._events[candidate["epoch"] - 1]["report"]
        context, turns, proposal = deepcopy(report["initial_context"]), report["turns"], deepcopy(candidate["proposal"])
        if self._current is not None:
            inputs, started = self._candidate_origins[self._current]
            basis, deadline = "screened_candidate", self._candidate_until
        elif self._current_recheck is not None:
            inputs, started = self._recheck_inputs
            check = self._rechecks[self._current_recheck]["report"]
            basis, deadline = "consistency_check", self._recheck_until
            context.update(availability_cutoff=inputs.as_of.isoformat(), topology=inputs.topology.model_dump(mode="json"),
                symptom=next(row.payload for row in inputs.records if row.payload["evidence_id"] == inputs.symptom_evidence_id))
            turns = [{"feedback": check["feedback"]}]
            proposal["supporting_evidence"] = [row.payload["evidence_id"] for row in inputs.records
                if row.kind == "component" and row.payload["service_id"] == proposal["service_id"]
                and row.payload["evidence_id"] in check["evidence_ids"]]
        else:
            raise ProcedureBindingRejected("current_evidence_required")
        key = _hash([candidate_revision, basis, inputs.digest])
        return inputs, started, basis, deadline, key, context, turns, proposal

    def prepare_procedures(self, graph, *, candidate_revision, now, permissions=None):
        """Freeze choices before selection latency; read-only, with no provider or probe.

        The graph client and clock/permissions are trusted local inputs. A saved
        candidate or consistency-report dictionary is deliberately not accepted.
        """
        from graph.procedure_catalog import capture_procedure_catalog

        with self._lock:
            entered = self._clock()
            self._expire()
            self._withdraw_execution("new_preparation")
            self._withdraw_procedure_offer("new_preparation")
            event = {"outcome": "rejected", "reason": None, "state": "rejected", "offer": None,
                     "candidate_revision": candidate_revision, "execution_authorized": False}
            if len(self._procedure_offers) >= 2 * self.max_epochs:
                return {**event, "reason": "procedure_preparation_budget_exhausted"}
            self._procedure_offers.append(event)
            try:
                inputs, started, basis, deadline, key, context, turns, proposal = self._procedure_source(candidate_revision)
                if key in self._offered_sources:
                    raise ProcedureBindingRejected("evidence_already_offered")
                if now.tzinfo is None or now < inputs.as_of + timedelta(seconds=self._clock() - started):
                    raise ProcedureBindingRejected("procedure_clock_behind_evidence")
                catalog = capture_procedure_catalog(graph)
                offer = bind_procedure_offer(catalog, incident_id=self.incident_id, candidate_revision=candidate_revision,
                    input_digest=inputs.digest, source_kind=inputs.source_kind, evidence_basis=basis, context=context,
                    turns=turns, proposal=proposal,
                    observations=[ObservationRecord.model_validate(row.payload) for row in inputs.records if row.kind == "component"],
                    permissions=permissions if permissions is not None else PreflightPermissions(),
                    now=now + timedelta(seconds=self._clock() - entered))
                self._expire()
                if self._procedure_source(candidate_revision)[4] != key or self._clock() >= deadline:
                    raise ProcedureBindingRejected("current_evidence_required")
                self._active_offer, self._offer_index, self._offer_source = offer, len(self._procedure_offers) - 1, key
                self._offer_until = min(deadline, started + (offer.expires_at - inputs.as_of).total_seconds())
                self._offered_sources.add(key)
                event.update(outcome="offered", reason="choices_bound", state="pending_selection",
                             offer=offer.model_dump(mode="json"), selection_context=offer.selection_context())
            except Exception as error:
                event["reason"] = str(error) if isinstance(error, ProcedureBindingRejected) else "invalid_or_unavailable_binding"
            except BaseException:
                self._withdraw_procedure_offer("preparation_interrupted")
                event.update(outcome="interrupted", reason="preparation_interrupted", state="rejected")
                raise
            return deepcopy(event)

    def select_procedure(self, decision, *, graph, observation, runtime, permissions, now):
        """One untrusted choice per offer, followed by read-only preflight. Never dispatches.

        The immediate observation, runtime inspection and current effect policy
        must come from trusted adapters, not from a model response or old report.
        """
        from graph.procedure_catalog import capture_procedure_catalog

        with self._lock:
            entered = self._clock()
            self._expire()
            event = {"outcome": "rejected", "reason": "current_offer_required", "plan": None,
                     "action_dispatched": False, "execution_authorized": False}
            if self._active_offer is None:
                return event
            offer, source = self._active_offer, self._offer_source
            event["offer_digest"] = offer.digest
            event["provenance"] = {"candidate_revision": offer.candidate_revision, "input_digest": offer.input_digest,
                                   "source_kind": offer.source_kind, "evidence_basis": offer.evidence_basis}
            self._withdraw_procedure_offer("selection_attempted")
            self._procedure_selections.append(event)
            try:
                if not isinstance(decision, dict) or len(json.dumps(decision, allow_nan=False).encode()) > 4096:
                    raise ProcedureBindingRejected("invalid_procedure_decision")
                choice = ProcedureDecision.model_validate(decision)
                event["decision"] = choice.model_dump(mode="json")
                if choice.offer_digest != offer.digest:
                    raise ProcedureBindingRejected("offer_mismatch")
                if choice.action == "abstain":
                    event.update(outcome="abstained", reason="selector_abstained")
                    return deepcopy(event)
                rows = [row for row in offer.choices if row.plan.skill_name == choice.skill_name]
                if len(rows) != 1:
                    raise ProcedureBindingRejected("procedure_not_offered")
                plan = rows[0].plan
                inputs, started, _, deadline, key, *_ = self._procedure_source(offer.candidate_revision)
                if key != source:
                    raise ProcedureBindingRejected("evidence_superseded")
                if (now.tzinfo is None or now < inputs.as_of + timedelta(seconds=self._clock() - started)
                        or not offer.created_at <= now < offer.expires_at):
                    raise ProcedureBindingRejected("procedure_offer_expired_or_clock_invalid")
                permissions = PreflightPermissions.model_validate(permissions.model_dump())
                if not getattr(permissions, rows[0].required_permission):
                    raise ProcedureBindingRejected("operator_permission_required")
                observation = ObservationRecord.model_validate(observation.model_dump(mode="json"))
                if (observation.evidence_id in self._seen or observation.evidence_id in self._procedure_evidence
                        or observation.measured_at < offer.created_at):
                    raise ProcedureBindingRejected("new_target_observation_required")
                self._procedure_evidence[observation.evidence_id] = _hash(observation.model_dump(mode="json"))
                catalog = capture_procedure_catalog(graph)
                checked_at = now + timedelta(seconds=self._clock() - entered)
                check = check_preflight(plan, catalog=catalog, observation=observation, runtime=runtime,
                    incident_id=self.incident_id, revision=plan.revision,
                    now=checked_at, permissions=permissions)
                event["preflight"] = {**check, "checked_at": checked_at.isoformat(), "catalog_digest": catalog.digest,
                    "observation_id": observation.evidence_id, "observation_digest": _hash(observation.model_dump(mode="json")),
                    "permissions": permissions.model_dump(), "runtime_digest": _hash(runtime.model_dump(mode="json"))}
                self._expire()
                if self._procedure_source(offer.candidate_revision)[4] != key or self._clock() >= deadline:
                    raise ProcedureBindingRejected("current_evidence_required")
                event.update(outcome="eligible" if check["eligible"] else "rejected", reason=check["reason"],
                             plan=plan.model_dump(mode="json") if check["eligible"] else None)
                if check["eligible"]:
                    self._selected_procedure = (len(self._procedure_selections) - 1, offer, source, plan)
            except Exception as error:
                event["reason"] = str(error) if isinstance(error, ProcedureBindingRejected) else "invalid_or_unavailable_selection"
            except BaseException:
                event.update(outcome="interrupted", reason="selection_interrupted")
                raise
            return deepcopy(event)

    def authorize_execution(self, selection_index, graph, *, approve_execution=False, permissions=None):
        """Explicit trusted operator opt-in, one controlled action per controller.

        Not called by the shadow runner, API, dashboard, model or collector.
        Public selection reports and synthetic-source sessions cannot mint grants.
        """
        from agent.procedure_execution import ExecutionDenied, ProcedureExecutionGrant, utc_now
        from core.procedure_contracts import SCRIPT_OPERATIONS
        from graph.procedure_catalog import capture_procedure_catalog

        with self._lock:
            self._expire()
            if approve_execution is not True:
                raise ExecutionDenied("explicit_operator_execution_approval_required")
            if self._source_kind != "retained-observation":
                raise ExecutionDenied("synthetic_execution_forbidden")
            if self._execution_grant is not None:
                raise ExecutionDenied("incident_execution_attempt_already_reserved")
            if (type(selection_index) is not int or self._selected_procedure is None
                    or self._selected_procedure[0] != selection_index):
                raise ExecutionDenied("current_owned_selection_required")
            _, offer, source, plan = self._selected_procedure
            inputs, started, _, deadline, key, context, *_ = self._procedure_source(offer.candidate_revision)
            if context["topology"]["source"] != "neo4j-export":
                raise ExecutionDenied("retained_graph_export_required")
            now = utc_now()
            if (key != source or self._clock() >= deadline or not offer.created_at <= now < offer.expires_at
                    or now < inputs.as_of + timedelta(seconds=self._clock() - started)):
                raise ExecutionDenied("execution_evidence_expired_or_changed")
            try:
                catalog = capture_procedure_catalog(graph)
            except Exception:
                raise ExecutionDenied("execution_catalog_unavailable_or_rejected") from None
            contract = catalog.get(plan.skill_name)
            if catalog.digest != plan.catalog_digest or contract.digest != plan.contract_digest:
                raise ExecutionDenied("catalog_changed")
            if contract.script_path.removeprefix("/sops/") not in SCRIPT_OPERATIONS:
                raise ExecutionDenied("network_procedure_execution_not_supported")
            try:
                permissions = PreflightPermissions.model_validate((permissions or PreflightPermissions()).model_dump())
            except (ValueError, TypeError, AttributeError):
                raise ExecutionDenied("invalid_execution_permissions") from None
            if not getattr(permissions, contract.policy.permission):
                raise ExecutionDenied("operator_permission_required")
            self._expire()
            now = utc_now()
            try:
                current_key = self._procedure_source(offer.candidate_revision)[4]
            except ProcedureBindingRejected:
                raise ExecutionDenied("execution_evidence_expired_or_changed") from None
            if (current_key != key or self._clock() >= deadline
                    or not offer.created_at <= now < offer.expires_at):
                raise ExecutionDenied("execution_evidence_expired_or_changed")
            self._execution_grant = ProcedureExecutionGrant(self, offer, plan, contract, source, selection_index, permissions)
            return self._execution_grant

    def revoke_execution(self):
        """Stop a pending local grant; cannot undo an operation already attempted."""
        with self._lock:
            self._withdraw_execution("operator_revoked")

    def snapshot(self):
        with self._lock:
            self._expire()
            return deepcopy({"schema_version": 1, "kind": "shadow-replanning", "incident_id": self.incident_id,
                "profile": self.profile,
                "evidence_layout": self.evidence_layout,
                "investigation_protocol": self.investigation_protocol,
                "origin_input_digest": self._origin_digest, "original_symptom": self._origin,
                "association": "operator-declared-not-causal", "source_kind": self._source_kind,
                "max_epochs": self.max_epochs, "implementation": self._implementation,
                "evidence_limits": self.evidence_limits.model_dump(), "shadow_limits": self.shadow_limits.model_dump(),
                "epochs": self._events, "candidates": self._candidates,
                "current_candidate": self._candidates[self._current] if self._current is not None else None,
                "candidate_rechecks": self._rechecks,
                "current_consistency_check": self._rechecks[self._current_recheck] if self._current_recheck is not None else None,
                "procedure_offers": self._procedure_offers, "procedure_selections": self._procedure_selections,
                "procedure_evidence_digests": self._procedure_evidence,
                "procedure_execution": self._execution_grant.snapshot() if self._execution_grant is not None else None,
                "current_procedure_offer": self._active_offer.model_dump(mode="json") if self._active_offer is not None else None,
                "last_guard": self._last_guard, "incident_budget": self.budget.snapshot(),
                "recovery_verified": False, "causal_attribution": False, "execution_authorized": False})

    def invalidate_current(self):
        """Trusted refresh/import failure: retain history but withdraw the current candidate."""
        with self._lock:
            self._retire("expired", "refresh_rejected")
            self._withdraw_recheck("refresh_rejected")


def main():
    from eval.baselines.investigation import FixedProbePolicy
    from eval.shadow_replay import _write_new, load_inputs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, action="append", required=True, help="One to four frozen epochs in cutoff order")
    parser.add_argument("--output", type=Path, required=True, help="New private report; no overwrite")
    parser.add_argument("--profile", choices=PROFILES, default="legacy", help="Explicit frozen allowance; defaults stay unchanged")
    parser.add_argument("--evidence-layout", choices=SUPPORTED_EVIDENCE_LAYOUTS, default="legacy-v1",
                        help="Opt-in observation display; no change to tools, screening or allowances")
    parser.add_argument("--investigation-protocol", choices=INVESTIGATION_PROTOCOLS, default="model-directed-v1",
                        help="Explicit coverage scheduling; default and budget ceilings stay unchanged")
    args = parser.parse_args()
    if not 1 <= len(args.inputs) <= 4:
        parser.error("One to four frozen epoch paths are required")
    if args.profile == "two-epoch-v1" and len(args.inputs) != 2:
        parser.error("The two-epoch profile requires exactly two frozen epoch paths")
    if args.output.exists() or args.output.is_symlink():
        parser.error("Use a new report path")
    try:
        origin = load_inputs(args.inputs[0])
    except (ValueError, OSError, sqlite3.Error):
        print(json.dumps({"outcome": "rejected", "reason": "origin_input_unavailable_or_rejected",
                          "real_model_calls": 0, "execution_authorized": False}))
        return 1
    marker = args.output.with_name(args.output.name + ".started.json")
    try:
        _write_new(marker, {"kind": "shadow-replan-start", "origin_input_digest": origin.digest,
                            "profile": args.profile, "evidence_layout": args.evidence_layout,
                            "investigation_protocol": args.investigation_protocol,
                            "epoch_paths": len(args.inputs), "real_model_calls": 0, "execution_authorized": False})
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "output_unavailable_or_reused",
                          "real_model_calls": 0, "execution_authorized": False}))
        return 1
    investigation = ReplanningInvestigation(origin, profile=args.profile, evidence_layout=args.evidence_layout,
                                           investigation_protocol=args.investigation_protocol)
    failure, exit_code = None, 0
    try:
        for index, path in enumerate(args.inputs):
            investigation.run_epoch(origin if index == 0 else load_inputs(path), FixedProbePolicy())
    except (ValueError, OSError, sqlite3.Error):
        investigation.invalidate_current()
        failure, exit_code = {"epoch": index + 1, "reason": "input_unavailable_or_rejected"}, 1
    except KeyboardInterrupt:
        investigation.invalidate_current()
        failure, exit_code = {"epoch": index + 1, "reason": "interrupted"}, 130
    result = investigation.snapshot()
    result["cli_failure"] = failure
    try:
        _write_new(args.output, result)
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "report_write_failed",
                          "start_marker_retained": True, "real_model_calls": 0, "execution_authorized": False}))
        return 1
    print(json.dumps({"incident_id": result["incident_id"], "profile": result["profile"], "epochs": len(result["epochs"]),
        "epoch_outcomes": [row["reason"] for row in result["epochs"]],
        "candidate_states": [row["state"] for row in result["candidates"]],
        "calls_used": result["incident_budget"]["calls_used"],
        "decisions_used": result["incident_budget"]["decisions_used"],
        "cli_failure": failure, "real_model_calls": 0, "recovery_verified": False, "execution_authorized": False}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
