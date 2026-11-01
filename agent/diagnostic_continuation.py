"""Explicit fresh diagnostic epochs from completed data. Never resumes a saved graph or repair."""

from copy import deepcopy
from datetime import datetime, timezone
import re
import time

from agent.investigation import HypothesisProposal, InvestigationLimits
from agent.shadow import DependencySnapshot, ShadowLimits
from agent.shadow_budget import SharedShadowBudget
from core.operation_journal import JournalDenied, OperationJournal
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.workflow_checkpoints import CheckpointDenied, DiagnosticCheckpoints, diagnostic_thread_id
from eval.shadow_replan import ReplanRejected, ReplanningInvestigation, _hash
from eval.shadow_replay import FrozenInputs, implementation_manifest


class ContinuationRejected(ValueError):
    """Fixed categories; no untrusted exception text is returned."""


def utc_now():
    return datetime.now(timezone.utc)


def _distinct_stores(store, journal):
    if type(journal) is not OperationJournal:
        raise ContinuationRejected("typed_operation_journal_required")
    if store.path == journal.path or (store.path.exists() and journal.path.exists() and store.path.samefile(journal.path)):
        raise ContinuationRejected("distinct_diagnostic_and_operation_stores_required")


def _fresh(controller, inputs, driver, approve_external, approve_trace_transfer):
    if inputs.source_kind == "retained-observation":
        age = (utc_now() - inputs.as_of).total_seconds()
        if not 0 <= age <= 30:
            raise ContinuationRejected("recent_retained_capture_required")
    controller._admit(inputs, driver, approve_external, approve_trace_transfer)


class _JournalCheckedDriver:
    def __init__(self, driver, guard):
        self._driver, self._guard = driver, guard

    def __getattr__(self, name):
        return getattr(self._driver, name)

    def complete(self, *args, **kwargs):
        self._guard.check()
        return self._driver.complete(*args, **kwargs)


def _run(controller, inputs, driver, store, guard, *, parent=None, approve_external=False, approve_trace_transfer=False):
    event = controller.run_epoch(inputs, _JournalCheckedDriver(driver, guard), checkpoint_path=store.path,
        approve_external=approve_external, approve_trace_transfer=approve_trace_transfer)
    guard.check()
    if event["outcome"] != "investigated":
        return {"outcome": "rejected", "reason": event["reason"], "diagnosis": controller.snapshot(),
                "execution_authorized": False, "automatic_continuation_allowed": False}
    identifier = event["report"]["workflow_checkpoint"]["thread_id"]
    captured = utc_now().isoformat()
    payload = {"schema_version": 1, "captured_at": captured, "origin_digest": controller._origin_digest,
        "implementation": controller._implementation, "journal_identity": guard.identity,
        "parent_thread_id": parent, "state": controller.diagnostic_resume_state()}
    store.save_controller(identifier, payload)
    guard.check()
    return {"outcome": "checkpointed", "thread_id": identifier, "parent_thread_id": parent,
            "diagnosis": controller.snapshot(), "historical_only": True, "execution_authorized": False,
            "recovery_verified": False, "automatic_continuation_allowed": False}


def _restore(origin, record, *, clock):
    payload, now = record["payload"], utc_now()
    if record["claimed_child"] is not None:
        raise ContinuationRejected("continuation_already_claimed")
    if (type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload["origin_digest"] != origin.digest or payload["implementation"] != implementation_manifest()):
        raise ContinuationRejected("continuation_origin_or_implementation_changed")
    captured = datetime.fromisoformat(payload["captured_at"])
    completed = datetime.fromisoformat(record["completed_at"])
    binding = payload["journal_identity"]
    if (captured.tzinfo is None or captured > now or completed.tzinfo is None or completed > now
            or type(binding) is not dict or set(binding) != {"device", "inode"}
            or any(type(value) is not int or value < 0 for value in binding.values())):
        raise ContinuationRejected("continuation_clock_invalid")
    captured = min(captured, completed)
    state, saved = payload["state"], payload["state"]["snapshot"]
    controller = ReplanningInvestigation(origin, profile=saved["profile"], evidence_layout=saved["evidence_layout"],
        investigation_protocol=saved["investigation_protocol"], max_epochs=saved["max_epochs"],
        evidence_limits=InvestigationLimits.model_validate(saved["evidence_limits"]),
        shadow_limits=ShadowLimits.model_validate(saved["shadow_limits"]), clock=clock)
    expected = controller.snapshot()
    for key in ("schema_version", "kind", "incident_id", "origin_input_digest", "original_symptom", "source_kind",
                "association", "implementation", "profile", "evidence_layout", "investigation_protocol",
                "max_epochs", "evidence_limits", "shadow_limits"):
        if saved[key] != expected[key]:
            raise ContinuationRejected("continuation_controller_identity_changed")
    if (saved["execution_authorized"] is not False or saved["causal_attribution"] is not False
            or saved["recovery_verified"] is not False or saved["procedure_execution"] is not None
            or saved["procedure_offers"] or saved["procedure_selections"] or saved["candidate_rechecks"]
            or saved["current_consistency_check"] is not None or saved["current_procedure_offer"] is not None
            or saved["procedure_evidence_digests"] or saved["last_guard"] is not None
            or state["input_schema_version"] != origin.schema_version):
        raise ContinuationRejected("diagnostic_only_completed_history_required")
    events, candidates = saved["epochs"], saved["candidates"]
    if not isinstance(events, list) or not 1 <= len(events) < controller.max_epochs or not isinstance(candidates, list):
        raise ContinuationRejected("continuation_epoch_budget_exhausted")
    for index, event in enumerate(events, 1):
        if (type(event["epoch"]) is not int or event["epoch"] != index or event["outcome"] != "investigated"
                or not isinstance(event["report"], dict) or event["report"]["execution_authorized"] is not False):
            raise ContinuationRejected("continuation_history_inconsistent")
    last = events[-1]
    if last["report"]["workflow_checkpoint"]["thread_id"] != diagnostic_thread_id(
            payload["native_identity"]["environment_id"], payload["native_identity"]["symptom_evidence_id"]):
        raise ContinuationRejected("continuation_checkpoint_link_changed")
    if len(candidates) > len(events):
        raise ContinuationRejected("continuation_candidate_history_inconsistent")
    candidates = deepcopy(candidates)
    for candidate in candidates:
        number = candidate["epoch"]
        proposal = HypothesisProposal.model_validate(candidate["proposal"]).model_dump(mode="json")
        if (type(number) is not int or not 1 <= number <= len(events)
                or candidate["execution_authorized"] is not False
                or candidate["revision"] != "CAND-" + _hash([events[number - 1]["input_digest"], proposal])
                or candidate["proposal"] != events[number - 1]["report"]["result"].get("proposal")):
            raise ContinuationRejected("continuation_candidate_history_inconsistent")
        if candidate["state"] == "provisional":
            candidate.update(state="expired", retirement_reason="process_restart", retired_at_cutoff=None)
        elif candidate["state"] not in {"expired", "superseded"}:
            raise ContinuationRejected("continuation_candidate_history_inconsistent")
    seen, investigated = state["seen"], state["investigated"]
    if (not isinstance(seen, dict) or not seen or len(seen) > 16384 or not isinstance(investigated, list)
            or len(investigated) != len(set(investigated)) or not set(investigated) <= set(seen)
            or any(not isinstance(key, str) or len(key) > 128 or not isinstance(value, str)
                   or re.fullmatch(r"[a-f0-9]{64}", value) is None for key, value in seen.items())):
        raise ContinuationRejected("continuation_evidence_history_invalid")
    cutoff = datetime.fromisoformat(state["last_cutoff"])
    topology = DependencySnapshot.model_validate(state["last_topology"])
    if (cutoff.tzinfo is None or cutoff != datetime.fromisoformat(last["cutoff"])
            or cutoff != datetime.fromisoformat(payload["native_identity"]["as_of"])
            or cutoff < origin.as_of or topology.captured_at > cutoff):
        raise ContinuationRejected("continuation_cutoff_inconsistent")
    resumed_at = utc_now()
    if resumed_at < now:
        raise ContinuationRejected("continuation_clock_invalid")
    controller.budget = SharedShadowBudget.from_completed_checkpoint(controller.evidence_limits, controller.shadow_limits,
        saved["incident_budget"], downtime=(resumed_at - captured).total_seconds(), clock=clock)
    controller._events, controller._candidates = deepcopy(events), candidates
    controller._seen, controller._investigated = deepcopy(seen), set(investigated)
    controller._last_cutoff, controller._last_topology = cutoff, topology
    return controller


def start_diagnosis(origin, driver, *, workflow_database, operation_journal, approve_start=False,
                    approve_external=False, approve_trace_transfer=False, clock=time.monotonic, **profile):
    if approve_start is not True:
        return {"outcome": "rejected", "reason": "explicit_diagnostic_start_required", "execution_authorized": False}
    try:
        origin = FrozenInputs.model_validate(origin.model_dump(mode="json"))
        controller = ReplanningInvestigation(origin, clock=clock, **profile)
        store = DiagnosticCheckpoints(workflow_database)
        _distinct_stores(store, operation_journal)
        _fresh(controller, origin, driver, approve_external, approve_trace_transfer)
        with operation_journal.diagnostic_guard(REGISTRY.environment_id, controller.incident_id,
                original_trigger=origin.symptom_evidence_id, initialize=True) as guard:
            return _run(controller, origin, driver, store, guard, approve_external=approve_external,
                        approve_trace_transfer=approve_trace_transfer)
    except (ContinuationRejected, ReplanRejected, CheckpointDenied, JournalDenied) as error:
        return {"outcome": "rejected", "reason": str(error), "execution_authorized": False}
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return {"outcome": "rejected", "reason": "diagnostic_start_unavailable", "execution_authorized": False}


def continue_diagnosis(origin, fresh, driver, *, parent_thread_id, workflow_database, operation_journal,
                       approve_continuation=False, approve_external=False, approve_trace_transfer=False, clock=time.monotonic):
    if approve_continuation is not True:
        return {"outcome": "rejected", "reason": "explicit_diagnostic_continuation_required", "execution_authorized": False}
    try:
        origin, fresh = (FrozenInputs.model_validate(value.model_dump(mode="json")) for value in (origin, fresh))
        store = DiagnosticCheckpoints(workflow_database)
        _distinct_stores(store, operation_journal)
        record = store.load_controller(parent_thread_id)
        controller = _restore(origin, record, clock=clock)
        _fresh(controller, fresh, driver, approve_external, approve_trace_transfer)
        child = diagnostic_thread_id(REGISTRY.environment_id, fresh.symptom_evidence_id)
        with operation_journal.diagnostic_guard(REGISTRY.environment_id, controller.incident_id,
                original_trigger=origin.symptom_evidence_id,
                expected_identity=record["payload"]["journal_identity"]) as guard:
            store.claim_continuation(parent_thread_id, child, expected_digest=record["digest"])
            guard.check()
            return _run(controller, fresh, driver, store, guard, parent=parent_thread_id,
                        approve_external=approve_external, approve_trace_transfer=approve_trace_transfer)
    except (ContinuationRejected, ReplanRejected, CheckpointDenied, JournalDenied) as error:
        return {"outcome": "rejected", "reason": str(error), "execution_authorized": False}
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return {"outcome": "rejected", "reason": "diagnostic_continuation_unavailable", "execution_authorized": False}
