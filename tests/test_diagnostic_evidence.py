"""Retained evidence is bounded context, not a fresh probe or an inferred cause."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

from core.diagnostics import ComponentReader, DiagnosticRequest
from core.evidence import EvidenceStore
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord


def component(stamp, **changes):
    data = dict(schema_version=2, service_id="adservice", instance_id="instance-a",
                environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
                registry_digest=REGISTRY.digest, measured_at=stamp, collected_at=stamp + timedelta(seconds=1),
                status="HEALTHY", detail="private text", quality="observed", measurements={"cpu_percent": 12})
    data.update(changes)
    return ObservationRecord(**data)


@pytest.fixture
def evidence(tmp_path):
    now = datetime.now(timezone.utc)
    store = EvidenceStore(tmp_path / "evidence.sqlite")
    row = component(now - timedelta(seconds=5))
    store.append_observations([row])
    return store, ComponentReader(store.path), now + timedelta(seconds=2), row


def read(reader, now, **changes):
    return reader.window(DiagnosticRequest(service_id="adservice", **changes), now=now.timestamp())


def test_no_creation_or_write_and_no_source_change(evidence, tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        ComponentReader(path).window(DiagnosticRequest(service_id="frontend"))
    assert not path.exists()
    store, reader, now, _ = evidence
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    read(reader, now)
    with closing(reader._connect()) as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.execute("DELETE FROM evidence")
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("change", [
    {"service_id": "not-registered"}, {"service_id": "../../etc/passwd"},
    {"query": "DELETE FROM evidence"}, {"url": "http://example.invalid"},
    {"window_seconds": 86400}, {"metric": "password"},
    {"service_id": "paymentservice", "metric": "cpu_percent"},
    {"end_at": "2026-10-18T10:00:00"},
])
def test_request_rejects_unknown_targets_operations_and_bounds(evidence, change):
    _, reader, now, _ = evidence
    with pytest.raises(ValueError):
        reader.window(DiagnosticRequest(**{"service_id": "adservice", **change}), now=now.timestamp())


def test_deployment_registry_kind_and_service_scope(evidence):
    store, reader, now, row = evidence
    store.append_observations([
        component(row.measured_at, environment_id="other-env"),
        component(row.measured_at, registry_digest="f" * 64),
        component(row.measured_at, service_id="paymentservice"),
    ])
    result = read(reader, now)
    assert [sample["evidence_id"] for sample in result["samples"]] == [row.evidence_id]
    alternate = REGISTRY.model_copy(update={"environment_id": "other-env"})
    assert read(ComponentReader(store.path, registry=alternate), now)["samples"] == []
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET kind='symptom' WHERE id=?", (row.evidence_id,))
    assert read(reader, now)["samples"] == []


@pytest.mark.parametrize("field,value", [
    ("service_id", "frontend"), ("environment_id", "other-env"), ("registry_digest", "f" * 64),
    ("registry_revision", "other-revision"), ("evidence_id", "OBS-another"),
    ("measured_at", "2020-01-01T00:00:00Z"), ("schema_version", 1),
])
def test_storage_payload_provenance_disagreement_fails_closed(evidence, field, value):
    store, reader, now, _ = evidence
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET payload=json_set(payload, ?, ?)", (f"$.{field}", value))
    with pytest.raises(ValueError, match="provenance"):
        read(reader, now)


def test_allowlisted_projection_does_not_echo_arbitrary_stored_fields(evidence):
    store, reader, now, _ = evidence
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET payload=json_set(payload, '$.detail', 'secret-one', "
                   "'$.raw_artifact_ref', '/private/secret-two', '$.measurements.password', 'secret-three')")
    result = read(reader, now, metric="cpu_percent")
    assert "secret-" not in json.dumps(result)
    assert result["metric"]["sample_mean"] == 12
    assert result["summary"]["fresh_now"]


def test_metric_semantics_are_target_specific_and_missing_is_not_zero(evidence):
    store, reader, now, row = evidence
    store.append_observations([
        component(row.measured_at, service_id="recommendationservice", measurements={"bytes": 1234}),
        component(row.measured_at, service_id="emailservice", measurements={"bytes": 5678}),
        component(row.measured_at, service_id="redis-cart", measurements={"maxmemory": 0}),
        component(now - timedelta(seconds=3), status="CONNECTION_REFUSED", measurements={}),
    ])
    for service, metric, expected in [("recommendationservice", "memory_usage_bytes", 1234),
                                      ("emailservice", "writable_layer_bytes", 5678),
                                      ("redis-cart", "redis_maxmemory_bytes", 0)]:
        result = reader.window(DiagnosticRequest(service_id=service, metric=metric), now=now.timestamp())
        assert result["metric"]["sample_mean"] == expected
    result = read(reader, now, metric="cpu_percent")
    assert result["metric"]["missing_samples"] == 1
    assert result["metric"]["sample_mean"] == 12
    assert result["samples"][-1]["value"] is None


def test_unknown_latest_does_not_fall_back_to_earlier_healthy(evidence):
    store, reader, now, _ = evidence
    store.append_observations([component(now - timedelta(seconds=2), status="UNKNOWN", quality="unknown")])
    result = read(reader, now, metric="cpu_percent")
    assert result["summary"]["status_at_window_end"] == "UNKNOWN"
    assert not result["summary"]["fresh_now"]
    assert result["summary"]["unknown_samples"] == 1
    assert result["samples"][-1]["value"] is None


def test_old_window_can_be_healthy_then_but_not_currently(evidence):
    _, reader, now, _ = evidence
    result = read(reader, now + timedelta(hours=2), end_at=now)
    assert result["summary"]["status_at_window_end"] == "HEALTHY"
    assert not result["summary"]["fresh_now"]
    assert result["summary"]["latest_age_seconds"] > 7200
    assert read(reader, now + timedelta(hours=2))["summary"]["status_at_window_end"] == "UNKNOWN"


def test_expired_and_future_stored_samples_are_not_evidence(evidence):
    store, reader, now, _ = evidence
    for stamp in (0, now.timestamp() + 60):
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE evidence SET stored=?", (stamp,))
        result = read(reader, now, metric="cpu_percent")
        assert result["samples"] == []
        assert result["metric"]["sample_mean"] is None
        assert result["summary"]["status_at_window_end"] == "UNKNOWN"


def test_historical_window_excludes_measurements_after_its_end(evidence):
    store, reader, now, first = evidence
    store.append_observations([component(now - timedelta(seconds=2), status="HIGH_CPU", measurements={"cpu_percent": 99})])
    result = read(reader, now, end_at=first.measured_at)
    assert [r["evidence_id"] for r in result["samples"]] == [first.evidence_id]
    assert result["summary"]["status_at_window_end"] == "HEALTHY"


def test_same_time_conflicting_observations_are_unknown_not_arbitrarily_healthy(evidence):
    store, reader, now, first = evidence
    store.append_observations([component(first.measured_at, status="HIGH_CPU")])
    result = read(reader, now)
    assert result["summary"]["latest_conflict"]
    assert result["summary"]["status_at_window_end"] == "UNKNOWN"
    assert not result["summary"]["fresh_now"]


def test_reader_import_does_not_load_settings_secrets_or_mutation_modules():
    subprocess.run([sys.executable, "-c", "import sys; import core.diagnostics; "
                    "assert not any(name in sys.modules for name in "
                    "('core.config', 'core.health', 'docker', 'agent.tools.control_gateway'))"],
                   check=True, capture_output=True)


def test_public_settings_and_logging_imports_remain_compatible():
    import core
    from core import get_logger, get_settings, settings, setup_logging
    from core.config import settings as configured
    from core.logging_config import get_logger as logger

    assert settings is configured is get_settings()
    assert get_logger is logger and callable(setup_logging)
    assert set(core.__all__) <= set(dir(core))
    with pytest.raises(AttributeError):
        core.nonexistent_public_attribute


def test_out_of_order_insertion_gaps_and_instance_changes_are_explicit(evidence):
    store, reader, now, _ = evidence
    store.append_observations([
        component(now - timedelta(seconds=2), instance_id="instance-b"),
        component(now - timedelta(seconds=100), instance_id=None),
    ])
    result = read(reader, now)
    stamps = [r["measured_at"] for r in result["samples"]]
    assert stamps == sorted(stamps)
    assert len(result["unobserved_intervals"]) == 2
    assert result["summary"]["instance_ids"] == ["instance-a", "instance-b"]
    assert result["summary"]["missing_instance_samples"] == 1


def test_latest_100_limit_is_disclosed(evidence):
    store, reader, now, _ = evidence
    store.append_observations([component(now - timedelta(seconds=100 - i / 10)) for i in range(120)])
    result = read(reader, now)
    assert result["truncated"] and len(result["samples"]) == 100
    assert result["summary"]["returned_samples"] == 100


@pytest.mark.parametrize("value", ["secret", -1, True])
def test_malformed_metric_values_are_rejected(evidence, value):
    store, reader, now, _ = evidence
    with sqlite3.connect(store.path) as db:
        raw = json.loads(db.execute("SELECT payload FROM evidence").fetchone()[0])
        raw["measurements"]["cpu_percent"] = value
        db.execute("UPDATE evidence SET payload=?", (json.dumps(raw),))
    with pytest.raises(ValueError, match="numeric"):
        read(reader, now, metric="cpu_percent")


def test_invalid_clock_future_measurement_and_collection_time(evidence):
    store, reader, now, _ = evidence
    with pytest.raises(ValueError, match="future"):
        read(reader, now, end_at=now + timedelta(seconds=1))
    for clock in (True, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="clock"):
            reader.window(DiagnosticRequest(service_id="adservice"), now=clock)
    future = component(now + timedelta(seconds=1))
    store.append_observations([future])
    assert future.evidence_id not in [r["evidence_id"] for r in read(reader, now)["samples"]]
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET payload=json_set(payload, '$.collected_at', '2030-01-01T00:00:00Z')")
    with pytest.raises(ValueError, match="collection time"):
        read(reader, now)


def test_oversized_record_and_unknown_schema_rejected(evidence):
    store, reader, now, _ = evidence
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET payload=?", (" " * 8193,))
    with pytest.raises(ValueError, match="size limit"):
        read(reader, now)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        read(reader, now)


def dashboard(directory, transactions=None):
    return AppTest.from_string(
        "from pathlib import Path\nfrom dashboard.components.component_evidence import render\n"
        f"render(Path({str(directory)!r}), {transactions or []!r})\n").run(timeout=10)


def test_dashboard_missing_store_does_not_create_it(tmp_path):
    app = dashboard(tmp_path)
    assert not app.exception
    assert any("unavailable" in row.value for row in app.warning)
    assert not (tmp_path / "evidence.sqlite").exists()


def test_dashboard_selects_real_metric_without_execution_controls(evidence):
    store, _, _, _ = evidence
    app = dashboard(store.path.parent)
    app.selectbox(key="component_service").set_value("adservice").run()
    app.selectbox(key="component_metric_adservice").set_value("cpu_percent").run()
    assert not app.exception and not app.button
    assert app.dataframe[0].value["Value"].tolist() == [12]
    assert any("Cause not attributed" in row.value for row in app.caption)


def test_dashboard_transaction_anchor_never_shows_later_recovery(evidence):
    store, _, now, first = evidence
    store.append_observations([component(now - timedelta(seconds=100), status="HIGH_CPU", measurements={"cpu_percent": 99})])
    anchor = {"evidence_id": "SYMOBS-test", "measured_at": (now - timedelta(seconds=99)).isoformat(), "outcome": "failed"}
    app = dashboard(store.path.parent, [anchor])
    app.selectbox(key="component_service").set_value("adservice").run()
    app.selectbox(key="component_anchor").set_value(anchor["evidence_id"]).run()
    assert not app.exception
    assert app.metric[0].value == "HIGH_CPU"
    assert first.evidence_id not in app.dataframe[0].value["Evidence ID"].tolist()
    assert any("Current health is unknown" in row.value for row in app.warning)
