"""
eval/trainticket/topology.py

The FudanSELab TrainTicket dependency graph, transcribed from the published
service-architecture diagram (github.com/FudanSELab/train-ticket), loaded into
Neo4j under a SEPARATE label — :TTService — so it is completely isolated from the
live Online Boutique demo.

Why isolation matters: the collector, dashboard graph viewers, and health queries
all match `:Service`. Loading TrainTicket as `:TTService` means it is invisible to
all of them — the working closed-loop demo is untouched. This is a LOCALISATION
study only: we prove that the exact same Q1 traversal (via
GraphClient.get_root_cause, parameterised with node_label="TTService") localises
the root cause on a ~36-service topology with cascades up to 7 dependency hops —
far deeper than Online Boutique's 3-4. Closed-loop remediation on TrainTicket
(real probes + SOPs on its Spring Cloud stack) is named future work.

`A DEPENDS_ON B`  ==  service A calls / needs service B at runtime.
"""

from __future__ import annotations

# 36 services (~3x Online Boutique).
TT_SERVICES = [
    "frontend", "gateway", "auth", "verification-code", "preserve", "ticket-office",
    "travel2", "order", "payment", "inside-payment", "notification", "delivery",
    "rebook", "cancel", "execute", "wait-order", "travel-plan", "route-plan",
    "user", "food", "train-food", "station-food", "food-delivery", "consign",
    "consign-price", "contacts", "assurance", "security", "seat", "config",
    "train", "route", "basic", "price", "station", "voucher",
]

# Directed dependency edges (src depends on dst). Kept acyclic; the booking
# ("preserve") flow is the deep chain that reaches `station` at 7 hops from
# `frontend`: frontend→gateway→preserve→seat→travel2→basic→route→station.
TT_EDGES = [
    ("frontend", "gateway"),

    # Gateway routes to top-level business services only (never straight to infra)
    ("gateway", "auth"), ("gateway", "preserve"), ("gateway", "ticket-office"),
    ("gateway", "travel2"), ("gateway", "order"), ("gateway", "payment"),
    ("gateway", "inside-payment"), ("gateway", "notification"), ("gateway", "delivery"),
    ("gateway", "rebook"), ("gateway", "cancel"), ("gateway", "execute"),
    ("gateway", "wait-order"), ("gateway", "travel-plan"), ("gateway", "route-plan"),
    ("gateway", "user"), ("gateway", "food"), ("gateway", "consign"),
    ("gateway", "contacts"), ("gateway", "assurance"), ("gateway", "security"),
    ("gateway", "voucher"),

    ("auth", "verification-code"),

    # preserve = booking; the deep business flow
    ("preserve", "seat"), ("preserve", "user"), ("preserve", "assurance"),
    ("preserve", "contacts"), ("preserve", "security"), ("preserve", "food"),
    ("preserve", "consign"), ("preserve", "station"), ("preserve", "order"),

    ("seat", "travel2"), ("seat", "config"), ("seat", "order"),
    ("travel2", "train"), ("travel2", "route"), ("travel2", "basic"), ("travel2", "price"),
    ("basic", "station"), ("basic", "train"), ("basic", "route"), ("basic", "price"),
    ("route", "station"),
    ("order", "station"),

    ("food", "station-food"), ("food", "train-food"),
    ("station-food", "food-delivery"),
    ("consign", "consign-price"),

    ("payment", "order"),
    ("inside-payment", "payment"), ("inside-payment", "order"),
    ("delivery", "order"), ("delivery", "notification"),
    ("rebook", "order"), ("rebook", "seat"), ("rebook", "travel2"), ("rebook", "payment"),
    ("cancel", "order"), ("cancel", "notification"), ("cancel", "voucher"),
    ("execute", "order"),
    ("wait-order", "order"),
    ("security", "order"),

    ("travel-plan", "route-plan"), ("travel-plan", "travel2"), ("travel-plan", "route"),
    ("travel-plan", "train"), ("travel-plan", "station"),
    ("route-plan", "route"), ("route-plan", "station"), ("route-plan", "price"),
]

# Localisation scenarios spanning depth 1→7. Each: the alerting (surface) service,
# the true root, its Q1 traversal depth, and an ambiguous surface symptom that
# names neither the root nor the path (as a far-upstream monitor really sees it).
TT_SCENARIOS = [
    {"id": "TT-d1", "depth": 1, "alert_service": "frontend", "root": "gateway",
     "message": "frontend: API calls to the gateway timing out"},
    {"id": "TT-d2", "depth": 2, "alert_service": "frontend", "root": "preserve",
     "message": "frontend: booking page returning errors on submit"},
    {"id": "TT-d3", "depth": 3, "alert_service": "frontend", "root": "seat",
     "message": "frontend: seat selection intermittently failing at checkout"},
    {"id": "TT-d4", "depth": 4, "alert_service": "frontend", "root": "travel2",
     "message": "frontend: trip search and booking flows degraded"},
    {"id": "TT-d5", "depth": 5, "alert_service": "frontend", "root": "basic",
     "message": "frontend: elevated 5xx across booking, prices look stale"},
    {"id": "TT-d6", "depth": 6, "alert_service": "frontend", "root": "route",
     "message": "frontend: storefront error rate climbing, checkout conversions dropping"},
    {"id": "TT-d7", "depth": 7, "alert_service": "frontend", "root": "station",
     "message": "frontend: site-wide 5xx spike, success rate 98%->60% over 5 min"},
]

TT_LABEL = "TTService"
_HEALTHY = "HEALTHY"


def load_topology(gc) -> dict:
    """MERGE the TrainTicket graph into Neo4j as :TTService nodes + :DEPENDS_ON
    edges. Idempotent. Returns {services, edges} counts. Never touches :Service."""
    # Nodes
    gc._run(
        f"UNWIND $names AS n "
        f"MERGE (s:{TT_LABEL} {{name: n}}) "
        f"ON CREATE SET s.status = $healthy "
        f"ON MATCH SET s.status = coalesce(s.status, $healthy)",
        names=TT_SERVICES, healthy=_HEALTHY,
    )
    # Edges
    gc._run(
        f"UNWIND $edges AS e "
        f"MATCH (a:{TT_LABEL} {{name: e[0]}}), (b:{TT_LABEL} {{name: e[1]}}) "
        f"MERGE (a)-[:DEPENDS_ON]->(b)",
        edges=[list(e) for e in TT_EDGES],
    )
    counts = gc._run(
        f"MATCH (s:{TT_LABEL}) WITH count(s) AS n "
        f"MATCH (:{TT_LABEL})-[r:DEPENDS_ON]->(:{TT_LABEL}) "
        f"RETURN n AS services, count(r) AS edges"
    )
    return counts[0] if counts else {"services": 0, "edges": 0}


def reset_topology(gc) -> None:
    """Set every TrainTicket node back to HEALTHY (cleanup after a scenario)."""
    gc._run(f"MATCH (s:{TT_LABEL}) SET s.status = $h", h=_HEALTHY)
