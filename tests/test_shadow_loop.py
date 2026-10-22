"""Synthetic shadow decisions and mocked wire traffic; no external inference or repair."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from agent.investigation import InvestigationLimits, InvestigationSession
from agent.shadow import DriverReply, ShadowLimits, ShadowRunner, parse_decision, project_feedback
from agent.shadow_provider import GroqShadowDriver
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import SYMPTOM_ID, fixture_topology, run_controls, seed_case

USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


class ScriptedDriver:
    kind = "scripted"

    def __init__(self, replies):
        self.replies, self.messages, self.calls = list(replies), [], []

    def complete(self, messages, **limits):
        self.messages.append(json.loads(json.dumps(messages)))
        self.calls.append(limits)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(messages)
        return reply if isinstance(reply, DriverReply) else DriverReply(json.dumps(reply), dict(USAGE))


@pytest.fixture
def source(tmp_path):
    store = seed_case(tmp_path / "evidence.sqlite", {"paymentservice": "CONNECTION_REFUSED"})
    cutoff = datetime.now(timezone.utc)
    return store, cutoff, fixture_topology(cutoff)


def session(source, **kwargs):
    store, cutoff, _ = source
    return InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff, **kwargs)


def read(*services):
    return {"action": "read", "requests": [{"tool": "component_window", "service_id": name, "window_seconds": 60}
                                            for name in services]}


def suggest(messages):
    view = json.loads(messages[-1]["content"])
    sample = view["turns"][-1]["feedback"][0]["data"]["samples"][-1]
    return {"action": "propose", "hypothesis": {"service_id": "paymentservice", "condition": "readiness_failure",
                                                "supporting_evidence": [sample["evidence_id"]]}}


def test_langgraph_reads_then_proposes_with_usage_and_step_trace(source):
    driver = ScriptedDriver([read("paymentservice"), suggest])
    report = ShadowRunner(session(source), source[2], driver).run()
    assert report["termination_reason"] == "proposal"
    assert report["result"]["proposal"]["service_id"] == "paymentservice"
    assert len(report["attempts"]) == 2 and report["known_reported_tokens"] == 240
    assert report["provider_attempts"] == 0 and report["driver_kind"] == "scripted"
    assert all(attempt["usage_origin"] == "synthetic" for attempt in report["attempts"])
    assert report["session"]["calls_used"] == 1
    assert not report["execution_authorized"] and not report["causal_attribution"]
    assert report["session"]["root_cause_node"] is None
    assert "Synthetic offline component observation" not in json.dumps(driver.messages)
    assert all(0 < call["timeout_seconds"] <= 20 and call["max_output_tokens"] == 768 for call in driver.calls)


@pytest.mark.parametrize("content", [
    '```json\n{"action":"abstain","reason":"unknown_condition"}\n```',
    '{"action":"execute","command":"private-command"}',
    '{"action":"read","requests":[{"tool":"shell","service_id":"frontend"}]}',
    '{"action":"abstain","action":"propose","reason":"unknown_condition"}',
    '{"action":"abstain","reason":"unknown_condition","confidence":NaN}',
    '[]', 'null', '{"action":"read","requests":[]}',
    'x' * 8193,
])
def test_decision_parser_rejects_commands_ambiguity_nonfinite_and_unbounded_content(content):
    with pytest.raises(ValueError):
        parse_decision(content)


def test_invalid_output_retry_is_bounded_charged_and_not_echoed(source):
    invalid = DriverReply('{"action":"execute","command":"private-command"}', dict(USAGE))
    driver = ScriptedDriver([invalid] * 4)
    report = ShadowRunner(session(source), source[2], driver).run()
    assert report["termination_reason"] == "decision_budget_exhausted"
    assert len(report["attempts"]) == 4 and report["known_reported_tokens"] == 480
    assert report["session"]["calls_used"] == 0
    assert "private-command" not in json.dumps(report) + json.dumps(driver.messages)
    assert report["result"]["outcome"] == "abstained"


def test_duplicate_reads_consume_both_decision_and_tool_budgets(source):
    driver = ScriptedDriver([read("paymentservice")] * 4)
    report = ShadowRunner(session(source), source[2], driver).run()
    assert report["session"]["calls_used"] == 4
    assert [row["outcome"] for row in report["session"]["history"]] == ["ok", "duplicate", "duplicate", "duplicate"]
    assert len(report["attempts"]) == 4


@pytest.mark.parametrize("reply,reason", [
    (RuntimeError("private-provider-details"), "provider_unavailable"),
    (DriverReply("{}", None), "usage_unknown"),
    (DriverReply("{}", {"input_tokens": True, "output_tokens": 1, "total_tokens": 2}), "usage_unknown"),
    (DriverReply("{}", {"input_tokens": 10, "output_tokens": 10, "total_tokens": 3}), "usage_unknown"),
])
def test_failed_or_unmetered_requests_stop_without_hidden_retry(source, reply, reason):
    driver = ScriptedDriver([reply])
    report = ShadowRunner(session(source), source[2], driver).run()
    assert report["termination_reason"] == reason and len(driver.calls) == 1
    assert not report["usage_complete"] and report["admission_units"] > 0
    assert "private-provider-details" not in json.dumps(report)
    assert report["session"]["closed"]


@pytest.mark.parametrize("limit,expected_calls,reason", [
    (ShadowLimits(max_prompt_bytes=1024), 0, "prompt_limit"),
    (ShadowLimits(max_admission_units=1024), 0, "admission_budget_exhausted"),
    (ShadowLimits(max_reported_tokens=1), 1, "reported_token_limit"),
])
def test_prompt_admission_and_reported_usage_limits_are_distinct(source, limit, expected_calls, reason):
    driver = ScriptedDriver([{"action": "abstain", "reason": "unknown_condition"}])
    report = ShadowRunner(session(source), source[2], driver, limits=limit).run()
    assert report["termination_reason"] == reason and len(driver.calls) == expected_calls
    assert report["session"]["calls_used"] == 0
    if expected_calls:
        assert report["known_reported_tokens"] == 120  # Recorded overrun, not erased or called a hard billing cap.


def test_tool_batch_admission_does_not_partially_spend_an_oversized_batch(source):
    broker = session(source, limits=InvestigationLimits(max_tool_calls=1))
    driver = ScriptedDriver([read("paymentservice", "redis-cart")])
    report = ShadowRunner(broker, source[2], driver).run()
    assert report["termination_reason"] == "tool_budget_exhausted" and report["session"]["calls_used"] == 0


def test_post_provider_deadline_accounts_usage_but_discards_decision(source):
    ticks = [0]
    broker = session(source, clock=lambda: ticks[0])
    def delayed(_):
        ticks[0] = 181
        return read("paymentservice")
    driver = ScriptedDriver([delayed])
    report = ShadowRunner(broker, source[2], driver).run()
    assert report["termination_reason"] == "time_exhausted" and report["known_reported_tokens"] == 120
    assert report["session"]["calls_used"] == 0


def test_elapsed_time_before_request_prevents_any_driver_call(source):
    ticks = [0]
    broker = session(source, clock=lambda: ticks[0])
    runner = ShadowRunner(broker, source[2], ScriptedDriver([]))
    ticks[0] = 181
    assert runner.run()["termination_reason"] == "time_exhausted"


def test_sessions_and_runners_cannot_reset_model_budgets(source):
    broker = session(source)
    first = ShadowRunner(broker, source[2], ScriptedDriver([{"action": "abstain", "reason": "unknown_condition"}]))
    second = ShadowRunner(broker, source[2], ScriptedDriver([]))
    first.run()
    with pytest.raises(ValueError):
        first.run()
    with pytest.raises(ValueError):
        second.run()


def test_projection_omits_old_records_and_guessed_unshown_citations_are_denied(source):
    store, _, _ = source
    stamp = datetime.now(timezone.utc) - timedelta(seconds=20)
    rows = []
    for index in range(20):
        row = ObservationRecord(schema_version=2, environment_id=REGISTRY.environment_id,
            registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest, service_id="paymentservice",
            instance_id="a" * 64, measured_at=stamp + timedelta(milliseconds=index),
            collected_at=stamp + timedelta(milliseconds=index), status="CONNECTION_REFUSED", detail="private", quality="observed")
        rows.append(row)
    store.append_observations(rows)
    cutoff = datetime.now(timezone.utc)
    driver = ScriptedDriver([read("paymentservice"), {"action": "propose", "hypothesis": {
        "service_id": "paymentservice", "condition": "readiness_failure", "supporting_evidence": [rows[0].evidence_id]}}])
    report = ShadowRunner(session((store, cutoff, None)), fixture_topology(cutoff), driver).run()
    projection = report["turns"][0]["feedback"][0]["data"]
    assert len(projection["samples"]) == 8 and projection["prompt_omitted_samples"] == 13
    assert rows[0].evidence_id in report["session"]["issued_evidence_ids"]
    assert report["termination_reason"] == "unshown_citation"


def test_fixed_policy_and_scripted_loop_receive_identical_initial_information(source):
    first = ShadowRunner(session(source), source[2], FixedProbePolicy())
    other = ShadowRunner(session(source), source[2], ScriptedDriver([read("paymentservice"), suggest]))
    assert first.context_digest == other.context_digest
    fixed, scripted = first.run(), other.run()
    assert fixed["result"]["proposal"]["service_id"] == scripted["result"]["proposal"]["service_id"] == "paymentservice"
    assert fixed["provider_attempts"] == scripted["provider_attempts"] == 0
    assert fixed["session"]["calls_used"] == 8
    assert scripted["session"]["calls_used"] == 1  # Scripted behavior, NOT measured model superiority.
    assert fixed["known_reported_tokens"] == 0


def test_six_control_cases_have_no_external_model_or_causal_accuracy_claim():
    report = run_controls()
    assert report["passed"] and len(report["controls"]) == 6
    assert report["real_model_calls"] == 0 and report["causal_accuracy_measured"] is False
    assert all(row["report"]["session"]["calls_used"] <= 8 for row in report["controls"])


def test_cyclic_declared_dependencies_terminate_with_explicit_ambiguity(tmp_path):
    store = seed_case(tmp_path / "cycle.sqlite", {"paymentservice": "CONNECTION_REFUSED", "checkoutservice": "DEGRADED"})
    cutoff = datetime.now(timezone.utc)
    snapshot = fixture_topology(cutoff).model_copy(update={"edges": (("checkoutservice", "paymentservice"),
                                                                 ("paymentservice", "checkoutservice"))})
    report = ShadowRunner(session((store, cutoff, snapshot)), snapshot, FixedProbePolicy()).run()
    assert report["session"]["calls_used"] == 2 and not report["execution_authorized"]
    assert report["result"]["reason"] == "unresolved_alternatives"


@pytest.mark.parametrize("changes", [
    {"registry_digest": "f" * 64}, {"edges": (("frontend", "not-registered"),)},
    {"captured_at": datetime.now(timezone.utc) + timedelta(days=1)},
])
def test_invalid_or_future_topology_is_rejected_before_model_calls(source, changes):
    topology = source[2].model_copy(update=changes)
    with pytest.raises(ValueError):
        ShadowRunner(session(source), topology, ScriptedDriver([]))


@pytest.mark.parametrize("resource_first", [True, False])
def test_numeric_evidence_survives_other_views_of_the_same_observation(source, resource_first):
    store, _, _ = source
    with sqlite3.connect(store.path) as db:
        identifier, raw = db.execute("SELECT id,payload FROM evidence WHERE service='adservice'").fetchone()
        payload = json.loads(raw)
        payload["measurements"] = {"cpu_percent": 75}
        db.execute("UPDATE evidence SET payload=? WHERE id=?", (json.dumps(payload), identifier))
    broker = session(source)
    requests = [{"tool": "resource_window", "service_id": "adservice", "metric": "cpu_percent"},
                {"tool": "component_window", "service_id": "adservice"}]
    for request in requests if resource_first else reversed(requests):
        assert broker.call(request)["outcome"] == "ok"
    assert broker.propose({"service_id": "adservice", "condition": "resource_pressure",
                           "supporting_evidence": [identifier]})["outcome"] == "proposal"


def test_groq_adapter_uses_real_sdk_with_mock_transport_and_binds_caps():
    from langchain_openai import ChatOpenAI

    captured = []
    def handle(request):
        captured.append(request)
        body = json.loads(request.content)
        assert body.get("max_completion_tokens", body.get("max_tokens")) == 77
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(200, json={"id": "synthetic-wire-response", "object": "chat.completion",
            "created": 0, "model": "openai/gpt-oss-120b", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"action":"abstain","reason":"unknown_condition"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ChatOpenAI(model="openai/gpt-oss-120b", api_key="synthetic-key", max_retries=0,
                           base_url="https://api.groq.com/openai/v1", http_client=client)
        reply = GroqShadowDriver(model).complete([{"role": "user", "content": "synthetic"}],
                                                max_output_tokens=77, timeout_seconds=3)
    assert reply.usage["total_tokens"] == 14 and len(captured) == 1
    with pytest.raises(ValueError, match="retries"):
        GroqShadowDriver(SimpleNamespace(max_retries=2))


def test_shadow_import_loads_no_settings_provider_sdk_or_mutation_path():
    subprocess.run([sys.executable, "-c", "import sys; import agent.shadow; import agent.shadow_provider; "
        "assert not any(name in sys.modules for name in ('core.config','docker','agent.graph',"
        "'agent.nodes.reasoner','agent.nodes.executor','agent.tools.control_gateway','langchain_openai','openai'))"],
        check=True, capture_output=True)


def test_offline_controls_need_no_dotenv_runtime_settings_or_infrastructure(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    script = f"import sys; sys.path.insert(0, {root!r}); from eval.shadow_controls import run_controls; " \
        "report=run_controls(); assert report['passed']; assert 'core.config' not in sys.modules; " \
        "assert 'docker' not in sys.modules; assert 'agent.nodes.reasoner' not in sys.modules"
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env={}, check=True, capture_output=True)


def test_timestamp_public_import_keeps_its_previous_behavior():
    from core.telemetry import age_seconds
    from core.timestamps import age_seconds as pure

    assert age_seconds is pure
    assert pure(10, now=15) == 5
    assert pure(20, now=15) is None
    assert pure(True, now=15) is None
    assert pure("2026-01-01T00:00:00", now=15) is None


@pytest.mark.parametrize("target", ["paymentservice", "redis-cart"])
def test_scripted_decision_path_changes_after_different_observations(tmp_path, target):
    status = "CONNECTION_REFUSED" if target == "paymentservice" else "OOM_KILLED"
    store = seed_case(tmp_path / "adaptive.sqlite", {target: status})
    cutoff = datetime.now(timezone.utc)
    def adaptive(messages):
        view = json.loads(messages[-1]["content"])
        feedback = view["turns"][-1]["feedback"][0]
        if feedback["data"]["summary"]["status_at_window_end"] == "HEALTHY":
            return read("redis-cart")
        return {"action": "propose", "hypothesis": {"service_id": feedback["request"]["service_id"],
            "condition": "resource_pressure" if feedback["request"]["service_id"] == "redis-cart" else "readiness_failure",
            "supporting_evidence": [feedback["data"]["samples"][-1]["evidence_id"]]}}
    report = ShadowRunner(session((store, cutoff, None)), fixture_topology(cutoff),
                          ScriptedDriver([read("paymentservice"), adaptive, adaptive])).run()
    assert report["result"]["proposal"]["service_id"] == target
    assert report["session"]["calls_used"] == (1 if target == "paymentservice" else 2)
    assert report["driver_kind"] == "scripted" and report["provider_attempts"] == 0


def test_provider_factory_is_explicit_and_reuses_retry_disabled_groq_configuration(monkeypatch):
    import core.config
    import agent.nodes.reasoner as reasoner
    from agent.shadow_provider import configured_groq_driver

    model = SimpleNamespace(max_retries=0)
    monkeypatch.setattr(core.config, "settings", SimpleNamespace(llm_provider=SimpleNamespace(value="groq")))
    monkeypatch.setattr(reasoner, "_get_llm", lambda: model)
    assert configured_groq_driver().model is model
    monkeypatch.setattr(core.config, "settings", SimpleNamespace(llm_provider=SimpleNamespace(value="other")))
    with pytest.raises(ValueError, match="Groq"):
        configured_groq_driver()


def test_prompt_projection_discloses_omitted_missing_intervals():
    gaps = [{"seconds": index + 1} for index in range(6)]
    result = project_feedback({"outcome": "ok", "data": {"samples": [], "unobserved_intervals": gaps}})
    assert result["data"]["prompt_omitted_intervals"] == 3
    assert result["data"]["unobserved_intervals"] == gaps[-3:]


def test_shadow_and_standalone_adapter_disable_automatic_tracing(source, monkeypatch):
    from langsmith import get_tracing_context

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    def checked(_):
        assert get_tracing_context()["enabled"] is False
        return {"action": "abstain", "reason": "unknown_condition"}
    report = ShadowRunner(session(source), source[2], ScriptedDriver([checked])).run()
    assert report["result"]["outcome"] == "abstained"
    class Model:
        max_retries = 0

        def bind(self, **_):
            return self

        def invoke(self, _):
            assert get_tracing_context()["enabled"] is False
            return SimpleNamespace(content="{}", usage_metadata=dict(USAGE))
    assert GroqShadowDriver(Model()).complete([], max_output_tokens=10, timeout_seconds=1).usage == USAGE
