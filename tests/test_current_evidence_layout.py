"""Current-sample presentation checks; no model, live fault, recovery or token billing."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent.shadow import DriverReply, ShadowRunner
from agent.shadow_context import (CURRENT_EVIDENCE_LAYOUT, EVIDENCE_LAYOUTS, SUPPORTED_EVIDENCE_LAYOUTS,
                                  current_feedback, unpack_view)
from agent.shadow_protocol import CURRENT_SYSTEM, DECISION_SHAPE_RULES
from agent.shadow_provider import GroqShadowDriver
from core.schemas import ObservationRecord
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import CASES, fixture_topology
from eval.shadow_preflight import RecordingPolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import FrozenInputs, FrozenRecord
from tests.test_shadow_replan import BASE, Clock, epoch


def dense_inputs(statuses):
    origin = epoch(statuses=statuses, edges=fixture_topology(BASE).edges)
    records = list(origin.records)
    for row in origin.records:
        if row.kind != "component":
            continue
        for seconds in (5, 10, 15, 20, 25, 30, 35):
            stamp = datetime.fromisoformat(row.payload["measured_at"]) - timedelta(seconds=seconds)
            payload = {**row.payload, "evidence_id": "OBS-" + uuid4().hex,
                       "measured_at": stamp.isoformat(), "collected_at": stamp.isoformat(),
                       "status": "HEALTHY", "quality": "observed", "detail": "Built-in health probe: HEALTHY"}
            payload = ObservationRecord.model_validate(payload).model_dump(mode="json")
            records.append(FrozenRecord(kind="component", stored_at=row.stored_at, payload=payload))
    data = origin.model_dump()
    data["records"] = sorted(records, key=lambda row: row.payload["evidence_id"])
    return FrozenInputs.model_validate(data)


def run(inputs, *, layout=CURRENT_EVIDENCE_LAYOUT, policy=None):
    policy = policy or RecordingPolicy()
    controller = ReplanningInvestigation(inputs, profile="two-epoch-v1", evidence_layout=layout,
                                         investigation_protocol="bounded-coverage-v1", clock=Clock())
    event = controller.run_epoch(inputs, policy)
    return controller, event["report"], policy


def feedback(samples, **summary):
    return {"tool": "component_window", "data": {"samples": samples, "prompt_omitted_samples": 2,
            "summary": summary, "metric": {"minimum": 1, "maximum": 9}}}


def point(seconds, *, status="HEALTHY", identity="a" * 64):
    return {"evidence_id": "OBS-" + uuid4().hex, "measured_at": (BASE + timedelta(seconds=seconds)).isoformat(),
            "collected_at": (BASE + timedelta(seconds=seconds)).isoformat(), "status": status,
            "quality": "unknown" if status == "UNKNOWN" else "observed", "instance_id": identity, "value": 4}


def test_projection_keeps_all_latest_peers_not_the_preferred_status_or_last_list_item():
    samples = [point(10, status="DOWN"), point(0), point(10, status="HEALTHY")]
    source = feedback(samples, latest_conflict=True, latest_truncated=True, unknown_samples=0)
    before = deepcopy(source)
    result = current_feedback(source)
    assert result["data"]["samples"] == [samples[0], samples[2]]
    assert result["data"]["summary"] == source["data"]["summary"]
    assert result["data"]["metric"] == source["data"]["metric"]
    assert result["data"]["prompt_omitted_samples"] == 3
    assert result["data"]["recent_history_status_counts"] == {"HEALTHY": 1}
    assert source == before


def test_omitted_recent_history_counts_unknown_as_unknown():
    source = feedback([point(0, status="UNKNOWN", identity=None), point(1), point(2, status="DOWN")])
    result = current_feedback(source)["data"]
    assert result["recent_history_status_counts"] == {"HEALTHY": 1, "UNKNOWN": 1}
    assert result["prompt_omitted_samples"] == 4 and len(result["samples"]) == 1


@pytest.mark.parametrize("source", [
    {"tool": "component_window", "data": None},
    {"tool": "resource_window", "data": {"samples": [], "prompt_omitted_samples": 0}},
    {"tool": "error_signals", "data": {"signals": ["unchanged"]}},
    {"tool": "transaction_evidence", "data": {"observations": ["unchanged"]}},
    {"tool": "trace_window", "data": {"spans": ["unchanged"]}},
])
def test_projection_does_not_change_empty_failed_transaction_log_or_trace_results(source):
    assert current_feedback(source) == source


def test_naive_time_is_not_silently_compared_or_reinterpreted():
    sample = point(0)
    sample["measured_at"] = "2020-01-01T00:00:00"
    with pytest.raises(ValueError, match="aware"):
        current_feedback(feedback([sample]))


@pytest.mark.parametrize("case", CASES)
def test_dense_current_view_matches_default_fixed_terminal_and_charges_the_same_reads(case):
    inputs = dense_inputs(CASES[case][0])
    old, old_report, _ = run(inputs, layout="time-indexed-v1")
    new, report, policy = run(inputs)
    assert report["result"] == old_report["result"]
    assert report["termination_reason"] in {"proposal", "abstained"}
    assert report["evidence_assessment"] == old_report["evidence_assessment"]
    assert report["session"]["calls_used"] == old_report["session"]["calls_used"]
    assert new.snapshot()["incident_budget"]["limits"] == old.snapshot()["incident_budget"]["limits"]
    assert report["provider_attempts"] == 0 and not report["execution_authorized"]
    view = unpack_view(json.loads(policy.prompts[-1][-1]["content"]))
    assert view["initial"]["evidence_layout"] == CURRENT_EVIDENCE_LAYOUT
    assert "dependency_index" not in view
    assert view["initial"]["topology"]["edges"] == [list(edge) for edge in inputs.topology.edges]
    projected = [f["data"] for turn in report["turns"] for f in turn.get("feedback", []) if f.get("data")]
    assert projected and all(len(data["samples"]) == 1 for data in projected)
    assert all(data["prompt_omitted_samples"] == 7 for data in projected)
    assert len(inputs.records) == 97


def test_existing_layout_and_default_legacy_presentation_are_unchanged():
    assert EVIDENCE_LAYOUTS == ("legacy-v1", "time-indexed-v1")
    assert SUPPORTED_EVIDENCE_LAYOUTS == (*EVIDENCE_LAYOUTS, CURRENT_EVIDENCE_LAYOUT)
    inputs = dense_inputs(CASES["payment"][0])
    _, report, _ = run(inputs, layout="time-indexed-v1")
    data = [f["data"] for turn in report["turns"] for f in turn.get("feedback", []) if f.get("data")]
    assert all(len(row["samples"]) == 8 and "recent_history_status_counts" not in row for row in data)


@pytest.mark.usefixtures("mock_harmony_tokenizer")
def test_native_schema_can_cite_latest_ids_only_and_keeps_exact_named_values():
    inputs = dense_inputs(CASES["payment"][0])
    _, _, policy = run(inputs)
    driver = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)
    view = unpack_view(json.loads(policy.prompts[-1][-1]["content"]))
    shown = {sample["evidence_id"] for turn in view["turns"] for f in turn.get("feedback", [])
             if f.get("data") for sample in f["data"]["samples"]}
    schema = driver._schema(policy.prompts[-1])
    fields = schema["$defs"]["HypothesisProposal"]["properties"]
    assert set(fields["supporting_evidence"]["items"]["enum"]) == shown
    assert fields["service_id"]["enum"] == view["initial"]["registered_services"]
    assert schema["additionalProperties"] is False


def test_omitted_historical_id_cannot_be_used_even_as_a_contradiction():
    inputs = dense_inputs(CASES["payment"][0])
    old = next(row.payload["evidence_id"] for row in inputs.records if row.kind == "component"
               and row.payload["service_id"] == "paymentservice" and row.payload["status"] == "HEALTHY")
    class HistoricalCitation(FixedProbePolicy):
        kind = "scripted"
        def complete(self, messages, **kwargs):
            reply = super().complete(messages, **kwargs)
            data = json.loads(reply.content)
            if data["action"] == "propose":
                data["hypothesis"]["contradicting_evidence"] = [old]
            return DriverReply(json.dumps(data), reply.usage)
    _, report, _ = run(inputs, policy=HistoricalCitation())
    assert report["termination_reason"] == "unshown_citation" and not report["candidate_screened"]


def test_new_profile_requires_structured_dependency_screening():
    from agent.investigation import InvestigationSession
    from eval.shadow_replay import materialize

    inputs = dense_inputs(CASES["payment"][0])
    with materialize(inputs) as path:
        session = InvestigationSession(path, symptom_evidence_id=inputs.symptom_evidence_id, as_of=inputs.as_of)
        with pytest.raises(ValueError, match="structured"):
            ShadowRunner(session, inputs.topology, FixedProbePolicy(), evidence_layout=CURRENT_EVIDENCE_LAYOUT)


def test_current_system_is_explicit_about_loss_and_preserves_closed_decision_shapes():
    assert "not lossless" in CURRENT_SYSTEM and "not this prompt" in CURRENT_SYSTEM
    assert CURRENT_SYSTEM.count(DECISION_SHAPE_RULES) == 1
    assert "paymentservice" not in CURRENT_SYSTEM and "checkoutservice" not in CURRENT_SYSTEM


def test_current_layout_relocalizes_across_epochs_without_resetting_budgets():
    from tests.test_replanning_profile import full_epochs

    first, second = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout=CURRENT_EVIDENCE_LAYOUT,
                                         investigation_protocol="bounded-coverage-v1", clock=Clock())
    for inputs in (first, second):
        controller.run_epoch(inputs, FixedProbePolicy())
    snapshot = controller.snapshot()
    assert [row["proposal"]["service_id"] for row in snapshot["candidates"]] == ["paymentservice", "checkoutservice"]
    assert snapshot["incident_budget"]["calls_used"] == 16
    assert snapshot["incident_budget"]["decisions_used"] == 4
    assert snapshot["candidates"][0]["state"] == "superseded"
    assert not snapshot["execution_authorized"]


@pytest.mark.parametrize("tamper", [False, True])
def test_assessment_binds_current_layout_and_does_not_credit_it_to_the_old_profile(tamper):
    from eval.shadow_replan_assess import assess_replanning
    from tests.test_replanning_profile import full_epochs
    from tests.test_shadow_replan_assess import controls_for

    first, second = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout=CURRENT_EVIDENCE_LAYOUT,
                                         investigation_protocol="bounded-coverage-v1", clock=Clock())
    for inputs in (first, second):
        controller.run_epoch(inputs, FixedProbePolicy())
    snapshot = controller.snapshot()
    controls = controls_for(snapshot)
    controls["investigation_protocol"] = "bounded-coverage-v1"
    if tamper:
        controls["evidence_layout"] = "time-indexed-v1"
    result = assess_replanning(snapshot, controls)
    assert result["diagnostic_control_passed"] is not tamper
    assert not result["execution_authorized"] and not result["recovery_verified"]
