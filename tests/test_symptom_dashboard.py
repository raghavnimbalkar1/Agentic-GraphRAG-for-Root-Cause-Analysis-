"""Offline read-only evidence and Streamlit rendering tests."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

from core.audit import write_json_atomic
from core.evidence import EvidenceStore
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import SymptomObservation
from core.symptom_view import SymptomReader, monitor_view, transaction_row


def observation(outcome, offset=-5, **changes):
    stamp = datetime.now(timezone.utc) + timedelta(seconds=offset)
    steps = [{"step": step, "success": True, "status_code": 200, "latency_s": 0.01}
             for step in ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]]
    if outcome == "failed":
        steps[3].update(success=False, status_code=500)
    if outcome == "unknown":
        steps = []
    return SymptomObservation(environment_id=REGISTRY.environment_id, registry_digest=REGISTRY.digest,
                              registry_revision=REGISTRY.revision, started_at=stamp - timedelta(seconds=1),
                              measured_at=stamp, outcome=outcome, steps=steps,
                              quality="unknown" if outcome == "unknown" else "observed", **changes)


def status(directory, record=None, **changes):
    data = dict(schema_version=1, mode="shadow", delivery_enabled=False, interval_seconds=30,
                state="ready", timestamp=time.time(), evidence_store="ready",
                environment_id=REGISTRY.environment_id, registry_digest=REGISTRY.digest,
                last_observation=(record or observation("healthy")).model_dump(mode="json"))
    data.update(changes)
    write_json_atomic(directory / "symptom_monitor_status.json", data)


def episode_fixture(directory):
    store = EvidenceStore(directory / "evidence.sqlite")
    records = [observation("failed", -10), observation("failed", -9)]
    for record in records:
        store.record_symptom(record)
    return store, records


def app(directory):
    return AppTest.from_string(
        "from pathlib import Path\nfrom dashboard.components.symptoms import render\n"
        f"render(Path({str(directory)!r}))\n").run(timeout=10)


def test_reader_never_creates_or_mutates_database(tmp_path):
    path = tmp_path / "evidence.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        SymptomReader(path).snapshot()
    assert not path.exists()
    store, _ = episode_fixture(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    reader = SymptomReader(path)
    data = reader.snapshot()
    assert len(data["observations"]) == 2 and len(data["episodes"]) == 1
    assert len(reader.evidence(data["episodes"][0]["evidence_ids"])) == 2
    with closing(reader._connect()) as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.execute("DELETE FROM evidence")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert len(store.window(REGISTRY.environment_id, REGISTRY.digest)) == 2


def test_scope_applies_to_episode_list_and_evidence_ids(tmp_path):
    store, records = episode_fixture(tmp_path)
    for reader in (SymptomReader(store.path, environment="other-env"), SymptomReader(store.path, registry="f" * 64)):
        data = reader.snapshot()
        assert data["observations"] == data["episodes"] == []
        assert reader.evidence([record.evidence_id for record in records]) == []


def test_retention_loss_keeps_episode_but_not_stale_evidence(tmp_path):
    store, records = episode_fixture(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET stored=0")
    reader = SymptomReader(store.path)
    data = reader.snapshot()
    assert len(data["episodes"]) == 1 and data["observations"] == []
    assert reader.evidence([record.evidence_id for record in records]) == []


def test_payload_scope_cannot_impersonate_different_deployment(tmp_path):
    store, _ = episode_fixture(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET payload=json_set(payload, '$.environment_id', 'other-env')")
    with pytest.raises(ValueError, match="provenance"):
        SymptomReader(store.path).snapshot()


def test_schema_and_query_bounds(tmp_path):
    store, _ = episode_fixture(tmp_path)
    reader = SymptomReader(store.path)
    with pytest.raises(ValueError):
        reader.snapshot(seconds=1000000)
    with pytest.raises(ValueError):
        reader.evidence(["x"] * 33)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        reader.snapshot()


def test_observation_view_reports_truncation(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    for index in range(201):
        store.record_symptom(observation("healthy", -20 + index / 1000))
    data = SymptomReader(store.path).snapshot()
    assert len(data["observations"]) == 200 and data["truncated"]
    assert data["episodes"] == []


@pytest.mark.parametrize("change,expected", [
    ({"state": "stopped"}, "stopped"), ({"timestamp": 0}, "stale"),
    ({"timestamp": time.time() + 3600}, "stale"), ({"timestamp": None}, "stale"),
    ({"delivery_enabled": True}, "invalid"), ({"interval_seconds": float("nan")}, "invalid"),
    ({"registry_digest": "f" * 64}, "different_deployment"),
])
def test_monitor_states_are_not_current_healthy_claims(tmp_path, change, expected):
    status(tmp_path, **change)
    assert monitor_view(tmp_path)["state"] == expected


def test_fresh_heartbeat_does_not_refresh_old_evidence(tmp_path):
    status(tmp_path, record=observation("healthy", -600))
    view = monitor_view(tmp_path)
    assert view["state"] == "degraded" and not view["observation_fresh"]


def test_legacy_monitor_provenance_from_observation_still_loads(tmp_path):
    status(tmp_path)
    path = tmp_path / "symptom_monitor_status.json"
    data = json.loads(path.read_text())
    del data["environment_id"], data["registry_digest"]
    write_json_atomic(path, data)
    assert monitor_view(tmp_path)["state"] == "ready"


def test_invalid_or_missing_status_and_absent_transaction_duration(tmp_path):
    assert monitor_view(tmp_path)["state"] == "missing"
    path = tmp_path / "symptom_monitor_status.json"
    path.write_text("[1, 2]")
    assert monitor_view(tmp_path)["state"] == "invalid"
    path.write_bytes(b" " * 65537)
    assert monitor_view(tmp_path)["state"] == "invalid"
    assert transaction_row(observation("unknown").model_dump(mode="json"))["Duration (ms)"] is None


def test_dashboard_empty_and_missing_store_are_not_healthy(tmp_path):
    rendered = app(tmp_path)
    assert not rendered.exception
    assert "Unavailable" in [metric.value for metric in rendered.metric]
    assert any("Evidence store unavailable" in message.value for message in rendered.warning)
    assert not (tmp_path / "evidence.sqlite").exists()


def test_dashboard_unknown_sample_and_window_filter(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    record = observation("unknown")
    store.record_symptom(record)
    status(tmp_path, record=record, state="degraded")
    rendered = app(tmp_path)
    assert not rendered.exception
    assert rendered.metric[1].value == "Unknown"
    assert rendered.dataframe[0].value["Duration (ms)"].isna().all()
    rendered.selectbox[0].set_value("24 hours").run(timeout=10)
    assert not rendered.exception


def test_chart_and_table_use_utc_not_browser_or_source_timezone():
    from dashboard.components.symptoms import latency_chart

    record = observation("healthy").model_dump(mode="json")
    record["measured_at"] = "2026-10-18T20:08:00+05:30"
    row = transaction_row(record)
    assert row["Observed (UTC)"] == "2026-10-18T14:38:00+00:00"
    spec = latency_chart([record]).to_dict()
    assert spec["encoding"]["x"]["scale"]["type"] == "utc"
    assert spec["encoding"]["color"]["scale"]["domain"] == ["Healthy", "Failed", "Unknown"]
    assert spec["encoding"]["tooltip"][0]["type"] == "nominal"


def test_dashboard_shows_steps_and_unattributed_episode_without_actions(tmp_path):
    _, records = episode_fixture(tmp_path)
    status(tmp_path, record=records[-1])
    rendered = app(tmp_path)
    assert not rendered.exception
    assert len(rendered.button) == 0
    assert any("not attributed" in message.value for message in rendered.markdown)
    assert rendered.dataframe[0].value["Failed step"].tolist() == ["checkout", "checkout"]
    assert "checkout" in rendered.dataframe[-1].value["Step"].tolist()


def test_dashboard_recovery_is_observed_not_agent_resolution(tmp_path):
    store, _ = episode_fixture(tmp_path)
    store.record_symptom(observation("healthy", -7))
    last = observation("healthy", -6)
    store.record_symptom(last)
    status(tmp_path, record=last, state="stopped")
    rendered = app(tmp_path)
    assert not rendered.exception
    assert any("Observed recovery" in row.value for row in rendered.markdown)
    assert any("Not an autonomous-repair result" in row.value for row in rendered.caption)
    assert rendered.metric[0].value == "Stopped"


def test_dashboard_survives_corrupt_store_and_expired_references(tmp_path):
    store, _ = episode_fixture(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM evidence")
    rendered = app(tmp_path)
    assert not rendered.exception
    assert any("expired or unavailable" in row.value for row in rendered.warning)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE symptom_episodes SET payload='invalid json'")
    rendered = app(tmp_path)
    assert not rendered.exception
    assert any("Evidence store unavailable" in row.value for row in rendered.warning)
