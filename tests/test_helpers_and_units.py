"""
Pure-function units: SOP path resolution, blast-radius closure, F1 scoring,
report writing, and the GraphClient singleton's thread safety.
"""

from __future__ import annotations

import threading

from agent.nodes.executor import _resolve_host_path, PROJECT_ROOT
from dashboard.components.graph_viz import _compute_blast_radius
from eval.benchmark_full import _f1


# ── executor: Neo4j path → host path ───────────────────────────────────────

def test_resolve_neo4j_style_path():
    out = _resolve_host_path("/sops/redis/restart.sh")
    assert out == str(PROJECT_ROOT / "sops" / "redis" / "restart.sh")


def test_resolve_path_without_leading_slash():
    out = _resolve_host_path("sops/adservice/throttle.sh")
    assert out == str(PROJECT_ROOT / "sops" / "adservice" / "throttle.sh")


# ── dashboard: transitive blast radius ─────────────────────────────────────

EDGES = [
    ("frontend", "cartservice"),
    ("cartservice", "redis-cart"),
    ("checkoutservice", "cartservice"),
    ("frontend", "adservice"),
]


def test_blast_radius_walks_transitive_dependents():
    statuses = {"redis-cart": "OOM_KILLED", "cartservice": "HEALTHY",
                "frontend": "HEALTHY", "checkoutservice": "HEALTHY",
                "adservice": "HEALTHY"}
    blast = _compute_blast_radius(statuses, EDGES)
    assert blast == {"cartservice", "frontend", "checkoutservice"}


def test_blast_radius_empty_when_all_healthy():
    statuses = {s: "HEALTHY" for s in
                ("redis-cart", "cartservice", "frontend", "checkoutservice")}
    assert _compute_blast_radius(statuses, EDGES) == set()


# ── eval: blast-radius F1 ──────────────────────────────────────────────────

def test_f1_exact_match_is_one():
    assert _f1(["a", "b"], ["a", "b"]) == 1.0


def test_f1_disjoint_is_zero():
    assert _f1(["a"], ["b"]) == 0.0


def test_f1_both_empty_is_one():
    assert _f1([], []) == 1.0


def test_f1_partial_overlap():
    # precision 0.5, recall 0.5 → F1 0.5
    assert _f1(["a", "b"], ["b", "c"]) == 0.5


# ── graph client: singleton must connect exactly once under races ─────────

def test_singleton_connects_once_across_threads(monkeypatch):
    import graph.graph_client as gcm

    # Reset the class-level singleton, then stub out the real connection.
    monkeypatch.setattr(gcm.GraphClient, "_instance", None)
    monkeypatch.setattr(gcm.GraphClient, "_driver", None)
    connects = []

    def fake_connect(self):
        connects.append(1)
        self._driver = object()

    monkeypatch.setattr(gcm.GraphClient, "_connect", fake_connect)

    barrier = threading.Barrier(16)

    def construct():
        barrier.wait()
        gcm.GraphClient()

    threads = [threading.Thread(target=construct) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(connects) == 1
