"""Time-indexed display controls; all model behavior here is mocked or fixed."""

from copy import deepcopy
import json

import pytest

from agent.shadow import DriverReply, ShadowRunner
from agent.shadow_context import pack_temporal, temporal_observation_index, unpack_view
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import run_pair
from tests.test_replanning_profile import full_epochs
from tests.test_shadow_replan import Clock


def sample(identifier, measured_at, status="HEALTHY", instance="a" * 64):
    return {"evidence_id": f"OBS-{identifier:032x}", "measured_at": measured_at, "status": status,
            "quality": "unknown" if status == "UNKNOWN" else "observed", "instance_id": instance, "value": None}


def turn(samples, **summary):
    return {"feedback": [{"tool": "component_window", "request": {"service_id": "paymentservice"},
        "outcome": "ok", "data": {"samples": samples, "prompt_omitted_samples": 0,
            "summary": {"status_at_window_end": "HEALTHY", "fresh_at_cutoff": True, "latest_conflict": False,
                        "latest_truncated": False, "latest_age_seconds": 1, **summary}}}]}


def test_latest_ids_follow_measurement_time_not_id_or_list_order():
    old = sample(9, "2026-10-24T10:00:00+00:00", "DOWN")
    fresh = sample(1, "2026-10-24T10:00:05+00:00")
    source = [turn([fresh, old])]
    original = deepcopy(source)
    index = temporal_observation_index(source, ["paymentservice"])
    assert [row["evidence_id"] for row in index[0]["latest"]] == [fresh["evidence_id"]]
    assert index[0]["earlier_samples"] == 1 and index[0]["latest"][0]["status"] == "HEALTHY"
    assert source == original


def test_equal_instants_in_different_timezones_are_all_latest_not_lexically_sorted():
    a = sample(1, "2026-10-24T10:00:00+00:00")
    b = sample(2, "2026-10-24T15:30:00+05:30", "DOWN")
    index = temporal_observation_index([turn([a, b], latest_conflict=True)], [])
    assert [row["evidence_id"] for row in index[0]["latest"]] == [a["evidence_id"], b["evidence_id"]]
    assert not index[0]["earlier_samples"]
    assert index[0]["summary"]["latest_conflict"]


@pytest.mark.parametrize("status,instance,fresh,conflict,truncated", [
    ("UNKNOWN", "a" * 64, True, False, False),
    ("DOWN", None, True, False, False),
    ("HEALTHY", "a" * 64, False, False, False),
    ("HEALTHY", "a" * 64, True, True, False),
    ("HEALTHY", "a" * 64, True, True, True),
])
def test_display_preserves_unknown_identity_freshness_and_truncation(status, instance, fresh, conflict, truncated):
    point = sample(1, "2026-10-24T10:00:00+00:00", status, instance)
    index = temporal_observation_index([turn([point], status_at_window_end=status, fresh_at_cutoff=fresh,
        latest_conflict=conflict, latest_truncated=truncated)], ["paymentservice"])
    assert index[0]["latest"] == [{"evidence_id": point["evidence_id"], "status": status,
        "quality": point["quality"], "identified": instance is not None}]
    assert index[0]["summary"] == {"fresh_at_cutoff": fresh,
        "latest_conflict": conflict, "latest_truncated": truncated, "latest_age_seconds": 1}


def test_unread_is_distinct_from_read_unknown_unavailable_or_empty():
    unknown = turn([sample(1, "2026-10-24T10:00:00+00:00", "UNKNOWN")], status_at_window_end="UNKNOWN")
    unavailable = {"feedback": [{"tool": "component_window", "request": {"service_id": "cartservice"},
                                "outcome": "store_unavailable", "data": None}]}
    index = temporal_observation_index([unknown, unavailable], ["paymentservice", "cartservice", "redis-cart"])
    assert [row["read_outcome"] for row in index] == ["ok", "store_unavailable", "unread"]
    assert index[0]["latest"][0]["status"] == "UNKNOWN" and not index[1]["latest"] and not index[2]["latest"]
    empty = temporal_observation_index([turn([], fresh_at_cutoff=False, status_at_window_end="UNKNOWN")], [])
    assert not empty[0]["latest"] and empty[0]["read_outcome"] == "ok"


def test_repeated_reads_and_failed_reads_are_not_silently_reconciled_or_dropped():
    a = turn([sample(1, "2026-10-24T10:00:00+00:00", "DOWN")])
    b = turn([sample(2, "2026-10-24T10:00:01+00:00")])
    failed = {"feedback": [{"tool": "resource_window", "request": {"service_id": "paymentservice", "metric": "bytes"},
                           "outcome": "invalid_request", "data": None}]}
    index = temporal_observation_index([a, b, failed], ["paymentservice"])
    assert len(index) == 3 and [row["read_outcome"] for row in index] == ["ok", "ok", "invalid_request"]


def test_resource_index_preserves_zero_measurements_and_disclosed_prompt_omission():
    source = turn([sample(1, "2026-10-24T10:00:00+00:00", "HIGH_CPU")])
    feedback = source["feedback"][0]
    feedback.update(tool="resource_window", request={"service_id": "adservice", "metric": "cpu_percent"})
    feedback["data"]["samples"][0]["value"] = 0
    feedback["data"]["prompt_omitted_samples"] = 100
    row = temporal_observation_index([source], ["adservice"])[0]
    assert row["latest"][0]["value"] == 0 and row["metric"] == "cpu_percent"
    assert row["prompt_omitted_samples"] == 100


@pytest.mark.parametrize("stamp", ["not-a-time", "2026-10-24T10:00:00"])
def test_invalid_or_naive_time_is_rejected_not_guessed(stamp):
    with pytest.raises(ValueError):
        temporal_observation_index([turn([sample(1, stamp)])], [])


def test_wrapper_keeps_literal_index_and_every_inner_fact_without_mutation():
    source = [turn([sample(1, "2026-10-24T10:00:00+00:00", "UNKNOWN")])]
    view = {"initial": {"registered_services": ["paymentservice"]}, "turns": source,
            "evidence_index": temporal_observation_index(source, ["paymentservice"])}
    original = deepcopy(view)
    encoded = pack_temporal(view)
    assert unpack_view(encoded) == view == original
    assert encoded["evidence_index"] == view["evidence_index"]
    assert not any(marker in json.dumps(encoded["evidence_index"]) for marker in ("$record", "$literal", "$shared"))
    encoded["evidence_index"][0]["latest"][0]["status"] = "HEALTHY"
    assert view == original


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra=True),
    lambda value: value.update(evidence_index={}),
    lambda value: value.update(evidence_index=[None]),
    lambda value: value.update(view=[]),
    lambda value: value.update(view={"evidence_index": []}),
])
def test_invalid_or_conflicting_temporal_wrapper_is_rejected(change):
    encoded = pack_temporal({"initial": {}, "evidence_index": []})
    change(encoded)
    with pytest.raises(ValueError):
        unpack_view(encoded)


class Recording(FixedProbePolicy):
    def __init__(self):
        self.prompts = []

    def complete(self, messages, **kwargs):
        self.prompts.append(deepcopy(messages))
        return super().complete(messages, **kwargs)


def investigate(first, second, layout, driver=None):
    driver = driver or Recording()
    controller = ReplanningInvestigation(first, profile="two-epoch-v1", evidence_layout=layout, clock=Clock())
    controller.run_epoch(first, driver, approve_external=driver.kind == "model")
    controller.run_epoch(second, driver, approve_external=driver.kind == "model")
    return controller.snapshot(), driver


def test_temporal_and_legacy_views_have_identical_underlying_facts_reads_decisions_and_caps():
    first, second = full_epochs()
    old, a = investigate(first, second, "legacy-v1")
    new, b = investigate(first, second, "time-indexed-v1")
    assert len(a.prompts) == len(b.prompts) == 8
    for left, right in zip(a.prompts, b.prompts, strict=True):
        plain, temporal = [unpack_view(json.loads(messages[-1]["content"])) for messages in (left, right)]
        plain.pop("evidence_index")
        temporal.pop("evidence_index")
        temporal["initial"].pop("evidence_layout")
        assert plain == temporal
    assert [row["proposal"]["service_id"] for row in new["candidates"]] == ["paymentservice", "checkoutservice"]
    for key in ("calls_used", "decisions_used", "known_reported_tokens", "limits"):
        assert old["incident_budget"][key] == new["incident_budget"][key]
    assert new["incident_budget"]["calls_used"] == 16 and not new["execution_authorized"]
    for left, right in zip(old["epochs"], new["epochs"], strict=True):
        assert left["report"]["turns"] == right["report"]["turns"]


def test_latest_index_exposes_recovered_payment_but_preserves_its_old_failure():
    snapshot, policy = investigate(*full_epochs(), "time-indexed-v1")
    final = unpack_view(json.loads(policy.prompts[-1][-1]["content"]))
    row = next(row for row in final["evidence_index"] if row["service"] == "paymentservice")
    assert row["latest"][0]["status"] == "HEALTHY" and row["earlier_samples"] == 1
    assert any(sample["status"] == "DOWN" for turn in final["turns"] for feedback in turn.get("feedback", [])
        if (feedback.get("request") or {}).get("service_id") == "paymentservice"
        for sample in feedback["data"]["samples"])
    assert snapshot["epochs"][1]["report"]["candidate_screened"]


@pytest.mark.parametrize("layout", ["automatic", None, True, "time-indexed-v2"])
def test_unknown_layout_is_rejected_before_policy_use(layout):
    first, _ = full_epochs()
    with pytest.raises(ValueError):
        ReplanningInvestigation(first, evidence_layout=layout)


def test_temporal_layout_cannot_be_enabled_without_structured_screening(tmp_path):
    from agent.investigation import InvestigationSession
    from eval.shadow_replay import materialize

    first, _ = full_epochs()
    with materialize(first) as path:
        session = InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id, as_of=first.as_of)
        with pytest.raises(ValueError):
            ShadowRunner(session, first.topology, FixedProbePolicy(), evidence_layout="time-indexed-v1")


def test_native_mocked_model_sees_latest_ids_but_still_has_to_choose_the_target():
    class MockModel(Recording):
        kind = "model"
        structured_decisions = True

        def estimate_input_tokens(self, messages):
            return 1000

        def complete(self, messages, **kwargs):
            reply = super().complete(messages, **kwargs)
            return DriverReply(json.dumps({"decision": json.loads(reply.content)}),
                {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}, "stop")

    snapshot, _ = investigate(*full_epochs(), "time-indexed-v1", MockModel())
    assert snapshot["incident_budget"]["decisions_used"] == 8
    assert snapshot["incident_budget"]["known_reported_tokens"] == 880
    assert all(row["report"]["candidate_screened"] for row in snapshot["epochs"])
    assert not snapshot["execution_authorized"]


def test_single_epoch_pair_uses_the_same_temporal_layout_without_hiding_the_service_choices():
    first, _ = full_epochs()
    paired = run_pair(first, Recording(), screening="dependency", context_profile="structured",
                      evidence_layout="time-indexed-v1")
    assert paired["initial_context_equal"]
    for arm in ("baseline", "comparator"):
        report = paired[arm]["report"]
        assert report["evidence_layout"] == "time-indexed-v1" and report["candidate_screened"]
        assert report["session"]["calls_used"] == 8 and not report["execution_authorized"]


def test_native_schema_keeps_all_registered_target_choices_and_only_displayed_citations():
    from types import SimpleNamespace
    from agent.shadow_provider import GroqShadowDriver
    from core.registry import DEFAULT_DEPLOYMENT

    _, policy = investigate(*full_epochs(), "time-indexed-v1")
    driver = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)
    schema = driver._schema(policy.prompts[-1])
    hypothesis = schema["$defs"]["HypothesisProposal"]["properties"]
    assert hypothesis["service_id"]["enum"] == sorted(DEFAULT_DEPLOYMENT.by_name)
    assert not {"execute", "repair"} & set(json.dumps(schema).split('"'))
    assert hypothesis["supporting_evidence"]["items"]["enum"]
