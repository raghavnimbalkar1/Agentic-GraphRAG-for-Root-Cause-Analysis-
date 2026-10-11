"""Opt-in read-only catalog checks and UUID-scoped Neo4j integration fixtures."""

import os
import pytest

from eval.controlled import ControlledGraph, load_fixture
from graph.graph_client import GraphClient
from graph.scripts.init_graph import verify_counts, smoke_test
from eval.protocol import matched_fixture

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.getenv("RCA_LIVE_GRAPH_TESTS") != "1", reason="Live Neo4j checks are opt-in")]


@pytest.fixture
def client():
    client = GraphClient()
    yield client
    client.close()


def test_seeded_catalog_read_only(client):
    before = client.get_service_snapshot()
    assert verify_counts(client)
    assert smoke_test(client)
    after = client.get_service_snapshot()
    assert {name: row["status"] for name, row in before.items()} == {name: row["status"] for name, row in after.items()}


def test_real_q1_and_predicted_blast_on_isolated_fixtures(client):
    fixture = load_fixture()
    for scenario in fixture["scenarios"]:
        with ControlledGraph(client, fixture["edges"], scenario["observations"]) as graph:
            result = graph.localise(scenario["alert"])
            assert result.root_cause_node == scenario["truth_root"]
            assert result.depth == scenario["depth"]
            assert set(graph.blast(result.root_cause_node)) == set(scenario["truth_potential_blast"])


def test_real_matched_and_negative_controls(client):
    fixture = matched_fixture()
    for scenario in fixture["scenarios"]:
        with ControlledGraph(client, fixture["edges"], scenario["observations"]) as graph:
            result = graph.predict(scenario["alert"], scenario["observation_age_seconds"])
            assert result["root"] == scenario["truth_root"]
            assert result["outcome"] == scenario["truth_outcome"]
            assert set(result["potential_blast"]) == set(scenario["truth_potential_blast"])
