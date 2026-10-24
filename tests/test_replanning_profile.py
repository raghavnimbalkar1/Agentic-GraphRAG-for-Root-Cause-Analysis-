"""Explicit cost-profile controls, not live diagnosis or efficiency measurements."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import sys

import pytest

from agent.investigation import InvestigationLimits, InvestigationSession
from agent.shadow import DriverReply, ShadowLimits
from agent.shadow_budget import SharedShadowBudget, epoch_limits
from agent.shadow_context import unpack_view
from core.diagnostics import ComponentReader, DiagnosticRequest
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import fixture_topology
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import FrozenInputs, FrozenRecord, materialize, save_inputs
from tests.test_shadow_replan import BASE, Clock, epoch


def full_epochs(*, after=None, **kwargs):
    first = epoch(statuses={"paymentservice": "DOWN", "checkoutservice": "DEGRADED"},
                  edges=fixture_topology(BASE).edges)
    second = epoch(1, statuses=after if after is not None else {"checkoutservice": "DEGRADED"},
                   edges=first.topology.edges, previous=first, **kwargs)
    return first, second


def run_profile(first, second, *, profile="two-epoch-v1", policy=None, clock=None):
    controller = ReplanningInvestigation(first, profile=profile, clock=clock or Clock())
    a = controller.run_epoch(first, policy or FixedProbePolicy())
    b = controller.run_epoch(second, policy or FixedProbePolicy())
    return controller, a, b


def test_frozen_profile_has_explicit_global_and_per_epoch_limits():
    evidence, shadow = epoch_limits("two-epoch-v1")
    budget = SharedShadowBudget(evidence, shadow, profile="two-epoch-v1", clock=Clock())
    assert budget.snapshot()["limits"] == {"max_tool_calls": 16, "max_decisions": 8,
        "max_total_bytes": 262144, "max_admission_units": 262144,
        "max_reported_tokens": 65536, "max_seconds": 720}
    assert evidence.max_tool_calls == 8 and evidence.max_total_bytes == 131072 and evidence.max_seconds == 360
    assert shadow.max_decisions == 4 and shadow.max_reported_tokens == 32768 and shadow.max_seconds == 360
    with pytest.raises(TypeError):
        budget.caps["max_tool_calls"] = 100


def test_full_boutique_replans_on_fresh_evidence_with_sixteen_individually_charged_reads():
    first, second = full_epochs()
    controller, a, b = run_profile(first, second)
    assert a["report"]["candidate_screened"] and b["report"]["candidate_screened"]
    snapshot = controller.snapshot()
    assert [row["proposal"]["service_id"] for row in snapshot["candidates"]] == ["paymentservice", "checkoutservice"]
    assert snapshot["incident_budget"]["calls_used"] == 16
    assert snapshot["incident_budget"]["decisions_used"] == 8
    assert [len(row["report"]["session"]["history"]) for row in snapshot["epochs"]] == [8, 8]
    assert [len(row["report"]["attempts"]) for row in snapshot["epochs"]] == [4, 4]
    assert all(item["tool"] == "component_window" for row in snapshot["epochs"]
               for item in row["report"]["session"]["history"])
    assert snapshot["original_symptom"]["evidence_id"] == first.symptom_evidence_id
    assert snapshot["current_candidate"]["epoch"] == 2 and not snapshot["execution_authorized"]


def test_legacy_default_still_stops_on_identical_inputs_and_does_not_switch_profile():
    first, second = full_epochs()
    controller, a, b = run_profile(first, second, profile="legacy")
    assert a["report"]["candidate_screened"]
    assert b["report"]["termination_reason"] == "decision_budget_exhausted"
    assert controller.snapshot()["incident_budget"]["calls_used"] == 8
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 4
    assert controller.snapshot()["profile"] == "legacy"
    assert controller.snapshot()["current_candidate"] is None


@pytest.mark.parametrize("after,expected", [
    ({"paymentservice": "UNKNOWN"}, "insufficient_evidence"),
    ({"paymentservice": "DOWN", "currencyservice": "DOWN"}, "unresolved_alternatives"),
    ({}, "unknown_condition"),
])
def test_bigger_allowance_does_not_weaken_abstention_or_prove_recovery(after, expected):
    first, second = full_epochs(after=after)
    controller, _, b = run_profile(first, second)
    assert b["report"]["result"]["outcome"] == "abstained" and b["report"]["result"]["reason"] == expected
    assert controller.snapshot()["current_candidate"] is None
    assert not controller.snapshot()["recovery_verified"]


def test_same_tool_fixed_policies_receive_identical_profile_and_context_on_each_epoch():
    class Recording(FixedProbePolicy):
        def __init__(self):
            self.views = []
        def complete(self, messages, **kwargs):
            self.views.append(unpack_view(json.loads(messages[-1]["content"])))
            return super().complete(messages, **kwargs)
    first, second = full_epochs()
    fixed, candidate = Recording(), Recording()
    a, _, _ = run_profile(first, second, policy=fixed)
    b, _, _ = run_profile(first, second, policy=candidate)
    assert fixed.views == candidate.views
    assert fixed.views[0]["initial"]["budget_profile"]["name"] == "two-epoch-v1"
    assert fixed.views[0]["remaining_tool_calls"] == 8
    assert fixed.views[0]["incident_remaining"]["tool_calls"] == 16
    assert fixed.views[4]["incident_remaining"]["tool_calls"] == 8
    for left, right in zip(a.snapshot()["epochs"], b.snapshot()["epochs"]):
        assert left["input_digest"] == right["input_digest"]
        assert left["report"]["initial_context_digest"] == right["report"]["initial_context_digest"]


def test_mocked_native_model_path_uses_same_eight_decision_sixteen_read_ceiling():
    class MockModel:
        kind = "model"
        structured_decisions = True
        def estimate_input_tokens(self, messages):
            return 1000
        def complete(self, messages, **kwargs):
            decision = json.loads(FixedProbePolicy().complete(messages).content)
            return DriverReply(json.dumps({"decision": decision}),
                               {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}, "stop")
    first, second = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=Clock())
    a = controller.run_epoch(first, MockModel(), approve_external=True)
    b = controller.run_epoch(second, MockModel(), approve_external=True)
    assert a["report"]["candidate_screened"] and b["report"]["candidate_screened"]
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 8
    assert controller.snapshot()["incident_budget"]["known_reported_tokens"] == 880


@pytest.mark.parametrize("kwargs", [
    {"profile": "automatic"}, {"profile": True}, {"profile": "two-epoch-v1", "max_epochs": 3},
    {"profile": "two-epoch-v1", "max_epochs": 1},
    {"profile": "two-epoch-v1", "evidence_limits": InvestigationLimits()},
    {"profile": "two-epoch-v1", "shadow_limits": ShadowLimits()},
    {"profile": "two-epoch-v1", "shadow_limits": ShadowLimits(max_decisions=6, max_seconds=360, max_reported_tokens=32768)},
])
def test_unknown_or_mutated_protocol_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ReplanningInvestigation(epoch(), clock=Clock(), **kwargs)


def test_no_third_epoch_or_refund_is_allowed_by_the_two_epoch_profile():
    first, second = full_epochs()
    controller, _, _ = run_profile(first, second)
    before = controller.snapshot()["incident_budget"]
    event = controller.run_epoch(epoch(2, previous=second, edges=first.topology.edges), FixedProbePolicy())
    assert event["reason"] == "epoch_budget_exhausted"
    assert controller.snapshot()["incident_budget"] == before
    assert controller.snapshot()["current_candidate"] is None


def test_per_epoch_deadline_still_stops_when_global_time_remains():
    class Waiting(FixedProbePolicy):
        def wait_for_admission(self, **kwargs):
            clock.value += 361
            return True
    clock, first = Clock(), epoch()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=clock)
    event = controller.run_epoch(first, Waiting())
    assert event["report"]["termination_reason"] == "time_exhausted"
    assert controller.snapshot()["incident_budget"]["remaining_seconds"] == 359
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 0


def test_observation_waits_still_charge_the_global_720_second_horizon():
    first = epoch(statuses={"paymentservice": "DOWN"}, offset=-721)
    second = epoch(1, previous=first, offset=0)
    controller, _, b = run_profile(first, second)
    assert b["reason"] == "time_exhausted" and b["report"] is None
    assert controller.snapshot()["incident_budget"]["remaining_seconds"] == 0


def test_per_epoch_decision_caps_are_not_silently_raised_to_eight():
    class Invalid:
        kind = "scripted"
        def complete(self, *args, **kwargs):
            return DriverReply('{"action":"execute","command":"PRIVATE"}',
                               {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    first, second = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=Clock())
    a = controller.run_epoch(first, Invalid())
    assert a["report"]["termination_reason"] == "decision_budget_exhausted" and len(a["report"]["attempts"]) == 4
    b = controller.run_epoch(second, Invalid())
    assert len(b["report"]["attempts"]) == 4 and controller.snapshot()["incident_budget"]["decisions_used"] == 8
    assert "PRIVATE" not in json.dumps(controller.snapshot())


def test_incident_byte_charges_accumulate_without_refunds():
    evidence, shadow = epoch_limits("two-epoch-v1")
    budget = SharedShadowBudget(evidence, shadow, profile="two-epoch-v1", clock=Clock())
    assert budget.charge_bytes(131072)
    assert budget.snapshot()["response_bytes"] == 131072
    assert budget.charge_bytes(131072)
    assert not budget.charge_bytes(1)
    assert budget.snapshot()["response_bytes"] == 262144


def test_per_epoch_bytes_cannot_use_the_entire_global_context_allowance(monkeypatch):
    original = ComponentReader.window

    def padded(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        result["summary"]["padding"] = "x" * 50000
        return result

    monkeypatch.setattr(ComponentReader, "window", padded)
    first, clock = epoch(), Clock()
    evidence, shadow = epoch_limits("two-epoch-v1")
    budget = SharedShadowBudget(evidence, shadow, profile="two-epoch-v1", clock=clock)
    with materialize(first) as path:
        session = InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id,
            as_of=first.as_of, limits=evidence, budget=budget, clock=clock)
        results = [session.call({"tool": "component_window", "service_id": name})
                   for name in ("paymentservice", "checkoutservice", "currencyservice")]
    assert [row["outcome"] for row in results] == ["ok", "ok", "output_limit"]
    assert budget.snapshot()["response_bytes"] < evidence.max_total_bytes
    assert budget.snapshot()["calls_used"] == 3


def test_unknown_usage_is_not_bypassed_by_larger_profile():
    class Unknown:
        kind = "scripted"
        def complete(self, *args, **kwargs):
            return DriverReply("{}", None)
    first, second = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=Clock())
    controller.run_epoch(first, Unknown())
    event = controller.run_epoch(second, FixedProbePolicy())
    assert event["reason"] == "usage_unknown" and event["report"] is None
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 1


def test_latched_fatal_stop_withdraws_candidate_even_if_clock_later_recovers():
    clock, first = Clock(), epoch(statuses={"paymentservice": "DOWN"})
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=clock)
    controller.run_epoch(first, FixedProbePolicy())
    assert controller.snapshot()["current_candidate"]
    clock.value = 99
    assert controller.budget.blocked_reason() == "clock_invalid"
    clock.value = 105
    result = controller.snapshot()
    assert result["current_candidate"] is None and result["candidates"][0]["retirement_reason"] == "incident_stopped"


def test_unapproved_external_model_is_still_never_dispatched():
    class Never:
        kind = "model"
        def complete(self, *args, **kwargs):
            pytest.fail("A research profile does not authorize external transfer")
    first, _ = full_epochs()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", clock=Clock())
    assert controller.run_epoch(first, Never())["reason"] == "external_transfer_not_approved"
    assert controller.snapshot()["incident_budget"]["decisions_used"] == 0


def test_cli_records_profile_on_marker_result_and_summary(tmp_path):
    first, second = full_epochs()
    a, b, out = (tmp_path / name for name in ("a.json", "b.json", "out.json"))
    save_inputs(a, first)
    save_inputs(b, second)
    args = [sys.executable, "-m", "eval.shadow_replan", "--profile", "two-epoch-v1",
            "--inputs", str(a), "--inputs", str(b), "--output", str(out)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["profile"] == "two-epoch-v1"
    assert json.loads(out.read_text())["incident_budget"]["calls_used"] == 16
    assert json.loads(out.with_name("out.json.started.json").read_text())["profile"] == "two-epoch-v1"


@pytest.mark.parametrize("paths", [1, 3])
def test_cli_requires_exactly_two_epoch_paths_for_profile(tmp_path, paths):
    path, out = tmp_path / "input.json", tmp_path / "out.json"
    save_inputs(path, epoch())
    args = [sys.executable, "-m", "eval.shadow_replan", "--profile", "two-epoch-v1", "--output", str(out)]
    for _ in range(paths):
        args += ["--inputs", str(path)]
    process = subprocess.run(args, capture_output=True, text=True)
    assert process.returncode != 0 and not out.exists() and not out.with_name("out.json.started.json").exists()


@pytest.mark.parametrize("hidden_status", ["DOWN", "HEALTHY"])
def test_omitted_latest_timestamp_tie_cannot_be_presented_as_confident_health(hidden_status):
    first = epoch()
    template = next(row for row in first.records if row.kind == "component" and row.payload["service_id"] == "paymentservice")
    data = first.model_dump(mode="json")
    data["records"] = [row for row in data["records"] if row["payload"]["evidence_id"] != template.payload["evidence_id"]]
    for index in range(101):
        payload = deepcopy(template.payload)
        status = hidden_status if index == 0 else "HEALTHY"
        payload.update(evidence_id=f"OBS-{index + 1000:032x}", status=status, detail=f"Built-in health probe: {status}")
        data["records"].append(FrozenRecord(kind="component", stored_at=template.stored_at, payload=payload).model_dump(mode="json"))
    data["records"].sort(key=lambda row: row["payload"]["evidence_id"])
    inputs = FrozenInputs.model_validate(data)
    with materialize(inputs) as path:
        result = ComponentReader(path).window(DiagnosticRequest(service_id="paymentservice"), now=inputs.as_of.timestamp())
    assert result["truncated"] and result["summary"]["latest_truncated"]
    assert result["summary"]["latest_conflict"] and not result["summary"]["fresh_now"]
    assert result["summary"]["status_at_window_end"] == "UNKNOWN"


def test_older_truncated_history_does_not_invalidate_an_unambiguous_latest_sample():
    first = epoch()
    with materialize(first) as path, sqlite3.connect(path) as db:
        row = db.execute("SELECT * FROM evidence WHERE service='paymentservice'").fetchone()
        for index in range(100):
            payload = json.loads(row[-1])
            measured = first.as_of - timedelta(seconds=5, milliseconds=index * 10)
            stamp = measured.timestamp()
            payload.update(evidence_id=f"OBS-{index + 1000:032x}",
                           measured_at=datetime.fromtimestamp(stamp, timezone.utc).isoformat())
            db.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (payload["evidence_id"], *row[1:5], stamp, row[-2], json.dumps(payload)))
        db.commit()
        result = ComponentReader(path).window(DiagnosticRequest(service_id="paymentservice"), now=first.as_of.timestamp())
    assert result["truncated"] and not result["summary"]["latest_truncated"]
    assert result["summary"]["fresh_now"]
