from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from eval.context_ablation import context_library, risk_ranked_choice, selection_fixture, summarize_pairs
from eval.live_acceptance import wait_detected
from simulation.application_canary import check_application
from eval.plot_controls import prompt_character_rows
from scripts.check_environment import api_reachability


def test_selection_fixture_includes_real_multiple_candidate_condition():
    cases = selection_fixture()["scenarios"]
    assert len(cases) == 9
    cpu = next(case for case in cases if case["id"] == "cpu-multiple-sops")
    assert cpu["observations"]["adservice"] == "HIGH_CPU"


def test_synthetic_library_never_creates_executable_candidates():
    rows = [{"name": "Restart", "description": "Restart", "risk_level": "MEDIUM",
             "applies_to": ["frontend"], "trigger_condition": "CRASH_LOOPING"}]
    library = context_library(rows, 50)
    assert len(library) == 51
    assert all(row.get("script_path") is None and row["applies_to"] == ["synthetic-unrelated-service"]
               for row in library[1:])
    assert "Applies to: frontend" in library[0]["description"]
    assert "Trigger: CRASH_LOOPING" in library[0]["description"]
    assert risk_ranked_choice([{"name": "Danger", "risk_level": "HIGH"}]) is None
    assert risk_ranked_choice([{"name": "Restart", "risk_level": "MEDIUM"},
                               {"name": "Flush", "risk_level": "LOW"}]) == "Flush"


def test_ablation_errors_stay_in_selection_denominator():
    pairs = [{"distractors": 0, "risk_ranked_choice": "Restart",
              "candidate_context": {"valid_selection": True, "prompt_characters": 100, "usage": None, "latency_s": 1}},
             {"distractors": 0, "risk_ranked_choice": "Restart",
              "candidate_context": {"error": "timeout", "prompt_characters": 100, "usage": None, "latency_s": 2}}]
    result = summarize_pairs(pairs)["0"]["treatments"]["candidate_context"]
    assert result["n_failed"] == 1
    assert result["valid_selection_rate"] == 0.5
    assert result["tokens"]["mean"] is None


def test_plot_cannot_relabel_paid_or_incomplete_results_as_no_model_controls():
    with pytest.raises(ValueError):
        prompt_character_rows({"metadata": {"kind": "paired_context_ablation"}})
    with pytest.raises(ValueError):
        prompt_character_rows({"metadata": {"kind": "selection_policy"},
                               "call_budget": {"attempted": 0}, "completed": False})


def test_preflight_checks_api_liveness_without_a_model_request(monkeypatch):
    response = SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})
    get = Mock(return_value=response)
    monkeypatch.setattr("scripts.check_environment.httpx.get", get)
    assert api_reachability()
    assert get.call_args.args[0].endswith("/health")
    response.status_code = 503
    assert not api_reachability()


def test_live_correlation_checks_actual_collector_condition_field(monkeypatch):
    monkeypatch.setattr("eval.live_acceptance.matching_incident", lambda *args: (
        {"condition": "OOM_KILLED", "alert_id": "INC-X"}, None))
    assert wait_detected("redis-cart", "OOM_KILLED", 0)["alert_id"] == "INC-X"
    with pytest.raises(RuntimeError, match="different condition"):
        wait_detected("redis-cart", "STALE_DATA", 0)


def test_canary_requires_cart_contents_and_checkout_marker(monkeypatch):
    responses = [SimpleNamespace(status_code=200, text="home"),
                 SimpleNamespace(status_code=200, text="0PUK6V6EV0"),
                 SimpleNamespace(status_code=200, text="Cart (1)"),
                 SimpleNamespace(status_code=200, text="Not an order confirmation"),
                 SimpleNamespace(status_code=200, text="home")]
    client = Mock()
    client.cookies = {"session": "synthetic"}
    client.request.side_effect = responses
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    constructor = Mock(return_value=client)
    monkeypatch.setattr("simulation.application_canary.httpx.Client", constructor)
    result = check_application()
    assert not result["success"]
    assert result["steps"][3]["marker_found"] is False
    assert result["steps"][-1]["step"] == "cleanup_cart"
    assert constructor.call_args.kwargs["base_url"] == "http://127.0.0.1:8080"
