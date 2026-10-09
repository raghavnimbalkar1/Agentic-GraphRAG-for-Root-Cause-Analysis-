from unittest.mock import Mock

import httpx
import pytest

from agent import main
from core import settings
from core.audit import read_report, write_json_atomic, report_path
from core.schemas import AlertPayload, ExecutionResult


@pytest.mark.asyncio
async def test_late_workflow_failure_retains_execution_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audit_dir", tmp_path)
    execution = ExecutionResult(skill_name="Restart", script_path="/sops/container/restart.sh", exit_code=0,
                                success=True, attempt=1)

    async def stream(*args, **kwargs):
        yield {"execute": {"execution_history": [execution], "root_cause_node": "frontend"}}
        raise RuntimeError("later verification failed")

    monkeypatch.setattr(main.agent_graph, "astream", stream)
    alert = AlertPayload(service="frontend", error_type="DEGRADED", message="fault")
    report = await main._run_incident(alert)
    assert report.resolution_status == "FAILED"
    assert report.skills_executed == ["Restart"]
    assert read_report(alert.alert_id).execution_history[0].success


@pytest.mark.asyncio
async def test_status_reports_dependency_outage_without_claiming_llm_connectivity(monkeypatch):
    monkeypatch.setattr(main, "GraphClient", Mock(side_effect=RuntimeError("offline")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/status")
    assert response.status_code == 200
    assert response.json()["neo4j"] == "unreachable"
    assert response.json()["llm_connectivity"] == "not_probed"


@pytest.mark.asyncio
async def test_api_rejects_outside_service_before_any_job_is_submitted():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/alert", json={"service": "other-service", "error_type": "DOWN", "message": "fault"})
    assert response.status_code == 422


def test_legacy_report_does_not_become_version_two_when_read(tmp_path, monkeypatch):
    from tests.helpers import make_state
    from agent.nodes.evaluator import _make_report
    from core.schemas import ResolutionStatus
    monkeypatch.setattr(settings, "audit_dir", tmp_path)
    payload = _make_report(make_state(), ResolutionStatus.ESCALATED).model_dump(mode="json")
    payload.pop("schema_version")
    write_json_atomic(report_path(payload["alert_id"]), payload)
    assert read_report(payload["alert_id"]).schema_version == 1


def test_report_filename_cannot_return_another_incident(tmp_path):
    write_json_atomic(report_path("INC-A", tmp_path), {"alert_id": "INC-B"})
    with pytest.raises(ValueError, match="does not match"):
        read_report("INC-A", tmp_path)
