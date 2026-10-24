"""Synthetic software checks only: no inference, live probes, grants or repairs."""

from copy import deepcopy
from datetime import timedelta

import pytest

from agent.candidate_recheck import RECHECK_LIMITS
from agent.investigation import InvestigationSession
from core.diagnostics import ComponentReader
from core.schemas import ObservationRecord
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import FrozenInputs, FrozenRecord, implementation_manifest
from tests.test_shadow_replan import Clock, epoch

STATUSES = {"paymentservice": "DOWN", "checkoutservice": "DEGRADED"}
EDGES = (("checkoutservice", "paymentservice"), ("checkoutservice", "currencyservice"))


def setup(*, edges=EDGES):
    clock = Clock()
    origin = epoch(statuses=STATUSES, edges=edges)
    run = ReplanningInvestigation(origin, clock=clock)
    run.run_epoch(origin, FixedProbePolicy())
    candidate = deepcopy(run.snapshot()["current_candidate"])
    assert candidate is not None
    clock.value += 40
    assert run.snapshot()["current_candidate"] is None
    return run, clock, origin, candidate


def refresh(origin, *, offset=60, statuses=None, **kwargs):
    inputs = epoch(1, statuses=STATUSES if statuses is None else statuses, previous=origin,
                   edges=origin.topology.edges, offset=offset, **kwargs)
    data = inputs.model_dump(mode="json")
    data["topology"]["revision"] = origin.topology.revision
    return FrozenInputs.model_validate(data)


def check(run, origin, candidate, **kwargs):
    return run.recheck_candidate(refresh(origin, **kwargs), candidate_revision=candidate["revision"])


def test_expired_proposal_stays_expired_and_new_receipt_has_no_authority():
    run, clock, origin, candidate = setup()
    before = run.snapshot()
    result = check(run, origin, candidate)
    snap = run.snapshot()
    assert result["outcome"] == "consistent"
    assert snap["current_candidate"] is None and snap["candidates"] == before["candidates"]
    assert result["report"]["validation_target"] == candidate["proposal"]["service_id"]
    assert result["report"]["reason"] == "same_candidate_new_observations"
    assert result["report"]["read_budget"]["session"]["calls_used"] == 3
    assert result["report"]["read_budget"]["limits"] == RECHECK_LIMITS.model_dump()
    assert snap["current_consistency_check"] == result
    for key in ("calls_used", "decisions_used", "response_bytes", "known_reported_tokens", "admission_units"):
        assert snap["incident_budget"][key] == before["incident_budget"][key]
    assert snap["incident_budget"]["evidence_elapsed_seconds"] == 60
    assert "proposal" not in result["report"]
    assert not result["execution_authorized"] and not result["report"]["execution_authorized"]
    assert set(result["report"]["evidence_ids"]).isdisjoint(candidate["proposal"]["supporting_evidence"])
    clock.value += 30
    assert run.snapshot()["current_consistency_check"] is None
    assert run.snapshot()["candidate_rechecks"][0]["reason"] == "freshness_expired"


@pytest.mark.parametrize("statuses", [
    {}, {"paymentservice": "UNKNOWN", "checkoutservice": "DEGRADED"},
    {"checkoutservice": "DEGRADED"},
    {**STATUSES, "currencyservice": "DOWN"},
    {**STATUSES, "paymentservice": "CONNECTION_REFUSED"},
    {**STATUSES, "checkoutservice": "HEALTHY"},
])
def test_changed_unknown_recovered_or_competing_state_never_retargets(statuses):
    run, _, origin, candidate = setup()
    result = check(run, origin, candidate, statuses=statuses)
    assert result["outcome"] == "rejected"
    assert result["reason"] in {"scope_state_changed", "incomplete_or_uncertain_scope"}
    assert result["report"]["validation_target"] == "paymentservice"
    assert run.snapshot()["current_consistency_check"] is None
    assert run.snapshot()["current_candidate"] is None


@pytest.mark.parametrize("service", ["checkoutservice", "paymentservice", "currencyservice"])
@pytest.mark.parametrize("identity", ["f" * 64, None])
def test_every_scoped_instance_is_bound_not_only_the_target(service, identity):
    run, _, origin, candidate = setup()
    result = check(run, origin, candidate, instances={service: identity})
    assert result["outcome"] == "rejected"
    assert result["reason"] == ("runtime_instance_changed" if identity else "incomplete_or_uncertain_scope")


def test_old_ids_cannot_be_relabelled_as_new_evidence():
    run, _, origin, candidate = setup()
    data = refresh(origin).model_dump(mode="json")
    old = next(row for row in data["records"] if row["kind"] == "component"
               and row["payload"]["service_id"] == "paymentservice")
    old["payload"]["status"] = "HEALTHY"
    old["payload"]["detail"] = "Built-in health probe: HEALTHY"
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["reason"] == "evidence_identity_rewritten" and result["report"] is None


def test_new_ids_with_old_measurement_times_do_not_refresh_the_scope():
    run, _, origin, candidate = setup()
    data = refresh(origin).model_dump(mode="json")
    for row in data["records"]:
        if row["kind"] == "component" and int(row["payload"]["evidence_id"].split("-")[1], 16) > 100:
            row["payload"]["measured_at"] = origin.as_of - timedelta(seconds=1)
            row["payload"] = ObservationRecord.model_validate(row["payload"]).model_dump(mode="json")
    # At +60 these are stale as well as not new; neither path may issue a receipt.
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["outcome"] == "rejected"


@pytest.mark.parametrize("field,value", [("revision", "different-topology"), ("source", "neo4j-export"),
                                        ("edges", [])])
def test_topology_revision_source_or_edges_change_requires_new_reasoning(field, value):
    run, _, origin, candidate = setup()
    data = refresh(origin).model_dump(mode="json")
    data["topology"][field] = value
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["reason"] == "topology_changed" and result["report"] is None


def test_old_cutoff_cannot_reset_clock_after_provider_wait():
    run, _, origin, candidate = setup()
    result = check(run, origin, candidate, offset=5)
    assert result["reason"] == "recheck_cutoff_behind_clock"
    assert not run.snapshot()["current_consistency_check"]


def test_same_bundle_or_second_attempt_is_not_a_retry_budget():
    run, _, origin, candidate = setup()
    first = run.recheck_candidate(origin, candidate_revision=candidate["revision"])
    assert first["reason"] == "recheck_cutoff_behind_clock"
    result = check(run, origin, candidate)
    assert result["reason"] == "recheck_already_attempted"
    assert len(run.snapshot()["candidate_rechecks"]) == 1


def test_requested_revision_must_be_the_actual_latest_candidate():
    run, _, origin, _ = setup()
    result = run.recheck_candidate(refresh(origin), candidate_revision="CAND-fabricated")
    assert result["reason"] == "latest_candidate_required"
    assert not run.snapshot()["candidate_rechecks"]


def test_new_epoch_withdraws_receipt_and_cannot_reuse_old_candidate():
    run, _, origin, candidate = setup()
    assert check(run, origin, candidate)["outcome"] == "consistent"
    # The recheck has already bound these IDs; rewriting them in a new epoch fails.
    fresh = refresh(origin, statuses={"checkoutservice": "DEGRADED"})
    run.run_epoch(fresh, FixedProbePolicy())
    snap = run.snapshot()
    assert snap["current_consistency_check"] is None
    assert snap["candidate_rechecks"][0]["reason"] == "fresh_epoch"
    assert run.recheck_candidate(refresh(origin), candidate_revision=candidate["revision"])["reason"] == "recheck_already_attempted"


@pytest.mark.parametrize("kind", ["provider", "store"])
def test_unavailable_or_slow_reads_cannot_leave_a_current_receipt(monkeypatch, kind):
    run, clock, origin, candidate = setup()
    original = ComponentReader.window

    def window(*args, **kwargs):
        if kind == "store":
            raise OSError("untrusted message")
        clock.value += 10
        return original(*args, **kwargs)

    monkeypatch.setattr(ComponentReader, "window", window)
    result = check(run, origin, candidate)
    assert result["outcome"] == "rejected"
    assert result["reason"] in {"recheck_read_failed", "recheck_time_exhausted"}
    assert run.snapshot()["current_consistency_check"] is None
    assert "untrusted message" not in str(result)


def test_interruption_consumes_attempt_and_never_creates_a_receipt(monkeypatch):
    run, _, origin, candidate = setup()
    monkeypatch.setattr(InvestigationSession, "call", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        check(run, origin, candidate)
    snap = run.snapshot()
    assert snap["candidate_rechecks"][0]["outcome"] == "interrupted"
    assert snap["current_consistency_check"] is None and snap["current_candidate"] is None


def test_unknown_model_usage_or_elapsed_incident_blocks_recheck():
    run, clock, origin, candidate = setup()
    run.budget.halt("usage_unknown")
    assert check(run, origin, candidate)["reason"] == "incident_stopped"
    assert run.snapshot()["current_consistency_check"] is None


def test_original_input_profile_cannot_be_relabelled():
    run, _, origin, candidate = setup()
    data = refresh(origin).model_dump(mode="json")
    data["source_kind"] = "retained-observation"
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["reason"] == "input_profile_changed"


def test_latest_transaction_must_still_fail_the_same_steps():
    run, _, origin, candidate = setup()
    result = check(run, origin, candidate, outcome="healthy")
    assert result["reason"] == "latest_failed_trigger_required" and result["report"] is None


def test_no_fresh_failed_transaction_means_no_consistency_receipt():
    run, _, origin, candidate = setup()
    data = refresh(origin).model_dump(mode="json")
    data["records"] = [row for row in data["records"] if row["payload"]["evidence_id"] != data["symptom_evidence_id"]]
    data["symptom_evidence_id"] = origin.symptom_evidence_id
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["reason"] == "new_failed_transaction_required"


def test_same_time_conflicting_observations_are_not_healthy_negative_controls():
    run, _, origin, candidate = setup()
    inputs = refresh(origin)
    payload = next(row.payload.copy() for row in inputs.records if row.kind == "component"
        and row.payload["service_id"] == "currencyservice" and row.payload["measured_at"] > origin.as_of.isoformat())
    payload.update(evidence_id="OBS-" + "f" * 32, status="DOWN", detail="Built-in health probe: DOWN")
    peer = FrozenRecord(kind="component", stored_at=inputs.as_of.timestamp(), payload=payload)
    data = inputs.model_dump(mode="json")
    data["records"].append(peer.model_dump(mode="json"))
    data["records"].sort(key=lambda row: row["payload"]["evidence_id"])
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["outcome"] == "rejected" and result["reason"] == "incomplete_or_uncertain_scope"


def test_receipt_is_copied_and_refresh_failure_withdraws_it():
    run, _, origin, candidate = setup()
    result = check(run, origin, candidate)
    result["report"]["validation_target"] = "redis-cart"
    assert run.snapshot()["current_consistency_check"]["report"]["validation_target"] == "paymentservice"
    run.invalidate_current()
    assert run.snapshot()["current_consistency_check"] is None


def test_recheck_implementation_is_hashed_for_new_experiments():
    assert "agent/candidate_recheck.py" in implementation_manifest()["source_sha256"]


def test_new_bundle_cannot_skip_past_the_whole_incident_deadline(monkeypatch):
    # A valid fixture bundle beyond 180 seconds must not create a new clock.
    # Shift both inputs into the past so FrozenInputs still forbids future data.
    from tests.test_shadow_replan import BASE
    monkeypatch.setattr("tests.test_shadow_replan.BASE", BASE - timedelta(seconds=300))
    origin = epoch(statuses=STATUSES, edges=EDGES)
    run = ReplanningInvestigation(origin, clock=Clock())
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["current_candidate"]
    result = check(run, origin, candidate, offset=181)
    assert result["reason"] == "incident_stopped"
    assert run.snapshot()["incident_budget"]["stop_reason"] == "time_exhausted"


def test_recheck_before_expiry_still_withdraws_old_proposal_instead_of_renewing_it():
    origin, clock = epoch(statuses=STATUSES, edges=EDGES), Clock()
    run = ReplanningInvestigation(origin, clock=clock)
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["current_candidate"]
    assert check(run, origin, candidate, offset=5)["outcome"] == "consistent"
    snap = run.snapshot()
    assert snap["current_candidate"] is None
    assert snap["candidates"][0]["retirement_reason"] == "recheck_requested"


def test_failed_refresh_at_epoch_limit_withdraws_consistency_receipt():
    origin = epoch(statuses=STATUSES, edges=EDGES)
    run = ReplanningInvestigation(origin, clock=Clock(), max_epochs=1)
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["current_candidate"]
    assert check(run, origin, candidate, offset=5)["outcome"] == "consistent"
    assert run.run_epoch(refresh(origin), FixedProbePolicy())["reason"] == "epoch_budget_exhausted"
    assert run.snapshot()["current_consistency_check"] is None


def test_interrupted_read_preserves_already_charged_attempt(monkeypatch):
    run, _, origin, candidate = setup()
    original = InvestigationSession.call

    def interrupted(session, request):
        original(session, request)
        raise KeyboardInterrupt()

    monkeypatch.setattr(InvestigationSession, "call", interrupted)
    with pytest.raises(KeyboardInterrupt):
        check(run, origin, candidate)
    attempt = run.snapshot()["candidate_rechecks"][0]
    assert attempt["read_budget"]["session"]["calls_used"] == 1
    assert len(attempt["read_budget"]["session"]["history"]) == 1
    assert not run.snapshot()["current_consistency_check"]


def test_fresh_at_cutoff_but_old_measurement_is_not_a_new_observation():
    origin = epoch(statuses=STATUSES, edges=EDGES)
    run = ReplanningInvestigation(origin, clock=Clock())
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["current_candidate"]
    data = refresh(origin, offset=5).model_dump(mode="json")
    for row in data["records"]:
        if row["kind"] == "component":
            row["payload"]["measured_at"] = origin.as_of - timedelta(seconds=1)
            row["payload"] = ObservationRecord.model_validate(row["payload"]).model_dump(mode="json")
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["reason"] == "new_component_observations_required"


def test_full_scope_recheck_has_separate_disclosed_reads_after_diagnosis_exhausts_its_reads():
    from tests.test_replanning_profile import full_epochs
    origin, _ = full_epochs()
    run = ReplanningInvestigation(origin, profile="two-epoch-v1", evidence_layout="time-indexed-v1",
        investigation_protocol="bounded-coverage-v1", clock=Clock())
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["current_candidate"]
    before = run.snapshot()["incident_budget"]
    result = check(run, origin, candidate, offset=5)
    assert result["outcome"] == "consistent"
    assert result["read_budget"]["session"]["calls_used"] == 8
    assert len(result["report"]["feedback"]) == 8
    after = run.snapshot()["incident_budget"]
    assert after["calls_used"] == before["calls_used"] == 8
    assert after["decisions_used"] == before["decisions_used"] == 2


@pytest.mark.parametrize("status", ["OOM_KILLED", "HIGH_CPU"])
def test_resource_condition_rechecks_only_the_original_target_and_status(status):
    service = "redis-cart" if status == "OOM_KILLED" else "adservice"
    statuses = {service: status, "checkoutservice": "DEGRADED"}
    origin = epoch(statuses=statuses, edges=(("checkoutservice", service),))
    run = ReplanningInvestigation(origin, clock=Clock())
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["current_candidate"]
    result = check(run, origin, candidate, offset=5, statuses=statuses)
    assert result["outcome"] == "consistent"
    assert result["report"]["condition"] == "resource_pressure"
    assert result["report"]["validation_target"] == service


def test_new_failed_transaction_with_different_step_requires_new_reasoning():
    run, _, origin, candidate = setup()
    data = refresh(origin).model_dump(mode="json")
    symptom = next(row["payload"] for row in data["records"]
        if row["payload"]["evidence_id"] == data["symptom_evidence_id"])
    step = next(row for row in symptom["steps"] if row["step"] == "add_cart")
    step.update(success=False, status_code=500)
    result = run.recheck_candidate(FrozenInputs.model_validate(data), candidate_revision=candidate["revision"])
    assert result["reason"] == "transaction_symptom_changed"
    assert result["read_budget"] is None


def test_current_receipt_is_also_withdrawn_on_incident_stop():
    run, _, origin, candidate = setup()
    assert check(run, origin, candidate)["outcome"] == "consistent"
    run.budget.halt("usage_unknown")
    assert run.snapshot()["current_consistency_check"] is None
    assert run.snapshot()["candidate_rechecks"][0]["reason"] == "incident_stopped"


def test_changed_recheck_facts_can_be_used_for_new_reasoning_without_another_refresh():
    run, _, origin, candidate = setup()
    fresh = refresh(origin, statuses={"checkoutservice": "DEGRADED"})
    result = run.recheck_candidate(fresh, candidate_revision=candidate["revision"])
    assert result["reason"] == "scope_state_changed"
    event = run.run_epoch(fresh, FixedProbePolicy())
    assert event["outcome"] == "investigated" and event["report"]["candidate_screened"]
    assert event["report"]["result"]["proposal"]["service_id"] == "checkoutservice"
    assert len(run.snapshot()["candidates"]) == 2
    assert run.snapshot()["candidates"][0]["state"] == "expired"


def test_recheck_ids_remain_immutable_when_a_new_reasoning_epoch_reads_them():
    run, _, origin, candidate = setup()
    fresh = refresh(origin, statuses={"checkoutservice": "DEGRADED"})
    run.recheck_candidate(fresh, candidate_revision=candidate["revision"])
    data = fresh.model_dump(mode="json")
    latest = next(row["payload"] for row in data["records"] if row["kind"] == "component"
        and row["payload"]["service_id"] == "paymentservice"
        and row["payload"]["measured_at"] > origin.as_of.isoformat())
    latest.update(status="DOWN", detail="Built-in health probe: DOWN")
    event = run.run_epoch(FrozenInputs.model_validate(data), FixedProbePolicy())
    assert event["reason"] == "evidence_identity_rewritten" and event["report"] is None
