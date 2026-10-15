"""
dashboard/components/graph_viz.py

Renders the Online Boutique dependency graph (Neo4j Infrastructure KG) as an
interactive force-directed network, colour-coded by live service health.

Graph observations have a bounded lifetime. Expired or unknown samples are gray,
observed faults are red, and healthy potential dependents are amber. Structural
exposure does not establish user-visible impact or a confirmed root cause.
"""

from __future__ import annotations

from pyvis.network import Network

from core.telemetry import fresh_service_statuses
from graph.graph_client import GraphClient

# ── Health → colour mapping ──────────────────────────────────────────────────

HEALTHY_COLOR    = "#2ecc71"   # green
ROOT_FAULT_COLOR = "#e74c3c"   # red    — an observed unhealthy service
BLAST_COLOR      = "#f39c12"   # amber  — healthy but downstream of an unhealthy node
EDGE_COLOR       = "#7f8c8d"
EDGE_FAULT_COLOR = "#e74c3c"
UNKNOWN_COLOR   = "#92979e"

# Services that are infrastructure (DB/cache) get a different shape.
DATASTORE_NODES = {"redis-cart"}


def _depends_on_edges(gc: GraphClient) -> list[tuple[str, str]]:
    """Return all (source, target) DEPENDS_ON edges."""
    rows = gc._run(
        "MATCH (a:Service)-[:DEPENDS_ON]->(b:Service) "
        "RETURN a.name AS src, b.name AS dst"
    )
    return [(r["src"], r["dst"]) for r in rows]


def _compute_blast_radius(
    statuses: dict[str, str],
    edges: list[tuple[str, str]],
) -> set[str]:
    """
    A service is in the blast radius if it (transitively) DEPENDS_ON any
    unhealthy service. Walks upstream from each unhealthy node.
    """
    unhealthy = {svc for svc, st in statuses.items() if st not in {"HEALTHY", "UNKNOWN", "STALE"}}
    if not unhealthy:
        return set()

    # Build reverse adjacency: who depends on X
    dependents: dict[str, list[str]] = {}
    for src, dst in edges:
        dependents.setdefault(dst, []).append(src)

    blast: set[str] = set()
    frontier = list(unhealthy)
    while frontier:
        node = frontier.pop()
        for up in dependents.get(node, []):
            if up not in blast and up not in unhealthy:
                blast.add(up)
                frontier.append(up)
    return blast


def build_network(gc: GraphClient, height: str = "560px", *, snapshot: dict | None = None) -> str:
    """
    Build the dependency graph and return it as an HTML string ready for
    st.components.v1.html().
    """
    snapshot = gc.get_service_snapshot() if snapshot is None else snapshot
    statuses = fresh_service_statuses(snapshot)
    edges = _depends_on_edges(gc)
    for src, dst in edges:
        statuses.setdefault(src, "UNKNOWN")
        statuses.setdefault(dst, "UNKNOWN")
    blast = _compute_blast_radius(statuses, edges)

    net = Network(
        height=height,
        width="100%",
        bgcolor="#0e1117",
        font_color="#fafafa",
        directed=True,
        notebook=False,
        cdn_resources="in_line",
    )
    # Stabilised physics so the layout settles quickly during a live demo.
    net.barnes_hut(gravity=-8000, central_gravity=0.3, spring_length=140,
                   spring_strength=0.04, damping=0.9)

    for svc, status in sorted(statuses.items()):
        if status in {"UNKNOWN", "STALE"}:
            color = UNKNOWN_COLOR
            previous = snapshot.get(svc, {}).get("status", "unknown")
            title = f"{svc}\nstatus: {status}\nlast recorded: {previous} (not current health)"
            size = 20
        elif status != "HEALTHY":
            color = ROOT_FAULT_COLOR
            title = f"{svc}\nobserved condition: {status}"
            size = 32
        elif svc in blast:
            color = BLAST_COLOR
            title = f"{svc}\nstatus: HEALTHY (potential dependency exposure; impact not confirmed)"
            size = 24
        else:
            color = HEALTHY_COLOR
            title = f"{svc}\nstatus: HEALTHY"
            size = 20

        net.add_node(
            svc,
            label=svc,
            color=color,
            title=title,
            shape="database" if svc in DATASTORE_NODES else "dot",
            size=size,
            borderWidth=2,
        )

    unhealthy = {s for s, st in statuses.items() if st not in {"HEALTHY", "UNKNOWN", "STALE"}}
    for src, dst in edges:
        # Highlight edges that lead into an unhealthy node.
        on_fault_path = dst in unhealthy or src in unhealthy
        net.add_edge(
            src, dst,
            color=EDGE_FAULT_COLOR if on_fault_path else EDGE_COLOR,
            width=3 if on_fault_path else 1,
            arrows="to",
        )

    net.set_options("""
    {
      "interaction": {"hover": true, "tooltipDelay": 80},
      "physics": {"stabilization": {"iterations": 180}},
      "nodes": {"font": {"size": 16, "face": "monospace"}}
    }
    """)

    return net.generate_html(notebook=False)
