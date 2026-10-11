import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import networkx as nx
import pytest

from core.audit import write_json_atomic
from eval.baselines.topology_text import TopologyTextBaseline
from eval.controlled import ControlledGraph
from eval.metrics import summarize
from eval.prediction import parse_prediction
from eval.protocol import check_call_budget, matched_fixture, select_scenarios
from eval.research_benchmark import measure, run
from simulation.incident_tracking import collector_ready


def test_protocol_truth_and_matched_blocks():
    fixture = matched_fixture()
    assert len(fixture["scenarios"]) == 22
    assert len({case["id"] for case in fixture["scenarios"]}) == 22
    graph = nx.DiGraph(fixture["edges"])
    for case in fixture["scenarios"]:
        assert set(case["observations"]) == set(graph.nodes)
        assert set(case["observation_age_seconds"]) == set(graph.nodes)
        if case["truth_root"] is not None:
            assert set(case["truth_potential_blast"]) == nx.ancestors(graph, case["truth_root"])
            paths = list(nx.all_simple_paths(graph, case["alert"]["service"], case["truth_root"]))
            assert case["depth"] == max((len(path) - 1 for path in paths), default=0)
    for specificity in ("generic", "specific"):
        cases = [case for case in fixture["scenarios"] if case.get("alert_specificity") == specificity]
        assert len(cases) == 4
        assert len({case["alert"]["message"] for case in cases}) == 1
        assert len({case["alert"]["error_type"] for case in cases}) == 1
        assert all(case["observations"] == cases[0]["observations"] for case in cases)


def test_call_budget_checked_before_database_or_model(monkeypatch):
    client = Mock()
    monkeypatch.setattr("graph.graph_client.GraphClient", client)
    with pytest.raises(ValueError, match="132 model calls"):
        run(3, with_llm=True, max_calls=30)
    client.assert_not_called()
    check_call_budget(0, 0)
    with pytest.raises(ValueError):
        check_call_budget(1, 0)
    with pytest.raises(ValueError, match="Unknown scenarios"):
        select_scenarios(matched_fixture(), ["not-a-case"])


@pytest.mark.parametrize("data", [
    {}, {"root_cause": "invented", "blast_radius": []},
    {"root_cause": "frontend", "blast_radius": ["frontend"]},
    {"root_cause": None, "blast_radius": [], "outcome": "root"},
    {"root_cause": None, "blast_radius": ["frontend"], "outcome": "review"},
    {"root_cause": "frontend", "blast_radius": [], "outcome": "none"},
])
def test_prediction_contract_rejects_invalid_or_inconsistent_outputs(data):
    with pytest.raises(ValueError):
        parse_prediction(json.dumps(data), ["frontend"])


def test_review_is_a_valid_prediction_but_a_failed_trial_is_not_credit():
    data = {"root_cause": None, "outcome": "review", "blast_radius": []}
    assert parse_prediction(json.dumps(data), ["frontend"])["outcome"] == "review"
    case = next(case for case in matched_fixture()["scenarios"] if case["id"] == "negative-unknown")
    failure = measure("x", case, 0, Mock(side_effect=TimeoutError()))
    review = measure("x", case, 1, lambda: {"root": None, "outcome": "review", "potential_blast": []})
    assert failure["decision_correct"] is False
    result = summarize([failure, review])["x"]
    assert result["root_accuracy"] is None
    assert result["negative_control_accuracy"] == 0.5
    assert result["potential_blast_f1"]["mean"] is None


def test_guarded_graph_declines_unknown_stale_healthy_and_ambiguous():
    client = Mock()
    graph = ControlledGraph(client, [["frontend", "redis-cart"]], {"frontend": "HEALTHY", "redis-cart": "UNKNOWN"})
    alert = {"service": "frontend", "error_type": "DEP_ERROR"}
    assert graph.predict(alert)["outcome"] == "review"
    client.get_root_cause.assert_not_called()
    graph.observations["redis-cart"] = "HEALTHY"
    client.get_root_cause.return_value = SimpleNamespace(candidate_roots=["frontend"], root_cause_node="frontend")
    assert graph.predict(alert)["outcome"] == "none"
    assert graph.predict(alert, {"frontend": 0, "redis-cart": 120})["outcome"] == "review"
    client.get_root_cause.return_value = SimpleNamespace(candidate_roots=["redis-cart", "adservice"])
    assert graph.predict(alert)["outcome"] == "review"


def test_topology_text_receives_same_snapshot_and_actual_edges():
    baseline = TopologyTextBaseline([["frontend", "redis-cart"]])
    prompt = baseline._build_prompt({"observations": {"redis-cart": "OOM_KILLED"},
                                     "observation_age_seconds": {"redis-cart": 0}})
    assert '["frontend", "redis-cart"]' in prompt
    assert "OOM_KILLED" in prompt and "OBSERVATION AGE SECONDS" in prompt


def test_observe_only_heartbeat_does_not_authorize_chaos(tmp_path):
    state = {"timestamp": time.time(), "graph_synced": True, "delivery_enabled": False}
    path = tmp_path / "collector_status.json"
    write_json_atomic(path, state)
    assert collector_ready(tmp_path, require_delivery=False)
    assert not collector_ready(tmp_path)
    state["delivery_enabled"] = True
    write_json_atomic(path, state)
    assert collector_ready(tmp_path)
