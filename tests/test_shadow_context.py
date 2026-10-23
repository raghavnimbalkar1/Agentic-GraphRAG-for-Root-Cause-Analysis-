"""Lossless prompt controls and offline sensitivity, not model or recovery results."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import subprocess
import sys

import pytest

from agent.investigation import InvestigationSession
from agent.shadow import DECISION, DriverReply, ShadowLimits, ShadowRunner, project_feedback
from agent.shadow_context import compact_schema, pack_view, unpack_view
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, run_controls, seed_case
from eval.shadow_preflight import RecordingPolicy, compare_profiles
from eval.shadow_replay import freeze_inputs, run_pair, save_inputs


def frozen_case(tmp_path, case="payment"):
    store = seed_case(tmp_path / f"{case}.sqlite", CASES[case][0])
    cutoff = datetime.now(timezone.utc)
    return freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff,
                         topology=fixture_topology(cutoff), source_kind="synthetic")


def test_metadata_factoring_round_trip_keeps_every_fact_and_input_unmodified():
    shared = {"environment_id": "local", "registry_revision": "revision", "registry_digest": "d" * 64,
              "evaluated_as_of": "2026-10-22T00:00:00+00:00"}
    view = {"initial": {"root_cause_node": None}, "turns": [
        {"feedback": [{"data": {**shared, "status": status, "quality": "unknown", "truncated": True,
            "prompt_omitted_samples": 9, "samples": [{"evidence_id": f"OBS-{index:032x}",
                "measured_at": "2026-10-21T00:00:00+00:00", "collected_at": "2026-10-22T00:00:00+00:00",
                "instance_id": None, "measurements": {"cpu_percent": 17}}],
            "unobserved_intervals": [None, [1, 2]], "signal": "Untrusted instructions stay data"}}]}
        for index, status in enumerate(["UNKNOWN", "HEALTHY", "OOM_KILLED"])], "remaining_decisions": 1}
    original = deepcopy(view)
    packed = pack_view(view)
    assert shared in packed["shared_metadata"]
    assert all("$shared" in row["feedback"][0]["data"] for row in packed["view"]["turns"])
    assert unpack_view(packed) == original == view
    assert "Untrusted instructions stay data" in json.dumps(packed)
    restored = unpack_view(packed)
    restored["turns"][0]["feedback"][0]["data"]["registry_digest"] = "changed"
    assert unpack_view(packed) == original


def test_different_provenance_and_times_are_never_silently_unified():
    rows = [{"registry_digest": value * 64, "collected_at": stamp, "status": "UNKNOWN"}
            for value, stamp in [("a", "older"), ("b", "later"), ("a", "older")]]
    assert unpack_view(pack_view({"rows": rows})) == {"rows": rows}


def test_singletons_and_small_groups_are_not_factored():
    packed = pack_view({"a": {"environment_id": "x"}, "b": {"environment_id": "x"},
                        "singleton": {"registry_digest": "b" * 64}})
    assert packed["shared_metadata"] == []


@pytest.mark.parametrize("change", [
    lambda value: value.update(context_encoding="unknown"),
    lambda value: value.update(unreviewed=True),
    lambda value: value.update(shared_metadata={}),
    lambda value: value.update(shared_metadata=[{"status": "HEALTHY"}]),
    lambda value: value.update(shared_metadata=[{"instance_id": {"hidden": "a"}}]),
    lambda value: value["view"].update({"$shared": -1}),
    lambda value: value["view"].update({"$shared": True}),
    lambda value: value["view"].update({"$shared": "0"}),
    lambda value: value["view"].update({"$shared": 2}),
    lambda value: value["view"].update(registry_digest="conflicting"),
])
def test_malformed_or_conflicting_metadata_encoding_is_rejected(change):
    packed = {"context_encoding": "shared-metadata-v1", "shared_metadata": [{"registry_digest": "a" * 64}],
              "view": {"$shared": 0, "status": "UNKNOWN"}}
    change(packed)
    with pytest.raises(ValueError):
        unpack_view(packed)


def test_reserved_field_cannot_be_disguised_as_evidence_metadata():
    with pytest.raises(ValueError):
        pack_view({"nested": [{"$shared": 0}]})


def test_schema_only_removes_display_titles_not_constraints_defaults_or_descriptions():
    schema = DECISION.json_schema()
    original = deepcopy(schema)
    compact = compact_schema(schema)
    def check(full, shortened):
        if isinstance(full, dict):
            assert set(shortened) == set(full) - {"title"}
            for key in shortened:
                check(full[key], shortened[key])
        elif isinstance(full, list):
            assert len(full) == len(shortened)
            for a, b in zip(full, shortened, strict=True):
                check(a, b)
        else:
            assert full == shortened
    check(schema, compact)
    assert schema == original
    assert compact["$defs"]["ToolRequest"]["additionalProperties"] is False
    assert "description" in compact["$defs"]["ToolRequest"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_preflight_preserves_full_public_facts_and_fixed_control_decisions(tmp_path, case):
    result = compare_profiles(frozen_case(tmp_path, case))
    assert result["matched_complete_control"] and result["matched_public_facts"]
    assert result["matched_fixed_decisions"] and result["envelope_bytes_saved"] > 0
    assert result["real_model_calls"] == 0 and not result["provider_budget_verified"]
    assert not result["model_trial_authorized"] and not result["execution_authorized"]
    assert not result["causal_accuracy_measured"]
    standard, compact = (result["profiles"][name] for name in ("standard", "compact"))
    assert standard["session_calls"] == compact["session_calls"] == 8
    for profile in (standard, compact):
        assert len(profile["attempts"]) == 4
        assert all(len(row["prompt_sha256"]) == 64 for row in profile["attempts"])
        assert profile["token_sensitivity"][0]["assumed_input_tokens"] > 0
        assert profile["token_sensitivity"][0]["reserved_output_tokens"] == 4 * 768
        assert [row["assumed_total_tokens"] for row in profile["token_sensitivity"]] == sorted(
            row["assumed_total_tokens"] for row in profile["token_sensitivity"])


def test_profile_parity_check_does_not_pass_guard_generated_abstention(tmp_path):
    result = compare_profiles(frozen_case(tmp_path), shadow_limits=ShadowLimits(max_prompt_bytes=1024))
    assert not result["matched_complete_control"]
    assert all(row["termination_reason"] == "prompt_limit" for row in result["profiles"].values())
    assert all(row["attempts"] == [] for row in result["profiles"].values())
    assert not result["provider_budget_verified"]


def test_same_profile_is_given_to_both_policies_with_stable_prompt_digests(tmp_path):
    policy = RecordingPolicy()
    paired = run_pair(frozen_case(tmp_path), policy, screening="dependency", context_profile="compact")
    baseline, candidate = (paired[name]["report"] for name in ("baseline", "comparator"))
    assert paired["initial_context_equal"] and baseline["context_profile"] == candidate["context_profile"] == "compact"
    assert baseline["turns"] == candidate["turns"] and baseline["candidate_screened"]
    assert baseline["attempts"][0]["prompt_sha256"] == candidate["attempts"][0]["prompt_sha256"]
    for messages, attempt in zip(policy.prompts, candidate["attempts"], strict=True):
        assert attempt["prompt_sha256"] == sha256(json.dumps(messages).encode()).hexdigest()
        assert "shared-metadata-v1" in messages[0]["content"]


def test_compact_context_keeps_visible_id_checks_and_does_not_authorize_repairs(tmp_path):
    store = seed_case(tmp_path / "source.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    class InvalidCitation:
        kind = "scripted"
        def complete(self, messages, **_):
            assert "context_encoding" in json.loads(messages[-1]["content"])
            return DriverReply(json.dumps({"action": "propose", "hypothesis": {
                "service_id": "paymentservice", "condition": "readiness_failure",
                "supporting_evidence": ["OBS-" + "f" * 32]}}),
                {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})
    session = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
    report = ShadowRunner(session, fixture_topology(cutoff), InvalidCitation(), screening="dependency",
                          context_profile="compact").run()
    assert report["termination_reason"] == "unshown_citation"
    assert not report["candidate_screened"] and not report["execution_authorized"]
    assert report["session"]["calls_used"] == 0 and report["known_reported_tokens"] == 20


def test_unknown_profile_is_rejected_before_driver_use(tmp_path):
    store = seed_case(tmp_path / "source.sqlite", {})
    cutoff = datetime.now(timezone.utc)
    session = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
    with pytest.raises(ValueError, match="context profile"):
        ShadowRunner(session, fixture_topology(cutoff), FixedProbePolicy(), context_profile="automatic")


@pytest.mark.parametrize("usage,reason", [
    (None, "usage_unknown"),
    ({"input_tokens": 1, "output_tokens": 1, "total_tokens": 3}, "usage_unknown"),
    ({"input_tokens": 8192, "output_tokens": 1, "total_tokens": 8193}, "reported_token_limit"),
])
def test_compact_mode_still_discards_unmetered_or_over_budget_reply(tmp_path, usage, reason):
    store = seed_case(tmp_path / "source.sqlite", {})
    cutoff = datetime.now(timezone.utc)
    class UsageDriver:
        kind = "scripted"
        def complete(self, messages, **_):
            return DriverReply('{"action":"abstain","reason":"unknown_condition"}', usage)
    session = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
    report = ShadowRunner(session, fixture_topology(cutoff), UsageDriver(), context_profile="compact").run()
    assert report["termination_reason"] == reason and len(report["attempts"]) == 1
    assert report["turns"] == [] and not report["candidate_screened"]


def test_all_four_public_tool_response_shapes_expand_without_fact_loss(tmp_path):
    store = seed_case(tmp_path / "source.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    session = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
    requests = [{"tool": "component_window", "service_id": "paymentservice"},
                {"tool": "resource_window", "service_id": "adservice", "metric": "cpu_percent"},
                {"tool": "transaction_evidence", "service_id": "frontend"},
                {"tool": "error_signals", "service_id": "frontend"}]
    feedback = [project_feedback(session.call(request)) for request in requests]
    view = {"initial": session.initial_context(), "turns": [{"feedback": feedback}]}
    assert unpack_view(pack_view(view)) == view


def test_compact_deferred_proposal_can_change_target_without_resetting_budget(tmp_path):
    store = seed_case(tmp_path / "source.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    topology = fixture_topology(cutoff).model_copy(update={"edges": (("checkoutservice", "paymentservice"),)})
    class RevisingDriver:
        kind = "scripted"
        def __init__(self):
            self.views = []
        def complete(self, messages, **_):
            view = unpack_view(json.loads(messages[-1]["content"]))
            self.views.append(view)
            step = len(self.views)
            name = "checkoutservice" if step <= 2 else "paymentservice"
            if step in {1, 3}:
                decision = {"action": "read", "requests": [{"tool": "component_window", "service_id": name}]}
            else:
                sample = view["turns"][-1]["feedback"][0]["data"]["samples"][-1]
                decision = {"action": "propose", "hypothesis": {"service_id": name, "condition": "readiness_failure",
                            "supporting_evidence": [sample["evidence_id"]]}}
            return DriverReply(json.dumps(decision), {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})
    session = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
    driver = RevisingDriver()
    report = ShadowRunner(session, topology, driver, screening="dependency", context_profile="compact").run()
    assert report["candidate_screened"] and report["result"]["proposal"]["service_id"] == "paymentservice"
    assert report["turns"][1]["outcome"] == "proposal_deferred"
    assert [view["remaining_decisions"] for view in driver.views] == [3, 2, 1, 0]
    assert len(report["attempts"]) == 4 and report["session"]["calls_used"] == 2
    assert report["known_reported_tokens"] == 80 and not report["execution_authorized"]


def test_compact_controls_work_without_changing_legacy_default():
    result = run_controls(screening="dependency", context_profile="compact")
    assert result["passed"] and result["real_model_calls"] == 0
    assert all(row["report"]["candidate_screened"] for row in result["controls"][:3])
    legacy = run_controls()
    assert legacy["context_profile"] == "standard"
    assert all(row["report"]["evidence_assessment"] is None for row in legacy["controls"])


def test_preflight_cli_is_offline_private_and_refuses_overwrite(tmp_path):
    path, report = tmp_path / "inputs.json", tmp_path / "report.json"
    save_inputs(path, frozen_case(tmp_path))
    command = [sys.executable, "-m", "eval.shadow_preflight", "--inputs", str(path), "--output", str(report)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["real_model_calls"] == 0
    assert os.stat(report).st_mode & 0o777 == 0o600
    before = report.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert report.read_bytes() == before


def test_context_and_preflight_imports_load_no_settings_provider_executor_or_network_client():
    subprocess.run([sys.executable, "-c", "import sys; import agent.shadow_context; import eval.shadow_preflight; "
        "assert not any(name in sys.modules for name in ('core.config','docker','agent.graph',"
        "'agent.nodes.reasoner','agent.nodes.executor','agent.shadow_provider','langchain_openai','openai'))"],
        check=True, capture_output=True)
