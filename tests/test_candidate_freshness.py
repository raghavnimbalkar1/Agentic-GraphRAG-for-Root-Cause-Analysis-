"""Synthetic candidate-lifetime controls; no provider, Docker or repair calls."""

from copy import deepcopy
from datetime import timedelta

import pytest

from core.schemas import ObservationRecord, SymptomObservation
from agent.evidence_policy import scope_evidence_deadline
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import FrozenInputs
from tests.test_shadow_replan import Clock, epoch

EDGES = (("checkoutservice", "paymentservice"), ("checkoutservice", "currencyservice"))


def aged_inputs(*, service_ages=None, symptom_age=1):
    inputs = epoch(statuses={"paymentservice": "DOWN"}, edges=EDGES)
    data = inputs.model_dump(mode="json")
    for row in data["records"]:
        payload = row["payload"]
        age = symptom_age if row["kind"] == "symptom" else (service_ages or {}).get(payload["service_id"], 1)
        stamp = inputs.as_of - timedelta(seconds=age)
        payload.update(measured_at=stamp.isoformat(), collected_at=stamp.isoformat())
        if row["kind"] == "symptom":
            payload["started_at"] = (stamp - timedelta(seconds=1)).isoformat()
        model = SymptomObservation if row["kind"] == "symptom" else ObservationRecord
        row["payload"] = model.model_validate(payload).model_dump(mode="json")
        row["stored_at"] = stamp.timestamp()
    return FrozenInputs.model_validate(data)


@pytest.mark.parametrize("service", ["checkoutservice", "currencyservice", "paymentservice"])
@pytest.mark.parametrize("age", [20, 29, 30])
def test_candidate_expires_at_earliest_required_component_not_just_target(service, age):
    inputs = aged_inputs(service_ages={service: age})
    clock = Clock()
    controller = ReplanningInvestigation(inputs, clock=clock)
    event = controller.run_epoch(inputs, FixedProbePolicy())
    assert event["report"]["candidate_screened"]
    until = inputs.as_of + timedelta(seconds=30 - age)
    snapshot = controller.snapshot()
    assert snapshot["candidates"][0]["valid_until"] == until.isoformat()
    if age < 30:
        assert snapshot["current_candidate"] is not None
        clock.value += 30 - age
    snapshot = controller.snapshot()
    assert snapshot["current_candidate"] is None
    assert snapshot["candidates"][0]["retirement_reason"] == "freshness_expired"
    assert not snapshot["execution_authorized"]


@pytest.mark.parametrize("age", [80, 89, 90])
def test_candidate_cannot_outlive_its_failed_transaction(age):
    inputs = aged_inputs(symptom_age=age)
    clock = Clock()
    controller = ReplanningInvestigation(inputs, clock=clock)
    event = controller.run_epoch(inputs, FixedProbePolicy())
    assert event["report"]["candidate_screened"]
    until = inputs.as_of + timedelta(seconds=90 - age)
    snapshot = controller.snapshot()
    assert snapshot["candidates"][0]["valid_until"] == until.isoformat()
    if age < 90:
        assert snapshot["current_candidate"] is not None
        clock.value += 90 - age
    snapshot = controller.snapshot()
    assert snapshot["current_candidate"] is None
    assert snapshot["candidates"][0]["retirement_reason"] == "freshness_expired"


def test_old_unrelated_component_does_not_shorten_candidate_lifetime():
    inputs = aged_inputs(service_ages={"adservice": 60})
    clock = Clock()
    controller = ReplanningInvestigation(inputs, clock=clock)
    controller.run_epoch(inputs, FixedProbePolicy())
    candidate = controller.snapshot()["current_candidate"]
    assert candidate is not None
    assert candidate["valid_until"] == (inputs.as_of + timedelta(seconds=29)).isoformat()


def test_provider_wait_cannot_leave_candidate_current_after_control_sample_expires():
    inputs = aged_inputs(service_ages={"currencyservice": 29})
    clock = Clock()

    class SlowPolicy(FixedProbePolicy):
        def complete(self, *args, **kwargs):
            clock.value += 1
            return super().complete(*args, **kwargs)

    controller = ReplanningInvestigation(inputs, clock=clock)
    event = controller.run_epoch(inputs, SlowPolicy())
    assert event["report"]["candidate_screened"]
    snapshot = controller.snapshot()
    assert snapshot["current_candidate"] is None
    assert snapshot["candidates"][0]["retirement_reason"] == "freshness_expired"
    assert snapshot["incident_budget"]["decisions_used"] == 2


def test_old_history_does_not_override_latest_required_observation():
    inputs = aged_inputs()
    data = inputs.model_dump(mode="json")
    history = deepcopy(next(row for row in data["records"] if row["kind"] == "component"
        and row["payload"]["service_id"] == "currencyservice"))
    stamp = inputs.as_of - timedelta(seconds=29)
    history["payload"].update(evidence_id="OBS-" + "f" * 32, measured_at=stamp, collected_at=stamp)
    history["payload"] = ObservationRecord.model_validate(history["payload"]).model_dump(mode="json")
    history["stored_at"] = stamp.timestamp()
    data["records"].append(history)
    data["records"].sort(key=lambda row: row["payload"]["evidence_id"])
    controller = ReplanningInvestigation(FrozenInputs.model_validate(data), clock=Clock())
    controller.run_epoch(FrozenInputs.model_validate(data), FixedProbePolicy())
    assert controller.snapshot()["current_candidate"]["valid_until"] == (
        inputs.as_of + timedelta(seconds=29)).isoformat()


@pytest.mark.parametrize("fault", ["missing", "unknown", "conflict", "missing_instance"])
def test_incomplete_or_uncertain_scope_cannot_supply_a_freshness_deadline(fault):
    inputs = aged_inputs()
    controller = ReplanningInvestigation(inputs, clock=Clock())
    report = controller.run_epoch(inputs, FixedProbePolicy())["report"]
    turns = deepcopy(report["turns"])
    for turn in turns:
        for feedback in turn.get("feedback", []):
            if (feedback.get("request") or {}).get("service_id") != "currencyservice":
                continue
            if fault == "missing":
                feedback["outcome"] = "unavailable"
            elif fault == "conflict":
                feedback["data"]["summary"]["latest_conflict"] = True
            else:
                for sample in feedback["data"]["samples"]:
                    if fault == "unknown":
                        sample.update(status="UNKNOWN", quality="unknown")
                    else:
                        sample["instance_id"] = None
    with pytest.raises(ValueError, match="complete known scope"):
        scope_evidence_deadline(report["initial_context"], turns)


@pytest.mark.parametrize("profile,layout,protocol", [
    ("legacy", "legacy-v1", "model-directed-v1"),
    ("two-epoch-v1", "legacy-v1", "bounded-coverage-v1"),
    ("two-epoch-v1", "time-indexed-v1", "bounded-coverage-v1"),
])
def test_expiry_rule_is_independent_of_layout_and_read_scheduling(profile, layout, protocol):
    inputs = aged_inputs(service_ages={"currencyservice": 29})
    clock = Clock()
    controller = ReplanningInvestigation(inputs, clock=clock, profile=profile,
        evidence_layout=layout, investigation_protocol=protocol)
    event = controller.run_epoch(inputs, FixedProbePolicy())
    assert event["report"]["candidate_screened"]
    clock.value += 1
    assert controller.snapshot()["current_candidate"] is None
    assert controller.snapshot()["candidates"][0]["retirement_reason"] == "freshness_expired"
