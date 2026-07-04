"""
LangGraph routing + alert ingestion: the control-flow contract of the loop.
"""

from __future__ import annotations

from agent.graph import route_after_evaluate, route_after_ingest, route_after_reason
from agent.nodes.ingest import ingest_alert
from tests.helpers import make_state


# ── route_after_ingest ─────────────────────────────────────────────────────

def test_ingest_error_routes_to_report():
    assert route_after_ingest(make_state(error_message="bad payload")) == "report"


def test_ingest_ok_routes_to_retrieve():
    assert route_after_ingest(make_state()) == "retrieve"


# ── route_after_reason ─────────────────────────────────────────────────────

def test_execute_routes_to_execute():
    assert route_after_reason(make_state(llm_decision="execute")) == "execute"


def test_skip_escalate_and_none_route_to_evaluate():
    for decision in ("skip", "escalate", None):
        assert route_after_reason(make_state(llm_decision=decision)) == "evaluate"


# ── route_after_evaluate (loop-termination contract) ───────────────────────

def test_all_healthy_terminates():
    assert route_after_evaluate(make_state(all_healthy=True)) == "report"


def test_max_attempts_terminates():
    state = make_state(attempt_count=5, max_attempts=5)
    assert route_after_evaluate(state) == "report"


def test_fallback_pending_goes_straight_to_reason():
    # NEXT_IF_FAIL fallback must NOT re-run Q2 (which would overwrite it)
    state = make_state(fallback_pending=True, attempt_count=1)
    assert route_after_evaluate(state) == "reason"


def test_no_skill_remaining_terminates():
    state = make_state(current_skill=None, attempt_count=1)
    assert route_after_evaluate(state) == "report"


def test_still_unhealthy_with_skills_loops_to_retrieve():
    state = make_state(attempt_count=1)
    assert route_after_evaluate(state) == "retrieve"


# ── ingest_alert ───────────────────────────────────────────────────────────

def test_ingest_initialises_state_from_valid_alert():
    raw = {"service": "frontend", "error_type": "OOM_KILLED", "message": "m"}
    state = ingest_alert({"alert_raw": raw})
    assert state["alert_service"] == "frontend"
    assert state["alert_error_type"] == "OOM_KILLED"
    assert state["attempt_count"] == 0
    assert state["visited_skills"] == []
    assert state["all_healthy"] is False
    assert state["error_message"] is None
    assert state["alert_id"].startswith("INC-")


def test_ingest_rejects_missing_service():
    state = ingest_alert({"alert_raw": {"error_type": "OOM_KILLED", "message": "m"}})
    assert state["error_message"] is not None


def test_ingest_rejects_unknown_error_type():
    raw = {"service": "frontend", "error_type": "NOT_A_REAL_STATUS", "message": "m"}
    state = ingest_alert({"alert_raw": raw})
    assert state["error_message"] is not None
