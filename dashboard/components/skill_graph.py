"""
dashboard/components/skill_graph.py

Renders the SECOND half of the dual graph — the Semantic Skill Graph — as an
interactive network read live from Neo4j. This is the "HOW" layer: the
remediation SOPs (Skill nodes), which services they APPLY_TO, and the
NEXT_IF_FAIL fallback chains the agent walks when a first remediation does not
restore health.

Where graph_viz.py shows the infrastructure graph (the WHERE — services +
DEPENDS_ON), this shows declared remediation applicability. Graph registration
alone does not authorize a new executable: the local operation allowlist and
reviewed implementation must also support it. Versioned research contracts are
inspected separately; this visualization is not an execution authorization.
"""

from __future__ import annotations

from textwrap import fill

from pyvis.network import Network

from graph.graph_client import GraphClient
from dashboard.graph_style import finish_network

# Risk → colour. The agent prefers the lowest-risk applicable SOP first.
RISK_COLOR = {
    "LOW":    "#5B9379",
    "MEDIUM": "#B69B58",
    "HIGH":   "#C5786C",
}
SERVICE_COLOR    = "#7999AB"
APPLIES_EDGE     = "#C8D2C9"
FALLBACK_EDGE    = "#B69B58"


def _skills(gc: GraphClient) -> list[dict]:
    return gc._run(
        "MATCH (k:Skill) "
        "OPTIONAL MATCH (k)-[:APPLIES_TO]->(s:Service) "
        "RETURN k.name AS name, k.risk_level AS risk, "
        "       k.trigger_condition AS trigger, k.script_path AS script, "
        "       collect(s.name) AS services"
    )


def _fallback_edges(gc: GraphClient) -> list[tuple[str, str]]:
    rows = gc._run("MATCH (a:Skill)-[:NEXT_IF_FAIL]->(b:Skill) "
                   "RETURN a.name AS src, b.name AS dst")
    return [(r["src"], r["dst"]) for r in rows]


def build_skill_network(gc: GraphClient, height: str = "600px") -> str:
    """Build the skill graph and return HTML for st.components.v1.html()."""
    skills = _skills(gc)
    services = sorted({s for sk in skills for s in sk["services"]})

    net = Network(height=height, width="100%", bgcolor="#ffffff",
                  font_color="#425D4C", directed=True, notebook=False,
                  cdn_resources="in_line")
    # Service nodes (the anchors)
    for svc in services:
        net.add_node(f"svc::{svc}", label=svc, color={"background": "#EAF0F4", "border": SERVICE_COLOR},
                     shape="database" if svc == "redis-cart" else "box",
                     title=f"Service: {svc}", size=22, borderWidth=2)

    # Skill nodes, coloured by risk, with a rich hover tooltip
    for sk in skills:
        risk = (sk.get("risk") or "LOW").upper()
        title = (f"SOP: {sk['name']}\n"
                 f"risk: {risk}\n"
                 f"trigger: {sk.get('trigger') or '—'}\n"
                 f"script: {sk.get('script') or '—'}")
        label = fill(sk["name"].removesuffix("_SOP").replace("_", " "), width=19, break_long_words=False)
        net.add_node(f"sop::{sk['name']}", label=label,
                     color={"background": "#E7EFE7" if risk == "LOW" else "#F5EFDF",
                            "border": RISK_COLOR.get(risk, "#9FAEA3")},
                     shape="box", title=title, size=16, borderWidth=1, margin=8)
        for svc in sk["services"]:
            net.add_edge(f"sop::{sk['name']}", f"svc::{svc}",
                         color=APPLIES_EDGE, width=1, arrows="to",
                         title="APPLIES_TO")

    # NEXT_IF_FAIL fallback chains — the multi-step remediation edges
    for src, dst in _fallback_edges(gc):
        net.add_edge(f"sop::{src}", f"sop::{dst}", color=FALLBACK_EDGE,
                     width=3, arrows="to", dashes=True, title="NEXT_IF_FAIL")

    net.set_options("""
    {
      "layout": {"randomSeed": 12},
      "interaction": {"hover": true, "tooltipDelay": 80},
      "physics": {"solver": "barnesHut", "stabilization": {"iterations": 600},
                  "barnesHut": {"gravitationalConstant": -2200, "centralGravity": 0.3,
                                "springLength": 120, "springConstant": 0.04,
                                "damping": 0.8, "avoidOverlap": 1}},
      "edges": {"arrows": {"to": {"scaleFactor": 0.5}}},
      "nodes": {"shapeProperties": {"borderRadius": 5},
                "font": {"size": 12, "face": "Arial", "color": "#425D4C"}}
    }
    """)
    return finish_network(net.generate_html(notebook=False))


def skill_summary(gc: GraphClient) -> dict:
    """Headline counts for the skill graph."""
    skills = _skills(gc)
    triggers = {sk.get("trigger") for sk in skills if sk.get("trigger")}
    by_service: dict[str, int] = {}
    for sk in skills:
        for svc in sk["services"]:
            by_service[svc] = by_service.get(svc, 0) + 1
    fallbacks = _fallback_edges(gc)
    return {
        "n_skills": len(skills),
        "n_triggers": len(triggers),
        "n_fallbacks": len(fallbacks),
        "max_candidates": max(by_service.values()) if by_service else 0,
        "max_candidates_service": max(by_service, key=by_service.get) if by_service else "—",
        "skills": skills,
    }
