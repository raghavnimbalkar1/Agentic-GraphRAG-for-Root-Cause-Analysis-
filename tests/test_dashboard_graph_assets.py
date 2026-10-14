"""Graph iframes must carry their own assets, not relative Streamlit URLs."""

from html.parser import HTMLParser

import pytest

pytest.importorskip("pyvis")

from dashboard.components.graph_viz import build_network
from dashboard.components.skill_graph import build_skill_network


class ScriptSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and (src := dict(attrs).get("src")):
            self.sources.append(src)


class GraphFixture:
    def get_all_service_statuses(self):
        return {"frontend": "HEALTHY", "redis-cart": "HEALTHY"}

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
