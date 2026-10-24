"""Synthetic multi-epoch software controls; no external inference, Docker or repair."""

from datetime import datetime, timedelta, timezone
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest

from agent.investigation import InvestigationLimits, InvestigationSession
from agent.shadow import DependencySnapshot, DriverReply, ShadowLimits
from agent.shadow_budget import SharedShadowBudget
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import FrozenInputs, FrozenRecord, materialize, save_inputs
from eval.shadow_controls import fixture_topology
from tests.test_shadow_loop import ScriptedDriver

BASE = datetime.now(timezone.utc) - timedelta(seconds=120)
EDGES = (("checkoutservice", "paymentservice"),)


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def test_slow_decision_cannot_restart_the_evidence_freshness_clock():
    clock = Clock()
    class Slow(FixedProbePolicy):
        def complete(self, *args, **kwargs):
            clock.value += 16
            return super().complete(*args, **kwargs)
    first = epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, clock=clock)
    event = controller.run_epoch(first, Slow())
    assert event["report"]["candidate_screened"]
    snapshot = controller.snapshot()
    assert snapshot["current_candidate"] is None
    assert snapshot["candidates"][0]["retirement_reason"] == "freshness_expired"


def epoch(index=0, *, statuses=None, previous=None, edges=EDGES, outcome="failed", instances=None,
          include_history=True, offset=None):
    cutoff = BASE + timedelta(seconds=index * 5 if offset is None else offset)
    stamp = cutoff - timedelta(seconds=1)
    provenance = {"environment_id": REGISTRY.environment_id, "registry_revision": REGISTRY.revision,
                  "registry_digest": REGISTRY.digest}
    identifier = f"SYMOBS-{index + 1:032x}"
    steps = [{"step": name, "success": outcome == "healthy" or name != "checkout",
              "status_code": 500 if outcome != "healthy" and name == "checkout" else 200, "latency_s": 0.01}
             for name in ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]]
    symptom = SymptomObservation(**provenance, evidence_id=identifier, started_at=stamp - timedelta(seconds=1),
        measured_at=stamp, collected_at=stamp, outcome=outcome, quality="unknown" if outcome == "unknown" else "observed",
        steps=steps)
    records = {row.payload["evidence_id"]: row for row in previous.records} if previous and include_history else {}
    records[identifier] = FrozenRecord(kind="symptom", stored_at=stamp.timestamp(), payload=symptom.model_dump(mode="json"))
    for pos, service in enumerate(REGISTRY.by_name):
        status = (statuses or {}).get(service, "HEALTHY")
        record = ObservationRecord(**provenance, schema_version=2, evidence_id=f"OBS-{index * 100 + pos + 1:032x}",
            service_id=service, instance_id=(instances or {}).get(service, f"{pos + 1:064x}"), measured_at=stamp,
            collected_at=stamp, status=status, quality="unknown" if status == "UNKNOWN" else "observed",
            detail=f"Built-in health probe: {status}")
        records[record.evidence_id] = FrozenRecord(kind="component", stored_at=stamp.timestamp(), payload=record.model_dump(mode="json"))
    if outcome != "failed":
        assert previous is not None
        # FrozenInputs is failure-driven: an older failed trigger remains in the bundle.
        identifier = previous.symptom_evidence_id
    topology = DependencySnapshot(source="declared-fixture", revision=f"topology-{index}",
        registry_digest=REGISTRY.digest, captured_at=cutoff, edges=edges)
    return FrozenInputs(source_kind="synthetic", as_of=cutoff, symptom_evidence_id=identifier,
                        topology=topology, records=tuple(records[key] for key in sorted(records)))


def run(origin, fresh, *, clock=None, evidence_limits=None, shadow_limits=None):
    controller = ReplanningInvestigation(origin, clock=clock or Clock(), evidence_limits=evidence_limits,
                                        shadow_limits=shadow_limits)
    a = controller.run_epoch(origin, FixedProbePolicy())
    b = controller.run_epoch(fresh, FixedProbePolicy())
    return controller, a, b


def test_recovered_root_does_not_keep_its_old_candidate_when_checkout_still_fails():
    first = epoch(statuses={"paymentservice": "DOWN", "checkoutservice": "DEGRADED"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    controller, a, b = run(first, second)
    assert a["report"]["result"]["proposal"]["service_id"] == "paymentservice"
    assert b["report"]["result"]["proposal"]["service_id"] == "checkoutservice"
    result = controller.snapshot()
    assert [row["state"] for row in result["candidates"]] == ["superseded", "provisional"]
    assert result["original_symptom"]["evidence_id"] == first.symptom_evidence_id
    assert result["incident_budget"]["calls_used"] == result["incident_budget"]["decisions_used"] == 4
    assert result["incident_budget"]["evidence_elapsed_seconds"] == 5
    assert result["current_candidate"]["epoch"] == 2
    assert not result["execution_authorized"] and not result["recovery_verified"]
    context = b["report"]["initial_context"]
    assert context["symptom"]["evidence_id"] == second.symptom_evidence_id
    assert context["continuation"]["original_symptom_id"] == first.symptom_evidence_id
    assert context["continuation"]["prior_candidates"][0]["state"] == "superseded"


def test_high_cpu_to_degraded_changes_condition_without_carrying_old_citations():
    edges = (("checkoutservice", "adservice"),)
    first = epoch(statuses={"adservice": "HIGH_CPU"}, edges=edges)
    second = epoch(1, statuses={"adservice": "DEGRADED"}, edges=edges, previous=first)
    controller, a, b = run(first, second)
    assert a["report"]["result"]["proposal"]["condition"] == "resource_pressure"
    assert b["report"]["result"]["proposal"]["condition"] == "readiness_failure"
    assert set(a["report"]["result"]["proposal"]["supporting_evidence"]).isdisjoint(
        b["report"]["result"]["proposal"]["supporting_evidence"])
    assert controller.snapshot()["candidates"][0]["retirement_reason"] == "fresh_epoch"


@pytest.mark.parametrize("condition", ["UNKNOWN", "HEALTHY"])
def test_unknown_or_healthy_new_evidence_does_not_inherit_a_prior_failure(condition):
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"paymentservice": condition}, previous=first)
    controller, _, b = run(first, second)
    assert controller.snapshot()["current_candidate"] is None
    assert b["report"]["result"]["outcome"] == "abstained"
    assert not controller.snapshot()["recovery_verified"]


def test_independent_new_anomaly_requires_abstention_not_old_target_reuse():
    edges = EDGES + (("checkoutservice", "currencyservice"),)
    first = epoch(statuses={"paymentservice": "DOWN"}, edges=edges)
    second = epoch(1, statuses={"paymentservice": "DOWN", "currencyservice": "DOWN"}, edges=edges, previous=first)
    controller, _, b = run(first, second)
    assert b["report"]["result"]["reason"] == "unresolved_alternatives"
    assert controller.snapshot()["current_candidate"] is None
    assert controller.snapshot()["incident_budget"]["calls_used"] == 6


@pytest.mark.parametrize("instance", ["f" * 64, None])
def test_runtime_replacement_requires_current_evidence_and_missing_identity_blocks(instance):
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"paymentservice": "DOWN"}, instances={"paymentservice": instance}, previous=first)
    controller, a, b = run(first, second, shadow_limits=ShadowLimits(max_decisions=6))
    assert controller.snapshot()["candidates"][0]["state"] == "superseded"
    if instance is None:
        assert controller.snapshot()["current_candidate"] is None and not b["report"]["candidate_screened"]
    else:
        assert b["report"]["candidate_screened"]
        assert b["report"]["result"]["proposal"]["supporting_evidence"] != a["report"]["result"]["proposal"]["supporting_evidence"]


def test_duplicate_refresh_retires_candidate_and_does_not_reset_any_allowance():
    origin = epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(origin, clock=Clock())
    controller.run_epoch(origin, FixedProbePolicy())
    before = controller.snapshot()["incident_budget"]
    event = controller.run_epoch(origin, FixedProbePolicy())
    after = controller.snapshot()
    assert event["reason"] == "cutoff_not_advanced"
    assert after["current_candidate"] is None and after["candidates"][0]["state"] == "expired"
    for key in ("calls_used", "decisions_used", "response_bytes", "admission_units", "known_reported_tokens"):
        assert after["incident_budget"][key] == before[key]


def test_new_cutoff_without_any_new_fact_does_not_become_a_fresh_investigation():
    first = epoch(statuses={"paymentservice": "DOWN"})
    data = first.model_dump(mode="json")
    data["as_of"] = (first.as_of + timedelta(seconds=1)).isoformat()
    second = FrozenInputs.model_validate(data)
    controller, _, event = run(first, second)
    assert event["reason"] == "no_new_evidence"
    assert controller.snapshot()["current_candidate"] is None


def test_rewritten_existing_observation_id_is_rejected_before_any_new_policy_call():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, previous=first)
    data = second.model_dump(mode="json")
    old = next(row for row in data["records"] if row["kind"] == "component" and row["payload"]["service_id"] == "paymentservice")
    old["payload"].update(status="HEALTHY", detail="Built-in health probe: HEALTHY")
    second = FrozenInputs.model_validate(data)
    controller, _, event = run(first, second)
    assert event["reason"] == "evidence_identity_rewritten" and event["report"] is None
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 2


def test_old_candidate_citation_is_not_issued_if_pruned_from_fresh_epoch():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first, include_history=False)
    controller = ReplanningInvestigation(first, clock=Clock())
    a = controller.run_epoch(first, FixedProbePolicy())
    old = a["report"]["result"]["proposal"]
    driver = ScriptedDriver([{"action": "propose", "hypothesis": old}])
    event = controller.run_epoch(second, driver)
    assert event["report"]["termination_reason"] == "unshown_citation"
    assert controller.snapshot()["current_candidate"] is None


def test_decision_budget_is_shared_across_fresh_sessions():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    controller, _, b = run(first, second, shadow_limits=ShadowLimits(max_decisions=2))
    assert b["report"]["termination_reason"] == "decision_budget_exhausted"
    assert b["report"]["attempts"] == []
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 2
    assert controller.snapshot()["current_candidate"] is None


def test_tool_budget_is_shared_even_when_policy_can_still_explicitly_abstain():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    controller, _, b = run(first, second, evidence_limits=InvestigationLimits(max_tool_calls=2))
    assert b["report"]["result"]["outcome"] == "abstained"
    assert controller.snapshot()["incident_budget"]["calls_used"] == 2
    assert controller.snapshot()["current_candidate"] is None


def test_reported_usage_is_shared_and_not_reintroduced_by_a_fresh_epoch():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, previous=first)
    controller = ReplanningInvestigation(first, clock=Clock(), shadow_limits=ShadowLimits(max_reported_tokens=120))
    driver = ScriptedDriver([{"action": "abstain", "reason": "insufficient_evidence"}])
    controller.run_epoch(first, driver)
    event = controller.run_epoch(second, ScriptedDriver([]))
    assert event["report"]["termination_reason"] == "admission_budget_exhausted"
    assert controller.snapshot()["incident_budget"]["known_reported_tokens"] == 120
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 1


@pytest.mark.parametrize("reply", [RuntimeError("PRIVATE-FAILURE"), DriverReply("{}", None)])
def test_unknown_usage_blocks_later_epochs_without_retry_or_raw_error(reply):
    first, clock = epoch(), Clock()
    second = epoch(1, previous=first)
    controller = ReplanningInvestigation(first, clock=clock)
    controller.run_epoch(first, ScriptedDriver([reply]))
    event = controller.run_epoch(second, ScriptedDriver([]))
    result = controller.snapshot()
    assert event["reason"] == "usage_unknown" and event["report"] is None
    assert result["incident_budget"]["decisions_used"] == 1 and not result["incident_budget"]["usage_complete"]
    assert "PRIVATE" not in json.dumps(result)


def test_interrupt_keeps_charged_attempt_and_prevents_continuation_with_unknown_usage():
    class Interrupted:
        kind = "scripted"
        def complete(self, *args, **kwargs):
            raise KeyboardInterrupt()
    first = epoch()
    controller = ReplanningInvestigation(first, clock=Clock())
    with pytest.raises(KeyboardInterrupt):
        controller.run_epoch(first, Interrupted())
    snapshot = controller.snapshot()
    assert snapshot["epochs"][0]["outcome"] == "interrupted"
    assert snapshot["incident_budget"]["decisions_used"] == 1
    assert snapshot["incident_budget"]["stop_reason"] == "usage_unknown"
    assert not snapshot["incident_budget"]["decision_in_flight"]


def test_wall_time_and_evidence_time_cannot_refund_each_other_on_refresh():
    clock = Clock()
    budget = SharedShadowBudget(InvestigationLimits(), ShadowLimits(), clock=clock)
    budget.advance_evidence_time(10)
    clock.value += 15
    before = budget.remaining_seconds()
    budget.advance_evidence_time(11)
    assert budget.remaining_seconds() == before == 155
    clock.value += 5
    assert budget.remaining_seconds() == 150


def test_expired_current_candidate_is_removed_without_waiting_for_refresh():
    clock, first = Clock(), epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, clock=clock)
    controller.run_epoch(first, FixedProbePolicy())
    assert controller.snapshot()["current_candidate"]
    clock.value += 30
    snapshot = controller.snapshot()
    assert snapshot["current_candidate"] is None and snapshot["candidates"][0]["retirement_reason"] == "freshness_expired"


@pytest.mark.parametrize("outcome", ["healthy", "unknown"])
def test_newer_nonfailed_transaction_blocks_the_older_failed_trigger_without_claiming_recovery(outcome):
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, previous=first, outcome=outcome)
    controller, _, event = run(first, second)
    assert event["reason"] == "latest_failed_trigger_required"
    assert controller.snapshot()["current_candidate"] is None
    assert not controller.snapshot()["recovery_verified"]


def test_topology_change_needs_new_revision_and_current_capture():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, previous=first, edges=EDGES + (("checkoutservice", "currencyservice"),))
    data = second.model_dump(mode="json")
    data["topology"]["revision"] = first.topology.revision
    controller, _, event = run(first, FrozenInputs.model_validate(data))
    assert event["reason"] == "topology_revision_required"
    assert controller.snapshot()["current_candidate"] is None


def test_trace_scope_cannot_be_silently_enabled_between_epochs():
    first = epoch()
    data = epoch(1, previous=first).model_dump(mode="json")
    data["schema_version"] = 2
    controller, _, event = run(first, FrozenInputs.model_validate(data))
    assert event["reason"] == "input_profile_changed"
    assert controller.snapshot()["current_candidate"] is None


def test_unapproved_model_never_gets_called_in_new_replanning_path():
    class Never:
        kind = "model"
        def complete(self, *args, **kwargs):
            pytest.fail("No external trial is authorized by this software test")
    first = epoch()
    controller = ReplanningInvestigation(first, clock=Clock())
    assert controller.run_epoch(first, Never())["reason"] == "external_transfer_not_approved"
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 0


def test_snapshot_and_returned_event_cannot_mutate_private_controller_history():
    first = epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, clock=Clock())
    event = controller.run_epoch(first, FixedProbePolicy())
    event["report"]["result"].clear()
    snapshot = controller.snapshot()
    snapshot["candidates"].clear()
    assert controller.snapshot()["current_candidate"]["proposal"]["service_id"] == "paymentservice"


def test_rejected_epochs_are_bounded_and_exhaustion_cannot_reset_budget():
    first = epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, max_epochs=2, clock=Clock())
    controller.run_epoch(first, FixedProbePolicy())
    controller.run_epoch(first, FixedProbePolicy())
    event = controller.run_epoch(epoch(1, previous=first), FixedProbePolicy())
    assert event["reason"] == "epoch_budget_exhausted"
    snapshot = controller.snapshot()
    assert len(snapshot["epochs"]) == 2 and snapshot["incident_budget"]["decisions_used"] == 2
    assert snapshot["current_candidate"] is None


def test_existing_single_epoch_session_still_has_no_shared_budget_or_history():
    first = epoch()
    with materialize(first) as path:
        session = InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id, as_of=first.as_of)
        assert session.budget is None and "continuation" not in session.initial_context()


def test_cli_is_offline_private_exclusive_and_preserves_epoch_outcomes(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN", "checkoutservice": "DEGRADED"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    a, b, out = (tmp_path / name for name in ("a.json", "b.json", "report.json"))
    save_inputs(a, first)
    save_inputs(b, second)
    args = [sys.executable, "-m", "eval.shadow_replan", "--inputs", str(a), "--inputs", str(b), "--output", str(out)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["real_model_calls"] == 0 and summary["candidate_states"] == ["superseded", "provisional"]
    assert out.stat().st_mode & 0o777 == 0o600
    repeat = subprocess.run(args, capture_output=True, text=True)
    assert repeat.returncode != 0


def test_imports_no_model_settings_docker_or_execution_modules():
    code = """
import sys
import eval.shadow_replan
for name in ('core.config', 'docker', 'openai', 'agent.nodes.reasoner', 'agent.tools.sandbox_tools'):
    assert name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr


def test_historical_retirement_is_not_rewritten_by_a_later_rejected_refresh():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, previous=first)
    controller, _, _ = run(first, second)
    before = controller.snapshot()["candidates"][0]
    assert before["state"] == "superseded"
    controller.run_epoch(second, FixedProbePolicy())
    assert controller.snapshot()["candidates"][0] == before


def test_evidence_clock_waits_count_toward_the_global_deadline_even_in_instant_replay():
    first = epoch(statuses={"paymentservice": "DOWN"}, offset=-200)
    second = epoch(1, previous=first, offset=0)
    controller, _, event = run(first, second)
    assert event["reason"] == "time_exhausted" and event["report"] is None
    snapshot = controller.snapshot()
    assert snapshot["incident_budget"]["decisions_used"] == 2
    assert snapshot["current_candidate"] is None and snapshot["candidates"][0]["retirement_reason"] == "time_exhausted"


def test_processing_wait_alone_exhausts_deadline_and_cannot_be_refunded_by_new_evidence():
    clock, first = Clock(), epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, clock=clock)
    controller.run_epoch(first, FixedProbePolicy())
    clock.value += 181
    second = epoch(1, previous=first)
    event = controller.run_epoch(second, ScriptedDriver([]))
    assert event["reason"] == "time_exhausted"
    assert controller.snapshot()["incident_budget"]["remaining_seconds"] == 0


def test_context_bytes_remain_spent_after_a_new_epoch_constructor():
    first = epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, clock=Clock())
    controller.run_epoch(first, FixedProbePolicy())
    before = controller.snapshot()["incident_budget"]
    remaining = controller.evidence_limits.max_total_bytes - before["response_bytes"] - 100
    assert controller.budget.charge_bytes(remaining)
    event = controller.run_epoch(epoch(1, previous=first), ScriptedDriver([]))
    assert event["outcome"] == "rejected"
    after = controller.snapshot()["incident_budget"]
    assert after["response_bytes"] == controller.evidence_limits.max_total_bytes - 100
    assert after["decisions_used"] == before["decisions_used"]


def test_global_admission_limit_is_checked_before_any_provider_rate_wait():
    class Waiting(ScriptedDriver):
        def wait_for_admission(self, **kwargs):
            pytest.fail("Spent incident admission must be checked before waiting")
    first, clock = epoch(), Clock()
    controller = ReplanningInvestigation(first, clock=clock)
    budget = controller.budget
    assert budget.claim_decision(controller.shadow_limits.max_admission_units - 1) is None
    budget.record_usage({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    event = controller.run_epoch(first, Waiting([]))
    assert event["report"]["termination_reason"] == "admission_budget_exhausted"
    assert budget.snapshot()["decisions_used"] == 1


def test_rate_wait_consumes_the_shared_deadline_before_dispatch():
    clock, first = Clock(), epoch()
    class Waiting(ScriptedDriver):
        def wait_for_admission(self, **kwargs):
            clock.value += 181
            return True
    controller = ReplanningInvestigation(first, clock=clock)
    event = controller.run_epoch(first, Waiting([]))
    assert event["report"]["termination_reason"] == "time_exhausted"
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 0


@pytest.mark.parametrize("bad_clock", [True, float("nan"), float("inf"), "1"])
def test_bad_initial_clock_is_rejected(bad_clock):
    with pytest.raises(ValueError):
        ReplanningInvestigation(epoch(), clock=lambda: bad_clock)


@pytest.mark.parametrize("bad_clock", [99, True, float("nan"), float("inf"), "1"])
def test_invalid_or_rollback_clock_latches_a_stop_without_more_calls(bad_clock):
    clock = Clock()
    budget = SharedShadowBudget(InvestigationLimits(), ShadowLimits(), clock=clock)
    clock.value = bad_clock
    assert budget.claim_tool() == "clock_invalid"
    clock.value = 110
    assert budget.claim_decision(10) == "clock_invalid"
    assert budget.snapshot()["decisions_used"] == budget.snapshot()["calls_used"] == 0


@pytest.mark.parametrize("bad_usage", [{"input_tokens": True, "output_tokens": 0, "total_tokens": 1},
                                      {"input_tokens": 1, "output_tokens": 1, "total_tokens": 1}, {}])
def test_invalid_usage_cannot_clear_inflight_state_and_continue(bad_usage):
    budget = SharedShadowBudget(InvestigationLimits(), ShadowLimits(), clock=Clock())
    assert budget.claim_decision(100) is None
    with pytest.raises(ValueError):
        budget.record_usage(bad_usage)
    assert budget.claim_decision(100) == "usage_unknown"
    assert not budget.snapshot()["usage_complete"] and not budget.snapshot()["decision_in_flight"]


def test_reported_token_overrun_is_retained_and_blocks_fresh_epoch():
    first = epoch()
    limits = ShadowLimits(max_reported_tokens=100)
    controller = ReplanningInvestigation(first, shadow_limits=limits, clock=Clock())
    event = controller.run_epoch(first, ScriptedDriver([{"action": "abstain", "reason": "insufficient_evidence"}]))
    assert event["report"]["termination_reason"] == "reported_token_limit"
    assert controller.snapshot()["incident_budget"]["known_reported_tokens"] == 120
    assert controller.run_epoch(epoch(1, previous=first), ScriptedDriver([]))["reason"] == "reported_token_limit"


def test_mismatched_limits_cannot_reset_a_shared_incident():
    first, clock = epoch(), Clock()
    budget = SharedShadowBudget(InvestigationLimits(max_tool_calls=2), ShadowLimits(), clock=clock)
    with materialize(first) as path, pytest.raises(ValueError, match="same incident limits"):
        InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id, as_of=first.as_of,
                             limits=InvestigationLimits(), budget=budget, clock=clock)


@pytest.mark.parametrize("value", [True, 0, 5, "2"])
def test_epoch_limits_are_strict_and_bounded(value):
    with pytest.raises(ValueError):
        ReplanningInvestigation(epoch(), max_epochs=value)


def test_wrong_origin_cannot_start_an_incident_with_a_relocalized_trigger():
    origin = epoch(statuses={"paymentservice": "DOWN"})
    other = epoch(1, previous=origin)
    controller = ReplanningInvestigation(origin, clock=Clock())
    event = controller.run_epoch(other, FixedProbePolicy())
    assert event["reason"] == "origin_epoch_required" and event["report"] is None
    assert controller.snapshot()["incident_budget"]["calls_used"] == 0


def test_same_frozen_epochs_have_identical_initial_context_for_two_deterministic_policies():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    a, _, _ = run(first, second)
    b, _, _ = run(first, second)
    for left, right in zip(a.snapshot()["epochs"], b.snapshot()["epochs"]):
        assert left["input_digest"] == right["input_digest"]
        assert left["report"]["initial_context_digest"] == right["report"]["initial_context_digest"]
        assert left["report"]["result"] == right["report"]["result"]


def test_cli_preserves_partial_results_and_withdraws_candidate_when_later_input_is_invalid(tmp_path):
    first = epoch(statuses={"paymentservice": "DOWN"})
    path, bad, output = (tmp_path / name for name in ("first.json", "bad.json", "out.json"))
    save_inputs(path, first)
    bad.write_text('"PRIVATE-INVALID"')
    result = subprocess.run([sys.executable, "-m", "eval.shadow_replan", "--inputs", str(path),
        "--inputs", str(bad), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 1 and "PRIVATE" not in result.stdout + result.stderr
    receipt = json.loads(output.read_text())
    assert len(receipt["epochs"]) == 1 and receipt["current_candidate"] is None
    assert receipt["candidates"][0]["state"] == "expired"
    assert receipt["cli_failure"] == {"epoch": 2, "reason": "input_unavailable_or_rejected"}
    assert output.with_name("out.json.started.json").exists()


def test_tool_claims_are_atomic_across_callers_of_one_process_local_budget():
    budget = SharedShadowBudget(InvestigationLimits(), ShadowLimits(), clock=Clock())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: budget.claim_tool(), range(40)))
    assert results.count(None) == 8 and results.count("budget_exhausted") == 32
    assert budget.snapshot()["calls_used"] == 8


def test_only_one_charged_decision_can_be_inflight_and_usage_is_settled_once():
    budget = SharedShadowBudget(InvestigationLimits(), ShadowLimits(), clock=Clock())
    assert budget.claim_decision(100) is None
    assert budget.claim_decision(100) == "decision_in_flight"
    budget.record_usage({"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})
    with pytest.raises(ValueError):
        budget.record_usage({"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})
    assert budget.claim_decision(100) is None
    budget.record_usage({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    assert budget.snapshot()["known_reported_tokens"] == 20


@pytest.mark.parametrize("approved", [True, "yes", 1])
def test_trace_bearing_model_needs_both_real_boolean_approval_flags(approved):
    class Never:
        kind = "model"
        def complete(self, *args, **kwargs):
            pytest.fail("No real API request is authorized by this test")
    data = epoch().model_dump(mode="json")
    data["schema_version"] = 2
    first = FrozenInputs.model_validate(data)
    controller = ReplanningInvestigation(first, clock=Clock())
    event = controller.run_epoch(first, Never(), approve_external=approved)
    assert event["reason"] in {"external_transfer_not_approved", "explicit_transfer_flags_required"}
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 0


def test_conflicting_latest_component_samples_do_not_form_a_replacement_candidate():
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"paymentservice": "DOWN"}, previous=first)
    data = second.model_dump(mode="json")
    latest = [row for row in data["records"] if row["kind"] == "component" and row["payload"]["service_id"] == "paymentservice"][-1]
    changed = json.loads(json.dumps(latest))
    changed["payload"].update(evidence_id="OBS-" + "f" * 32, status="HEALTHY", detail="Built-in health probe: HEALTHY")
    data["records"].append(changed)
    data["records"].sort(key=lambda row: row["payload"]["evidence_id"])
    controller, _, event = run(first, FrozenInputs.model_validate(data))
    assert event["report"]["result"]["outcome"] == "abstained"
    assert controller.snapshot()["current_candidate"] is None


def test_first_invalid_cli_input_does_not_leak_validation_payload(tmp_path):
    first = epoch()
    path, output = tmp_path / "bad.json", tmp_path / "report.json"
    save_inputs(path, first)
    data = json.loads(path.read_text())
    data["inputs"]["unexpected"] = "PRIVATE-VALIDATION-CONTENT"
    path.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, "-m", "eval.shadow_replan", "--inputs", str(path), "--output", str(output)],
                             capture_output=True, text=True)
    assert result.returncode == 1 and "PRIVATE" not in result.stdout + result.stderr
    assert result.stderr == "" and not output.exists()
    assert json.loads(result.stdout)["reason"] == "origin_input_unavailable_or_rejected"


def test_start_marker_refuses_reuse_even_if_previous_process_wrote_no_final_report(tmp_path):
    path, output = tmp_path / "inputs.json", tmp_path / "report.json"
    save_inputs(path, epoch())
    marker = output.with_name("report.json.started.json")
    marker.write_text('{"previous":"interrupted"}')
    result = subprocess.run([sys.executable, "-m", "eval.shadow_replan", "--inputs", str(path), "--output", str(output)],
                             capture_output=True, text=True)
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout)["reason"] == "output_unavailable_or_reused"
    assert marker.read_text() == '{"previous":"interrupted"}' and not output.exists()


def test_original_symptom_can_age_past_ninety_seconds_without_replacing_incident_identity():
    first = epoch(statuses={"paymentservice": "DOWN"}, offset=-95)
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first, offset=0)
    controller, _, b = run(first, second)
    assert b["report"]["candidate_screened"]
    assert controller.snapshot()["current_candidate"]["proposal"]["service_id"] == "checkoutservice"
    assert controller.snapshot()["original_symptom"]["evidence_id"] == first.symptom_evidence_id
    assert b["report"]["initial_context"]["symptom"]["evidence_id"] == second.symptom_evidence_id


def test_full_boutique_scope_does_not_silently_gain_new_budgets_for_replanning():
    first = epoch(statuses={"paymentservice": "DOWN"}, edges=fixture_topology(BASE).edges)
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first, edges=first.topology.edges)
    controller, a, b = run(first, second)
    assert a["report"]["candidate_screened"]
    assert b["report"]["termination_reason"] == "decision_budget_exhausted"
    assert controller.snapshot()["incident_budget"]["calls_used"] == 8
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 4
    assert controller.snapshot()["current_candidate"] is None


def test_mocked_native_model_profile_can_replan_with_same_shared_limits_and_sanitized_history():
    class MockModel:
        kind = "model"
        structured_decisions = True
        def estimate_input_tokens(self, messages):
            return 1000
        def complete(self, messages, **kwargs):
            decision = json.loads(FixedProbePolicy().complete(messages).content)
            return DriverReply(json.dumps({"decision": decision}),
                               {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}, "stop")
    first = epoch(statuses={"paymentservice": "DOWN"})
    second = epoch(1, statuses={"checkoutservice": "DEGRADED"}, previous=first)
    controller = ReplanningInvestigation(first, clock=Clock())
    a = controller.run_epoch(first, MockModel(), approve_external=True)
    b = controller.run_epoch(second, MockModel(), approve_external=True)
    assert a["report"]["candidate_screened"] and b["report"]["candidate_screened"]
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 4
    assert controller.snapshot()["incident_budget"]["known_reported_tokens"] == 440
    assert not controller.snapshot()["execution_authorized"]


def test_direct_broker_proposal_cannot_ignore_a_shared_unknown_usage_stop():
    first, clock = epoch(), Clock()
    budget = SharedShadowBudget(InvestigationLimits(), ShadowLimits(), clock=clock)
    assert budget.claim_decision(100) is None
    budget.record_usage(None)
    with materialize(first) as path:
        session = InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id,
                                       as_of=first.as_of, budget=budget, clock=clock)
        result = session.propose({"service_id": "frontend", "condition": "request_failure",
                                  "supporting_evidence": [first.symptom_evidence_id]})
    assert result["outcome"] == "usage_unknown" and not result["execution_authorized"]
