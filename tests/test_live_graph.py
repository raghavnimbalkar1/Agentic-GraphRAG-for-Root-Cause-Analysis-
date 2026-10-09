"""Opt-in read-only catalog checks and UUID-scoped Neo4j integration fixtures."""

import os
import pytest

from eval.controlled import ControlledGraph, load_fixture
from graph.graph_client import GraphClient
from graph.scripts.init_graph import verify_counts, smoke_test

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.getenv("RCA_LIVE_GRAPH_TESTS") != "1", reason="Live Neo4j checks are opt-in")]


def test_seeded_catalog_read_only():
    client = GraphClient()
    before = client.get_service_snapshot()
    assert verify_counts(client)
    assert smoke_test(client)
    after = client.get_service_snapshot()
    assert {name: row["status"] for name, row in before.items()} == {name: row["status"] for name, row in after.items()}


def test_real_q1_and_predicted_blast_on_isolated_fixtures():
    client = GraphClient()
    fixture = load_fixture()
    for scenario in fixture["scenarios"]:
        with ControlledGraph(client, fixture["edges"], scenario["observations"]) as graph:
            result = graph.localise(scenario["alert"])
            assert result.root_cause_node == scenario["truth_root"]
            assert result.depth == scenario["depth"]
            assert set(graph.blast(result.root_cause_node)) == set(scenario["truth_potential_blast"])
