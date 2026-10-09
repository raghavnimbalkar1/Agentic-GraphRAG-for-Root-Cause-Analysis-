from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.llm_usage import token_usage
from eval.context_ablation import is_off_target, parse_decision
from eval.controlled import ControlledGraph, load_fixture
from eval.metrics import distribution, score_prediction, summarize
from eval.research_benchmark import measure
from graph.graph_client import GraphClient


def test_empty_distribution_is_not_a_zero_second_result():
    assert distribution([None])["mean"] is None
    assert distribution([0])["mean"] == 0


def test_wrong_root_is_scored_from_its_prediction_not_ground_truth():
    scenario = load_fixture()["scenarios"][0]
    result = score_prediction({"root": "adservice", "potential_blast": ["frontend", "loadgenerator"]}, scenario)
    assert not result["root_correct"]
    assert result["potential_blast_f1"] == pytest.approx(2 / 3)


def test_failed_trials_remain_in_denominator():
    scenario = load_fixture()["scenarios"][0]
    success = measure("system", scenario, 0, lambda: {"root": "redis-cart", "potential_blast": scenario["truth_potential_blast"]})
    failure = measure("system", scenario, 1, Mock(side_effect=TimeoutError("unavailable")))
    summary = summarize([success, failure])["system"]
    assert summary["n_attempted"] == 2
    assert summary["n_failed"] == 1
    assert summary["root_accuracy"] == 0.5
    assert summary["tokens"]["mean"] is None


def test_isolated_fixture_cleanup_is_scope_limited_even_on_failure():
    client = Mock()
    with pytest.raises(RuntimeError):
        with ControlledGraph(client, [["a", "b"]], {"b": "DOWN"}) as graph:
            graph.localise({"service": "a", "error_type": "DEGRADED"})
            raise RuntimeError("failure")
    queries = [call.args[0] for call in client._run.call_args_list]
    assert all(":Service" not in query for query in queries)
    assert "scope_id:$scope" in queries[-1]
    assert client.get_root_cause.call_args.kwargs["scope_id"]


def test_evaluation_label_requires_scope():
    client = object.__new__(GraphClient)
    with pytest.raises(ValueError, match="scope_id"):
        client.get_root_cause("frontend", "DEGRADED", node_label="EvalService")


def test_shared_skill_has_all_applicable_targets_not_last_row_only():
    applicability = {"Restart": {"frontend", "cartservice"}}
    assert not is_off_target("Restart", "cartservice", applicability)
    assert is_off_target("Unknown", "cartservice", applicability)


def test_missing_usage_remains_null_and_zero_is_measured():
    assert token_usage(SimpleNamespace())["total_tokens"] is None
    assert token_usage(SimpleNamespace(usage_metadata={"input_tokens": 0, "output_tokens": 0}))["total_tokens"] == 0


def test_hand_reviewed_fixture_blast_and_depth_are_consistent():
    import networkx as nx
    fixture = load_fixture()
    graph = nx.DiGraph(fixture["edges"])
    for scenario in fixture["scenarios"]:
        assert set(scenario["truth_potential_blast"]) == nx.ancestors(graph, scenario["truth_root"])
        paths = list(nx.all_simple_paths(graph, scenario["alert"]["service"], scenario["truth_root"]))
        depth = max((len(path) - 1 for path in paths), default=0)
        assert scenario["depth"] == depth


@pytest.mark.parametrize("raw", ['[]', '{}', '{"action":"execute","chosen_skill":null}',
                                 '{"action":"skip","chosen_skill":"Restart"}'])
def test_invalid_ablation_json_is_an_error_not_a_valid_zero_hallucination(raw):
    with pytest.raises(ValueError):
        parse_decision(raw)


def test_off_target_ablation_choice_is_retained_for_scoring():
    decision = parse_decision('{"action":"execute","chosen_skill":"Unknown",'
                              '"reason":"test","root_cause_explanation":"test"}')
    assert is_off_target(decision["chosen_skill"], "redis-cart", {})
