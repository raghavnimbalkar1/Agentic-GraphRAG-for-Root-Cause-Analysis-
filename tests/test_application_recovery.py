from unittest.mock import Mock

from agent.nodes import evaluator
from core.health import Observation
from core.schemas import ExecutionResult, ResolutionStatus
from tests.helpers import make_state


def test_failed_checkout_blocks_resolution_even_with_healthy_services(monkeypatch):
    graph = Mock()
    monkeypatch.setattr(evaluator, "GraphClient", lambda: graph)
    monkeypatch.setattr(evaluator, "verify_incident", lambda _: {
        "redis-cart": {"healthy": True, "detail": "ready"},
        "cartservice": {"healthy": True, "detail": "ready"},
        "frontend": {"healthy": True, "detail": "home returns 200"},
        "application": {"healthy": False, "detail": "checkout returns 500"},
    })
    execution = ExecutionResult(skill_name="Redis_Restart_SOP", script_path="/sops/redis/restart.sh",
                                exit_code=0, success=True, attempt=1)
    result = evaluator.evaluate_and_route(make_state(llm_decision="execute", execution_history=[execution]))
    assert result["rca_report"].resolution_status == ResolutionStatus.PARTIAL
    assert not result["all_healthy"]
    assert "checkout returns 500" in result["rca_report"].notes
    graph.get_next_skill.assert_not_called()
    graph.update_service_status.assert_not_called()


def test_application_evidence_is_not_written_as_a_service(monkeypatch):
    graph = Mock()
    monkeypatch.setattr(evaluator, "GraphClient", lambda: graph)
    monkeypatch.setattr(evaluator, "verify_incident", lambda _: {
        name: {"healthy": True, "detail": "ready"}
        for name in ("redis-cart", "cartservice", "frontend", "application")
    })
    execution = ExecutionResult(skill_name="Redis_Restart_SOP", script_path="/sops/redis/restart.sh",
                                exit_code=0, success=True, attempt=1)
    result = evaluator.evaluate_and_route(make_state(llm_decision="execute", execution_history=[execution]))
    assert result["rca_report"].resolution_status == ResolutionStatus.RESOLVED
    assert {call.args[0] for call in graph.update_service_status.call_args_list} == {
        "redis-cart", "cartservice", "frontend",
    }


def setup_probes(monkeypatch, outcomes):
    client = Mock()
    monkeypatch.setattr(evaluator.docker, "DockerClient", lambda **kwargs: client)
    monkeypatch.setattr("core.health.observe_many", lambda *_: {"frontend": Observation("HEALTHY", "ready")})
    canary = Mock(side_effect=outcomes)
    monkeypatch.setattr("simulation.application_canary.check_application", canary)
    monkeypatch.setattr(evaluator.time, "sleep", lambda _: None)
    return client, canary


def test_canary_requires_two_consecutive_successes(monkeypatch):
    client, canary = setup_probes(monkeypatch, [
        {"success": True}, {"success": False}, {"success": True}, {"success": True},
    ])
    result = evaluator.verify_incident(["frontend"])
    assert result["frontend"]["healthy"] and result["application"]["healthy"]
    assert canary.call_count == 4
    client.close.assert_called_once()


def test_canary_error_is_not_recovery(monkeypatch):
    client, _ = setup_probes(monkeypatch, [RuntimeError("unavailable")])
    monkeypatch.setattr(evaluator.time, "monotonic", Mock(side_effect=[0, 30]))
    result = evaluator.verify_incident(["frontend"])
    assert not result["application"]["healthy"]
    assert result["application"]["measurements"]["error"] == "RuntimeError"
    client.close.assert_called_once()
