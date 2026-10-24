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
from agent.shadow import ShadowLimits, ShadowRunner
from agent.shadow_budget import PROFILES, SharedShadowBudget, epoch_limits
from agent.shadow_context import EVIDENCE_LAYOUTS
from agent.shadow_protocol import INVESTIGATION_PROTOCOLS
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
        if evidence_layout not in EVIDENCE_LAYOUTS:
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
        self._lock = threading.RLock()
        self._implementation = implementation_manifest()

    def _retire(self, state, reason, cutoff=None):
        if self._current is not None:
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

    def _withdraw_recheck(self, reason):
        if self._current_recheck is not None:
            self._rechecks[self._current_recheck].update(outcome="withdrawn", reason=reason)
        self._current_recheck, self._recheck_until = None, None

    def _facts(self, inputs):
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
                    # Accepted references are current-epoch component evidence, not prior candidate history.
                    supporting = [row.payload for row in inputs.records if row.kind == "component"
                                  and row.payload["evidence_id"] in proposal["supporting_evidence"]]
                    until = min(datetime.fromisoformat(row["measured_at"]) + timedelta(seconds=30) for row in supporting)
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
            except (ValueError, TypeError, AttributeError, OSError, sqlite3.Error) as error:
                event.update(outcome="rejected", reason=str(error) if isinstance(error, ReplanRejected)
                             else "invalid_or_unavailable_recheck")
            except BaseException:
                event.update(outcome="interrupted", reason="recheck_interrupted")
                raise
            return deepcopy(event)

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
    parser.add_argument("--evidence-layout", choices=EVIDENCE_LAYOUTS, default="legacy-v1",
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
