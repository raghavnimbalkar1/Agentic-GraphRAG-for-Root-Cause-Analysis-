from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import agent.graph as workflow
from agent.nodes import evaluator
from agent.nodes import retriever
from agent.nodes.executor import _resolve_host_path
from core.audit import report_path
from core.schemas import AlertPayload, ExecutionResult, ResolutionStatus
from graph.graph_client import GraphClient
from graph.scripts import init_graph
from tests.helpers import make_state


def test_stale_snapshot_prevents_remediation_lookup(monkeypatch):
    from core.health import SERVICES
    client = Mock()
    client.get_service_snapshot.return_value = {name: {"status": "HEALTHY", "age_seconds": 1000} for name in SERVICES}
    monkeypatch.setattr(retriever, "GraphClient", lambda: client)
    result = retriever.retrieve_context(make_state(root_cause_node=None))
    assert "Fresh health" in result["error_message"]
    client.get_root_cause.assert_not_called()
    client.get_skills.assert_not_called()


def test_execution_reprobes_original_fault_and_refuses_changed_root(monkeypatch):
    from agent.nodes import executor
    from core.health import Observation
    client = Mock()
    monkeypatch.setattr(executor.docker, "DockerClient", lambda **kwargs: client)
    monkeypatch.setattr("core.health.observe_many", lambda *args: {"redis-cart": Observation("HEALTHY", "ready")})
    execute = Mock(side_effect=AssertionError("Changed fault must not execute"))
    monkeypatch.setattr(executor, "execute_sop", execute)
    result = executor.run_sop(make_state(root_condition="OOM_KILLED"))
    assert "preflight failed" in result["error_message"]
    execute.assert_not_called()
    client.close.assert_called_once()


def test_ambiguous_roots_are_escalated_without_skills(monkeypatch):
    from core.health import SERVICES
    from core.schemas import DependencyChainResult
    from agent.nodes.reasoner import llm_decide
    client = Mock()
    client.get_service_snapshot.return_value = {name: {"status": "HEALTHY", "age_seconds": 1} for name in SERVICES}
    client.get_root_cause.return_value = DependencyChainResult(root_cause_node="redis-cart", depth=3,
        dependency_chain=["redis-cart", "cartservice", "checkoutservice", "frontend"],
        candidate_roots=["redis-cart", "paymentservice"])
    monkeypatch.setattr(retriever, "GraphClient", lambda: client)
    state = retriever.retrieve_context(make_state(root_cause_node=None))
    result = evaluator.evaluate_and_route(llm_decide(state))
    assert result["rca_report"].resolution_status == ResolutionStatus.ESCALATED
    assert len(result["rca_report"].candidate_roots) == 2
    client.get_skills.assert_not_called()


def test_explicit_escalation_ends_compiled_workflow(monkeypatch):
    execution = Mock(side_effect=AssertionError("Escalation must never execute"))
    monkeypatch.setattr(workflow, "ingest_alert", lambda s: make_state())
    monkeypatch.setattr(workflow, "retrieve_context", lambda s: s)
    monkeypatch.setattr(workflow, "llm_decide", lambda s: {**s, "llm_decision": "escalate"})
    monkeypatch.setattr(workflow, "run_sop", execution)
    monkeypatch.setattr(workflow, "generate_report", lambda s: s)
    monkeypatch.setattr(evaluator, "GraphClient", Mock(side_effect=AssertionError("No probe on escalation")))
    result = workflow.build_graph().compile().invoke({"alert_raw": {}})
    assert result["rca_report"].resolution_status == ResolutionStatus.ESCALATED
    assert result["attempt_count"] == 1
    assert result["rca_report"].skills_executed == []
    execution.assert_not_called()


def test_skip_does_not_reverify_previous_execution(monkeypatch):
    previous = ExecutionResult(skill_name="Redis_Restart_SOP", script_path="/sops/redis/restart.sh",
                               exit_code=0, success=True, attempt=1)
    probe = Mock(side_effect=AssertionError("Old result reused"))
    monkeypatch.setattr(evaluator, "GraphClient", Mock())
    monkeypatch.setattr(evaluator, "verify_real_health", probe)
    result = evaluator.evaluate_and_route(make_state(llm_decision="skip", attempt_count=1,
                                                    execution_history=[previous]))
    assert result["all_healthy"] is False
    probe.assert_not_called()


def test_nonzero_execution_uses_same_target_fallback(monkeypatch):
    from core.schemas import SkillNode
    gc = Mock()
    gc.get_next_skill.return_value = SkillNode(name="Redis_Flush_SOP", script_path="/sops/redis/cache_flush.sh",
                                              script_type="bash", description="flush", trigger_condition="STALE_DATA")
    monkeypatch.setattr(evaluator, "GraphClient", lambda: gc)
    previous = ExecutionResult(skill_name="Redis_Restart_SOP", script_path="/sops/redis/restart.sh",
                               exit_code=1, success=False, attempt=1)
    result = evaluator.evaluate_and_route(make_state(llm_decision="execute", execution_history=[previous],
                                                    root_condition="OOM_KILLED"))
    gc.get_next_skill.assert_called_once_with("Redis_Restart_SOP", "redis-cart")
    assert result["fallback_pending"]
    assert result["root_condition"] == "OOM_KILLED"
    assert result["current_trigger"] == "STALE_DATA"


def test_recovered_root_with_unhealthy_dependents_does_not_get_another_root_fix(monkeypatch):
    client = Mock()
    monkeypatch.setattr(evaluator, "GraphClient", lambda: client)
    monkeypatch.setattr(evaluator, "verify_incident", lambda services: {
        "redis-cart": {"healthy": True, "detail": "ready"},
        "frontend": {"healthy": False, "detail": "HTTP 500"},
    })
    execution = ExecutionResult(skill_name="Redis_Restart_SOP", script_path="/sops/redis/restart.sh",
                                exit_code=0, success=True, attempt=1)
    result = evaluator.evaluate_and_route(make_state(llm_decision="execute", execution_history=[execution]))
    assert result["rca_report"].resolution_status == ResolutionStatus.PARTIAL
    client.get_next_skill.assert_not_called()


@pytest.mark.parametrize("path", ["/sops/../core/config.py", "/sops/../../outside", "/etc/passwd"])
def test_script_cannot_escape_sops(path):
    with pytest.raises(ValueError):
        _resolve_host_path(path)


def test_script_symlink_cannot_escape_sops(tmp_path, monkeypatch):
    import agent.nodes.executor as executor
    root = tmp_path / "sops"
    root.mkdir()
    target = tmp_path / "outside.sh"
    target.touch()
    (root / "escape.sh").symlink_to(target)
    monkeypatch.setattr(executor, "SOPS_ROOT", root)
    with pytest.raises(ValueError):
        executor._resolve_host_path("/sops/escape.sh")


@pytest.mark.parametrize("symptom", ["HTTP_503", "HIGH_ERROR_RATE", "OOM_KILLED"])
def test_upstream_symptoms_are_accepted(symptom):
    assert AlertPayload(service="frontend", error_type=symptom, message="probe").error_type == symptom


@pytest.mark.parametrize("symptom", ["HEALTHY", "UNKNOWN", "NOT_A_REAL_STATUS"])
def test_non_incidents_are_rejected(symptom):
    with pytest.raises(ValidationError):
        AlertPayload(service="frontend", error_type=symptom, message="probe")


def test_audit_identifier_cannot_escape_directory():
    with pytest.raises(ValueError):
        report_path("x/../../outside")


def test_topology_smoke_check_is_read_only():
    gc = Mock()
    gc._run.return_value = [{"depth": 3}]
    assert init_graph.smoke_test(gc)
    gc.update_service_status.assert_not_called()
    assert "SET " not in gc._run.call_args.args[0]


def test_seed_has_expected_catalog_and_preserves_existing_health():
    import re
    source = init_graph.TOPOLOGY_CYPHER.read_text()
    services = re.findall(r"MERGE \(\w+:Service \{name: '([^']+)'\}\)", source)
    skills = re.findall(r"MERGE \(\w+:Skill \{name: '([^']+)'\}\)", source)
    assert len(set(services)) == init_graph.EXPECTED_NODES["Service"] == 12
    assert len(set(skills)) == init_graph.EXPECTED_NODES["Skill"] == 15
    assert source.count("ON CREATE SET") == 12
    assert ".error_code    = null" not in source
    assert source.count("MERGE (a)-[:NEXT_IF_FAIL]") == init_graph.EXPECTED_RELS["NEXT_IF_FAIL"] == 1


def test_failed_initial_connection_can_be_retried(monkeypatch):
    import graph.graph_client as module
    from neo4j.exceptions import ServiceUnavailable
    from core.exceptions import GraphError
    monkeypatch.setattr(GraphClient, "_instance", None)
    monkeypatch.setattr(GraphClient, "_driver", None)
    failed, healthy = Mock(), Mock()
    failed.verify_connectivity.side_effect = ServiceUnavailable("test outage")
    monkeypatch.setattr(module.GraphDatabase, "driver", Mock(side_effect=[failed, healthy]))
    with pytest.raises(GraphError):
        GraphClient()
    failed.close.assert_called_once()
    client = GraphClient()
    assert client._driver is healthy
    client.close()
    assert client._driver is None
