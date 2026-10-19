"""The operational landing view must preserve freshness and avoid graph side effects."""

import json
from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord
from dashboard.components.overview import collector_snapshot


def write_status(directory, **changes):
    stamp = datetime.now(timezone.utc)
    row = ObservationRecord(schema_version=2, service_id="frontend", status="HEALTHY", quality="observed",
                            measured_at=stamp, detail="probe", environment_id=REGISTRY.environment_id,
                            registry_digest=REGISTRY.digest, registry_revision=REGISTRY.revision)
    data = {"timestamp": stamp.timestamp(), "state": "ready", "observations": {"frontend": row.model_dump(mode="json")}}
    data.update(changes)
    path = directory / "collector_status.json"
    path.write_text(json.dumps(data))
    return stamp, path


def test_fresh_component_evidence_is_not_invented_for_missing_services(tmp_path):
    stamp, _ = write_status(tmp_path)
    snapshot = collector_snapshot(tmp_path, now=stamp.timestamp() + 1)
    assert snapshot["frontend"]["status"] == "HEALTHY"
    assert snapshot["redis-cart"]["status"] == "UNKNOWN"
    assert len(snapshot) == len(REGISTRY.services)


def test_stopped_or_stale_collector_does_not_supply_current_health(tmp_path):
    for change in ({"timestamp": 0}, {"state": "stopped"}):
        stamp, _ = write_status(tmp_path, **change)
        assert all(row["status"] == "UNKNOWN" for row in collector_snapshot(tmp_path, now=stamp.timestamp()+1).values())


def test_inconsistent_provenance_and_future_sample_are_unknown(tmp_path):
    stamp, path = write_status(tmp_path)
    for key, value in [("environment_id", "other"), ("registry_digest", "f"*64),
                       ("registry_revision", "other-revision"),
                       ("service_id", "redis-cart"), ("measured_at", (stamp+timedelta(hours=1)).isoformat())]:
        data = json.loads(path.read_text())
        original = data["observations"]["frontend"][key]
        data["observations"]["frontend"][key] = value
        path.write_text(json.dumps(data))
        row = collector_snapshot(tmp_path, now=stamp.timestamp()+1)["frontend"]
        assert row["status"] == "UNKNOWN" or row["age_seconds"] is None
        data["observations"]["frontend"][key] = original
        path.write_text(json.dumps(data))


def test_missing_corrupt_and_oversized_heartbeat_render_unknown(tmp_path):
    assert all(row["status"] == "UNKNOWN" for row in collector_snapshot(tmp_path).values())
    path = tmp_path / "collector_status.json"
    for payload in ("[1,2]", "invalid", " " * 262145):
        path.write_text(payload)
        assert all(row["status"] == "UNKNOWN" for row in collector_snapshot(tmp_path).values())


def test_overview_uses_real_unknown_counts_without_connecting_to_graph(tmp_path, monkeypatch):
    from dashboard.components import rca_report
    from graph.graph_client import GraphClient

    def forbid_connect(*args, **kwargs):
        raise AssertionError("Overview must not connect to Neo4j")

    monkeypatch.setattr(GraphClient, "_connect", forbid_connect)
    monkeypatch.setattr(rca_report, "all_reports", lambda: [])
    app = AppTest.from_string(
        "from pathlib import Path\nfrom dashboard.components.overview import render\n"
        f"render(Path({str(tmp_path)!r}))\n").run(timeout=10)
    assert not app.exception
    assert [item.value for item in app.metric] == ["12", "0 / 12", "0", "12"]
    assert not app.button


def test_native_navigation_renders_only_selected_view_without_model_or_graph(tmp_path, monkeypatch):
    from core.config import settings
    from dashboard.components import agent_log, rca_report
    from graph.graph_client import GraphClient

    connected = []
    monkeypatch.setattr(GraphClient, "_connect", lambda *args: connected.append(True))
    monkeypatch.setattr(agent_log, "agent_alive", lambda: False)
    monkeypatch.setattr(rca_report, "all_reports", lambda: [])
    monkeypatch.setattr(settings, "audit_dir", tmp_path)
    app = AppTest.from_file("dashboard/app.py").run(timeout=15)
    assert not app.exception
    assert not connected
    assert app.title[0].value == "Operations overview"
    assert len(app.selectbox) == 0
    assert len(app.tabs) == 0
