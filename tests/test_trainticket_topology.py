"""
TrainTicket topology integrity + the node_label allowlist that keeps the
parameterised traversal safe. Pure data / logic — no Neo4j, no LLM.

The longest-path check structurally proves each scenario's claimed depth (Q1
returns the LONGEST path to the root), so the "depth 7 on 36 services" claim is
verified here, not just asserted.
"""

from __future__ import annotations

from collections import defaultdict

from graph.graph_client import GraphClient
from eval.trainticket.topology import TT_EDGES, TT_SCENARIOS, TT_SERVICES


def _longest_path_len(src: str, dst: str) -> int:
    """Longest simple path length (edge count) from src to dst in the DAG,
    or -1 if unreachable. Valid because the topology is acyclic."""
    adj = defaultdict(list)
    for a, b in TT_EDGES:
        adj[a].append(b)
    memo: dict[str, int] = {}

    def dfs(n: str) -> int:
        if n == dst:
            return 0
        if n in memo:
            return memo[n]
        best = -1
        for nb in adj[n]:
            d = dfs(nb)
            if d >= 0:
                best = max(best, d + 1)
        memo[n] = best
        return best

    return dfs(src)


def test_edges_reference_only_known_services():
    known = set(TT_SERVICES)
    for a, b in TT_EDGES:
        assert a in known, f"unknown source {a}"
        assert b in known, f"unknown target {b}"


def test_no_self_loops():
    assert all(a != b for a, b in TT_EDGES)


def test_topology_is_acyclic():
    adj = defaultdict(list)
    for a, b in TT_EDGES:
        adj[a].append(b)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {s: WHITE for s in TT_SERVICES}

    def has_cycle(n):
        colour[n] = GREY
        for nb in adj[n]:
            if colour[nb] == GREY:
                return True
            if colour[nb] == WHITE and has_cycle(nb):
                return True
        colour[n] = BLACK
        return False

    assert not any(has_cycle(s) for s in TT_SERVICES if colour[s] == WHITE)


def test_scenarios_match_their_claimed_depth():
    # Q1 returns the deepest (longest) path to the root; each scenario's depth
    # must equal the actual longest path from its alert service to its root.
    for sc in TT_SCENARIOS:
        got = _longest_path_len(sc["alert_service"], sc["root"])
        assert got == sc["depth"], (
            f"{sc['id']}: claimed depth {sc['depth']} but longest path "
            f"{sc['alert_service']}→{sc['root']} is {got}")


def test_depth_reaches_seven():
    assert max(sc["depth"] for sc in TT_SCENARIOS) == 7, \
        "the headline is a depth-7 cascade — deeper than Online Boutique"


def test_scenarios_span_full_range():
    depths = {sc["depth"] for sc in TT_SCENARIOS}
    assert depths == {1, 2, 3, 4, 5, 6, 7}


def test_node_label_allowlist_is_locked_down():
    # Labels can't be Cypher parameters, so get_root_cause interpolates node_label.
    # The allowlist is the safety boundary for that interpolation.
    assert GraphClient._VALID_LABELS == {"Service", "TTService"}
