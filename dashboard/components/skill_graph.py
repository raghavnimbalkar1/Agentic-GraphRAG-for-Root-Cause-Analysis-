"""
dashboard/components/skill_graph.py

Renders the SECOND half of the dual graph — the Semantic Skill Graph — as an
interactive network read live from Neo4j. This is the "HOW" layer: the
remediation SOPs (Skill nodes), which services they APPLY_TO, and the
NEXT_IF_FAIL fallback chains the agent walks when a first remediation does not
restore health.

Where graph_viz.py shows the infrastructure graph (the WHERE — services +
DEPENDS_ON), this shows the knowledge that makes remediation autonomous: every
edge here is data in Neo4j, not code. Adding a Skill node + APPLIES_TO edge
teaches the agent a new remediation with zero code change — this view is the
proof of that claim.
"""

from __future__ import annotations

from pyvis.network import Network

from graph.graph_client import GraphClient

# Risk → colour. The agent prefers the lowest-risk applicable SOP first.
RISK_COLOR = {
    "LOW":    "#2ecc71",   # green  — network-only sandbox, no Docker socket
    "MEDIUM": "#f39c12",   # amber  — Docker socket, controls sibling containers
    "HIGH":   "#e74c3c",   # red    — reserved / human-confirmation tier
}
SERVICE_COLOR    = "#3498db"   # blue  — infrastructure node
APPLIES_EDGE     = "#7f8c8d"
FALLBACK_EDGE    = "#e67e22"   # orange — NEXT_IF_FAIL chain


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

    net = Network(height=height, width="100%", bgcolor="#0e1117",
                  font_color="#fafafa", directed=True, notebook=False)
    net.barnes_hut(gravity=-12000, central_gravity=0.25, spring_length=170,
                   spring_strength=0.03, damping=0.9)

    # Service nodes (the anchors)
    for svc in services:
        net.add_node(f"svc::{svc}", label=svc, color=SERVICE_COLOR,
                     shape="database" if svc == "redis-cart" else "box",
                     title=f"Service: {svc}", size=22, borderWidth=2)

    # Skill nodes, coloured by risk, with a rich hover tooltip
    for sk in skills:
        risk = (sk.get("risk") or "LOW").upper()
        title = (f"SOP: {sk['name']}\n"
                 f"risk: {risk}\n"
                 f"trigger: {sk.get('trigger') or '—'}\n"
                 f"script: {sk.get('script') or '—'}")
        net.add_node(f"sop::{sk['name']}", label=sk["name"],
                     color=RISK_COLOR.get(risk, "#95a5a6"),
                     shape="dot", title=title, size=16, borderWidth=1)
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
      "interaction": {"hover": true, "tooltipDelay": 80},
      "physics": {"stabilization": {"iterations": 200}},
      "nodes": {"font": {"size": 14, "face": "monospace"}}
    }
    """)
    return net.generate_html(notebook=False)


def skill_summary(gc: GraphClient) -> dict:
    """Headline counts for the skill graph."""
    skills = _skills(gc)
    triggers = {sk.get("trigger") for sk in skills if sk.get("trigger")}
    multi = [sk["name"] for sk in skills]
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
