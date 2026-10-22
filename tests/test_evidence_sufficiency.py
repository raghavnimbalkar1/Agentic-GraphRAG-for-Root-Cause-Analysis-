"""Declared-scope software checks, not live/model diagnosis or causal accuracy."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from agent.evidence_policy import SCREENING_POLICY, assess_dependencies, screening_contract
from agent.investigation import InvestigationSession
from agent.shadow import DriverReply, ShadowRunner, project_feedback
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, run_controls, seed_case
from eval.shadow_replay import freeze_inputs, run_pair, save_inputs


def source(tmp_path, case="payment", *, statuses=None, edges=None):
    store = seed_case(tmp_path / "source.sqlite", statuses if statuses is not None else CASES[case][0])
    cutoff = datetime.now(timezone.utc)
    topology = fixture_topology(cutoff)
    if edges is not None:
        topology = topology.model_copy(update={"edges": edges})
    return store, cutoff, topology


def session(src):
    return InvestigationSession(src[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=src[1])


def read(*names):
    return {"action": "read", "requests": [{"tool": "component_window", "service_id": name} for name in names]}


def trace(src):
    context = ShadowRunner(session(src), src[2], FixedProbePolicy()).context
    broker, turns = session(src), []
    for name in screening_contract(context)["required_scope"]:
        request = {"tool": "component_window", "service_id": name}
        turns.append({"feedback": [project_feedback(broker.call(request))]})
    return context, turns


def hypothesis(turns, name, condition="readiness_failure", **fields):
    rows = [item["data"]["samples"][-1] for turn in turns for item in turn.get("feedback", [])
            if item.get("data") and item["data"]["service_id"] == name]
    return {"service_id": name, "condition": condition,
            "supporting_evidence": [rows[-1]["evidence_id"]], **fields}


class Scripted:
    kind = "scripted"

    def __init__(self, replies):
        self.replies, self.messages = list(replies), []

    def complete(self, messages, **_):
        self.messages.append(deepcopy(messages))
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(json.loads(messages[-1]["content"]))
        return DriverReply(json.dumps(reply), {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})


@pytest.mark.parametrize("case,name,condition", [("payment", "paymentservice", "readiness_failure"),
    ("redis", "redis-cart", "resource_pressure"), ("payment-and-unrelated-cpu", "paymentservice", "readiness_failure")])
def test_complete_consistent_scope_screens_only_an_evidence_supported_candidate(tmp_path, case, name, condition):
    context, turns = trace(source(tmp_path, case))
    result = assess_dependencies(context, turns, hypothesis(turns, name, condition))
    assert result["verdict"] == "candidate_supported" and result["coverage_complete"]
    assert not result["causal_attribution"] and not result["execution_authorized"]
    assert "suggested_service" not in result and "root_cause_node" not in result


def test_coverage_is_explicit_and_unread_is_not_healthy(tmp_path):
    context, turns = trace(source(tmp_path))
    empty = assess_dependencies(context, [])
    assert empty["missing_services"] == screening_contract(context)["required_scope"]
    first = assess_dependencies(context, turns[:1], hypothesis(turns, "checkoutservice"))
    assert first["verdict"] == "more_evidence_required" and not first["coverage_complete"]
    assert "paymentservice" in first["missing_services"]
    assert "paymentservice" not in first["reason"]  # No alternative is chosen for the driver.


@pytest.mark.parametrize("case", ["payment", "redis"])
def test_full_scope_prevents_the_symptom_service_becoming_the_dependency_candidate(tmp_path, case):
    context, turns = trace(source(tmp_path, case))
    result = assess_dependencies(context, turns, hypothesis(turns, "checkoutservice"))
    assert result["verdict"] == "blocked" and result["reason"] == "downstream_anomaly_unresolved"


def test_independent_anomalies_remain_ambiguous(tmp_path):
    context, turns = trace(source(tmp_path, "multiple-candidates"))
    for target, condition in (("paymentservice", "readiness_failure"), ("redis-cart", "resource_pressure")):
        result = assess_dependencies(context, turns, hypothesis(turns, target, condition))
        assert result["reason"] == "competing_anomaly_unresolved" and result["verdict"] == "blocked"


def test_unknown_evidence_and_unsupported_conditions_do_not_pass(tmp_path):
    context, turns = trace(source(tmp_path, "unknown-telemetry"))
    result = assess_dependencies(context, turns)
    assert "paymentservice" in result["uncertain_services"] and not result["coverage_complete"]
    src = source(tmp_path / "unsupported", statuses={"paymentservice": "CONFIG_DRIFT"})
    context, turns = trace(src)
    result = assess_dependencies(context, turns, hypothesis(turns, "paymentservice"))
    assert result["reason"] == "unsupported_condition" and result["unsupported_services"] == ["paymentservice"]


@pytest.mark.parametrize("fields,reason", [
    ({"condition": "request_failure"}, "condition_not_supported"),
    ({"supporting_evidence": [SYMPTOM_ID]}, "latest_target_evidence_required"),
    ({"unresolved_alternatives": ["cartservice"]}, "declared_unresolved_evidence"),
    ({"contradicting_evidence": [SYMPTOM_ID]}, "declared_unresolved_evidence"),
    ({"unresolved_alternatives": ["not-registered"]}, "invalid_hypothesis"),
])
def test_target_specific_citation_condition_and_unresolved_evidence_are_checked(tmp_path, fields, reason):
    context, turns = trace(source(tmp_path))
    result = assess_dependencies(context, turns, hypothesis(turns, "paymentservice", **fields))
    assert result["verdict"] == "blocked" and result["reason"] == reason


def test_unrelated_targets_and_healthy_numeric_values_are_not_root_evidence(tmp_path):
    context, turns = trace(source(tmp_path, "payment-and-unrelated-cpu"))
    result = assess_dependencies(context, turns, {"service_id": "adservice", "condition": "resource_pressure",
                                                "supporting_evidence": [SYMPTOM_ID]})
    assert result["reason"] == "candidate_outside_scope"
    # A number alone has no reviewed pressure threshold in this policy version.
    context, turns = trace(source(tmp_path / "healthy", statuses={}, edges=(("checkoutservice", "adservice"),)))
    data = turns[-1]["feedback"][0]["data"]
    data["samples"][-1]["value"] = 99.9
    result = assess_dependencies(context, turns, hypothesis(turns, "adservice", "resource_pressure"))
    assert result["reason"] == "condition_not_supported"


@pytest.mark.parametrize("field,value", [("registry_digest", "0" * 64), ("registry_revision", "other"),
    ("environment_id", "other"), ("evaluated_as_of", "2000-01-01T00:00:00+00:00"), ("service_id", "other")])
def test_wrong_scope_or_receipt_provenance_blocks_coverage(tmp_path, field, value):
    context, turns = trace(source(tmp_path))
    turns[0]["feedback"][0]["data"][field] = value
    result = assess_dependencies(context, turns)
    assert "checkoutservice" in result["uncertain_services"] and not result["coverage_complete"]


@pytest.mark.parametrize("change", ["stale", "future", "unknown", "missing-instance", "conflict", "future-collection", "unavailable"])
def test_invalid_or_uncertain_views_never_establish_health(tmp_path, change):
    context, turns = trace(source(tmp_path))
    feedback = turns[0]["feedback"][0]
    data, cutoff = feedback["data"], datetime.fromisoformat(context["availability_cutoff"])
    sample = data["samples"][-1]
    if change in {"stale", "future"}:
        sample["measured_at"] = (cutoff + timedelta(seconds=-31 if change == "stale" else 1)).isoformat()
    elif change == "unknown":
        sample.update(quality="unknown", status="UNKNOWN")
    elif change == "missing-instance":
        sample["instance_id"] = None
    elif change == "conflict":
        data["summary"]["latest_conflict"] = True
    elif change == "future-collection":
        sample["collected_at"] = (cutoff + timedelta(seconds=1)).isoformat()
    else:
        feedback.update(outcome="unavailable", data=None)
    result = assess_dependencies(context, turns)
    assert not result["coverage_complete"]
    assert "checkoutservice" in result["missing_services"] + result["uncertain_services"]


def test_conflicting_retained_views_at_the_same_cutoff_fail_closed(tmp_path):
    context, turns = trace(source(tmp_path))
    changed = deepcopy(turns[0])
    changed["feedback"][0]["data"]["samples"][-1]["instance_id"] = "b" * 64
    result = assess_dependencies(context, [*turns, changed])
    assert result["uncertain_services"] == ["checkoutservice"]
    assert not result["coverage_complete"]


def test_declared_cycles_are_bounded_and_ambiguous_anomalies_are_blocked(tmp_path):
    src = source(tmp_path, statuses={"checkoutservice": "DEGRADED", "paymentservice": "CONNECTION_REFUSED"},
                 edges=(("checkoutservice", "paymentservice"), ("paymentservice", "checkoutservice")))
    context, turns = trace(src)
    assert len(screening_contract(context)["required_scope"]) == 2
    for name in ("checkoutservice", "paymentservice"):
        assert assess_dependencies(context, turns, hypothesis(turns, name))["verdict"] == "blocked"


def test_missing_declared_edges_can_hide_a_real_alternative_so_screening_is_not_causality(tmp_path):
    context, turns = trace(source(tmp_path, "redis", edges=()))
    review = assess_dependencies(context, turns, hypothesis(turns, "checkoutservice"))
    assert review["required_scope"] == ["checkoutservice"] and review["verdict"] == "candidate_supported"
    assert not review["causal_attribution"] and not review["execution_authorized"]


def test_scope_larger_than_tool_budget_abstains_instead_of_expanding_limits(tmp_path):
    src = source(tmp_path)
    with sqlite3.connect(src[0].path) as db:
        raw = json.loads(db.execute("SELECT payload FROM evidence WHERE kind='symptom'").fetchone()[0])
        raw["steps"] = [{"step": "home", "success": False, "status_code": 500, "latency_s": 0.01}]
        db.execute("UPDATE evidence SET payload=? WHERE kind='symptom'", (json.dumps(raw),))
    report = ShadowRunner(session(src), src[2], FixedProbePolicy(), screening="dependency").run()
    assert len(report["evidence_assessment"]["required_scope"]) > 8
    assert report["session"]["calls_used"] == 8 and len(report["attempts"]) == 4
    assert report["result"]["outcome"] == "abstained" and not report["candidate_screened"]
    assert report["evidence_assessment"]["missing_services"]


def test_deferred_proposal_can_change_target_without_resetting_budgets(tmp_path):
    src = source(tmp_path, edges=(("checkoutservice", "paymentservice"),))
    driver = Scripted([read("checkoutservice"),
        lambda view: {"action": "propose", "hypothesis": hypothesis(view["turns"], "checkoutservice")},
        read("paymentservice"),
        lambda view: {"action": "propose", "hypothesis": hypothesis(view["turns"], "paymentservice")}])
    report = ShadowRunner(session(src), src[2], driver, screening="dependency").run()
    assert report["result"]["proposal"]["service_id"] == "paymentservice" and report["candidate_screened"]
    assert report["turns"][1]["outcome"] == "proposal_deferred"
    assert report["turns"][1]["proposal_review"]["missing_services"] == ["paymentservice"]
    assert len(report["attempts"]) == 4 and report["session"]["calls_used"] == 2
    assert report["known_reported_tokens"] == 440 and report["provider_attempts"] == 0
    assert not report["execution_authorized"] and report["session"]["root_cause_node"] is None
    next_view = json.loads(driver.messages[2][-1]["content"])
    assert next_view["remaining_decisions"] == 1 and next_view["remaining_tool_calls"] == 7
    assert report["evidence_assessment"]["verdict"] == "candidate_supported"


def test_repeated_premature_proposals_exhaust_the_original_decision_budget(tmp_path):
    src = source(tmp_path)
    premature = {"action": "propose", "hypothesis": {"service_id": "checkoutservice", "condition": "request_failure",
                                                     "supporting_evidence": [SYMPTOM_ID]}}
    report = ShadowRunner(session(src), src[2], Scripted([premature] * 4), screening="dependency").run()
    assert len(report["attempts"]) == 4 and report["session"]["calls_used"] == 0
    assert all(turn["outcome"] == "proposal_deferred" for turn in report["turns"])
    assert report["termination_reason"] == "decision_budget_exhausted" and not report["candidate_screened"]
    assert report["result"]["outcome"] == "abstained" and report["session"]["closed"]


@pytest.mark.parametrize("bad", ["reference", "target"])
def test_security_denials_remain_terminal_in_screened_mode(tmp_path, bad):
    src = source(tmp_path)
    proposal = {"service_id": "checkoutservice", "condition": "request_failure", "supporting_evidence": [SYMPTOM_ID]}
    if bad == "reference":
        proposal["supporting_evidence"] = ["OBS-" + "f" * 32]
    else:
        proposal["service_id"] = "not-registered"
    driver = Scripted([{"action": "propose", "hypothesis": proposal}])
    report = ShadowRunner(session(src), src[2], driver, screening="dependency").run()
    assert len(report["attempts"]) == 1 and report["session"]["closed"]
    assert report["termination_reason"] == ("unshown_citation" if bad == "reference" else "denied")
    assert not report["candidate_screened"]


def test_structural_screen_never_bypasses_the_final_broker_reference_checks(tmp_path):
    src = source(tmp_path, edges=(("checkoutservice", "paymentservice"),))
    old_id = "OBS-" + "e" * 32
    old_stamp = datetime.now(timezone.utc) - timedelta(seconds=60)
    src[0].append_observations([ObservationRecord(schema_version=2, evidence_id=old_id,
        environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest,
        service_id="paymentservice", instance_id="a" * 64, measured_at=old_stamp, collected_at=old_stamp,
        status="CONNECTION_REFUSED", quality="observed", detail="synthetic old observation")])
    src = (src[0], datetime.now(timezone.utc), src[2])
    def with_old_reference(view):
        proposed = hypothesis(view["turns"], "paymentservice")
        proposed["supporting_evidence"].append(old_id)
        return {"action": "propose", "hypothesis": proposed}
    report = ShadowRunner(session(src), src[2], Scripted([read("checkoutservice", "paymentservice"),
        with_old_reference]), screening="dependency").run()
    assert report["result"]["outcome"] == "denied" and not report["candidate_screened"]
    assert old_id in report["session"]["issued_evidence_ids"]  # Visible does not mean fresh enough to cite.
    assert not report["execution_authorized"] and not report["causal_attribution"]


def test_opt_in_does_not_relabel_legacy_provisional_results(tmp_path):
    src = source(tmp_path, "redis")
    driver = Scripted([read("checkoutservice"),
        lambda view: {"action": "propose", "hypothesis": hypothesis(view["turns"], "checkoutservice")}])
    legacy = ShadowRunner(session(src), src[2], driver).run()
    assert legacy["result"]["proposal"]["service_id"] == "checkoutservice"
    assert legacy["screening_policy"] == "provisional-v1" and not legacy["candidate_screened"]
    assert legacy["evidence_assessment"] is None


def test_invalid_screening_mode_is_rejected_before_claim_or_decisions(tmp_path):
    src = source(tmp_path)
    broker = session(src)
    driver = Scripted([])
    with pytest.raises(ValueError, match="screening"):
        ShadowRunner(broker, src[2], driver, screening="automatic")
    assert not driver.messages and broker.snapshot()["calls_used"] == 0 and not broker.snapshot()["closed"]


def test_screened_same_input_pair_and_six_controls_need_no_model(tmp_path):
    src = source(tmp_path)
    inputs = freeze_inputs(src[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=src[1],
                           topology=src[2], source_kind="synthetic")
    pair = run_pair(inputs, FixedProbePolicy(), screening="dependency")
    assert pair["initial_context_equal"]
    assert pair["baseline"]["report"]["candidate_screened"] and pair["comparator"]["report"]["candidate_screened"]
    assert pair["baseline"]["report"]["screening_policy"] == SCREENING_POLICY
    controls = run_controls(screening="dependency")
    assert controls["passed"] and controls["real_model_calls"] == 0


def test_screened_cli_replays_a_frozen_bundle_offline(tmp_path):
    src = source(tmp_path)
    inputs = freeze_inputs(src[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=src[1],
                           topology=src[2], source_kind="synthetic")
    path, output = tmp_path / "inputs.json", tmp_path / "result.json"
    save_inputs(path, inputs)
    process = subprocess.run([sys.executable, "-m", "eval.shadow_replay", "replay", "--screened",
        "--inputs", str(path), "--output", str(output)], check=True, capture_output=True)
    assert json.loads(process.stdout)["candidate_screened"]
    assert json.loads(output.read_text())["report"]["provider_attempts"] == 0


def test_policy_import_has_no_infrastructure_or_provider_dependency(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {root!r}); import agent.evidence_policy; "
        "assert not any(name in sys.modules for name in ('core.config','docker','langchain_openai','agent.nodes.executor'))"],
        cwd=tmp_path, env={}, check=True, capture_output=True)
