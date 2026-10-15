"""Graph iframes must carry their own assets, not relative Streamlit URLs."""

from html.parser import HTMLParser

import pytest

pytest.importorskip("pyvis")

from dashboard.components.graph_viz import UNKNOWN_COLOR, _compute_blast_radius, build_network
from dashboard.components.skill_graph import build_skill_network


class ScriptSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and (src := dict(attrs).get("src")):
            self.sources.append(src)


class GraphFixture:
    def get_service_snapshot(self):
        return {name: {"status": "HEALTHY", "age_seconds": 0} for name in ("frontend", "redis-cart")}

    def _run(self, query):
        if "DEPENDS_ON" in query:
            return [{"src": "frontend", "dst": "redis-cart"}]
        if "NEXT_IF_FAIL" in query:
            return []
        return [{"name": "Redis_Restart_SOP", "risk": "MEDIUM", "trigger": "OOM_KILLED",
                 "script": "/sops/redis/restart.sh", "services": ["redis-cart"]}]


@pytest.mark.parametrize("renderer", [build_network, build_skill_network])
def test_graph_resources_are_embedded(renderer):
    html = renderer(GraphFixture())
    parser = ScriptSources()
    parser.feed(html)
    assert not any(src.startswith("lib/") or "vis-network" in src for src in parser.sources)
    assert "function neighbourhoodHighlight" in html
    assert "new vis.Network" in html


def test_stale_and_unknown_nodes_do_not_assert_root_cause_or_impact():
    graph = GraphFixture()
    graph.get_service_snapshot = lambda: {
        "frontend": {"status": "HEALTHY", "age_seconds": 1000},
        "redis-cart": {"status": "UNKNOWN", "age_seconds": 0},
    }
    html = build_network(graph)
    assert UNKNOWN_COLOR in html and "not current health" in html
    assert "ROOT CAUSE" not in html and "impacted - in blast radius" not in html
    assert _compute_blast_radius({"frontend": "HEALTHY", "redis-cart": "STALE"},
                                [("frontend", "redis-cart")]) == set()
