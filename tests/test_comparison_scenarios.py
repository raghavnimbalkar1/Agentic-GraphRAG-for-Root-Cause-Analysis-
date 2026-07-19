"""
The Live Duel scenarios must be well-formed and span the depth range that makes
the demo meaningful (shallow = everyone wins, deep = only GraphRAG). Pure data
checks — no Neo4j / LLM needed.
"""

from __future__ import annotations

from core.schemas import ServiceStatus
from dashboard.components.comparison import SCENARIOS

REQUIRED = {"id", "depth", "fault", "alert_service", "root",
            "condition", "error_type", "message"}


def test_every_scenario_is_well_formed():
    for sc in SCENARIOS:
        assert REQUIRED <= set(sc), f"{sc.get('id')} missing keys"
        assert sc["alert_service"] != sc["root"] or sc["depth"] == 0, \
            f"{sc['id']}: a cascade must alert somewhere other than the root"


def test_conditions_are_real_service_statuses():
    valid = {s.value for s in ServiceStatus}
    for sc in SCENARIOS:
        assert sc["condition"] in valid, f"{sc['id']}: bogus condition {sc['condition']}"


def test_scenarios_span_shallow_to_deep():
    depths = {sc["depth"] for sc in SCENARIOS}
    assert 1 in depths, "need a depth-1 case (the 'everyone wins' control)"
    assert max(depths) >= 4, "need a deep case where baselines break"


def test_scenario_ids_unique():
    ids = [sc["id"] for sc in SCENARIOS]
    assert len(ids) == len(set(ids))
