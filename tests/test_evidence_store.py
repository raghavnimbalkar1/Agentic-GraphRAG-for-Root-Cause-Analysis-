"""Persistent evidence stays scoped, bounded and separate from attributed causes."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from core.evidence import EvidenceStore
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation


def component(**changes):
    data = dict(schema_version=2, service_id="adservice", instance_id="instance-123",
                environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
                registry_digest=REGISTRY.digest, measured_at=datetime.now(timezone.utc),
                status="HEALTHY", detail="probe", quality="observed")
    data.update(changes)
    return ObservationRecord(**data)


def symptom(outcome, stamp, **changes):
    names = ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]
    steps = [{"step": name, "success": True, "status_code": 200, "latency_s": 0.01} for name in names]
    if outcome == "failed":
        steps[3].update(success=False, status_code=500)
    if outcome == "unknown":
        steps = []
    data = dict(environment_id=REGISTRY.environment_id, registry_revision=REGISTRY.revision,
                registry_digest=REGISTRY.digest, started_at=stamp - timedelta(seconds=1),
                measured_at=stamp, outcome=outcome, quality="unknown" if outcome == "unknown" else "observed",
                steps=steps)
    data.update(changes)
    return SymptomObservation(**data)


def window(store, **kwargs):
    return store.window(REGISTRY.environment_id, REGISTRY.digest, **kwargs)


def episodes(store):
    return store.episodes(REGISTRY.environment_id, REGISTRY.digest)


def test_legacy_reading_and_new_provenance_contract(tmp_path):
    legacy = component(schema_version=1, environment_id=None, registry_revision=None, registry_digest=None)
    assert ObservationRecord.model_validate_json(legacy.model_dump_json()).schema_version == 1
    with pytest.raises(ValidationError, match="provenance"):
        component(environment_id=None)
    with pytest.raises(ValidationError, match="quality"):
        component(status="UNKNOWN")
    with pytest.raises(ValueError, match="version 2"):
        EvidenceStore(tmp_path / "evidence.db").append_observations([legacy])


def test_component_storage_sanitizes_payload_and_deduplicates(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    row = component(detail="secret exception", raw_artifact_ref="/private/secret",
                    measurements={"cpu_percent": 82.3, "password": "secret", "bytes": float("nan"),
                                  "clients": True, "seconds": -1})
    store.append_observations([row, row])
    saved = window(store)
    assert len(saved) == 1
    assert saved[0]["measurements"] == {"cpu_percent": 82.3}
    assert saved[0]["raw_artifact_ref"] is None
    assert "secret" not in json.dumps(saved)
    with pytest.raises(ValueError, match="different data"):
        store.append_observations([component(evidence_id=row.evidence_id, status="HIGH_CPU")])
    assert len(window(store)) == 1


def test_window_is_scoped_and_future_records_are_not_current_evidence(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    now = datetime.now(timezone.utc)
    rows = [component(measured_at=now), component(environment_id="another-env"),
            component(registry_digest="f" * 64), component(service_id="paymentservice"),
            component(measured_at=now + timedelta(minutes=5))]
    store.append_observations(rows)
    assert {r["evidence_id"] for r in window(store, service="adservice")} == {rows[0].evidence_id}
    assert len(window(store)) == 2
    assert window(store, since=now.timestamp() + 1) == []
    with pytest.raises(ValueError):
        window(store, limit=1001)


def test_retention_and_row_cap(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db", max_rows=10, retention_seconds=60)
    store.append_observations([component() for _ in range(15)])
    assert len(window(store)) == 10
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE evidence SET stored=0")
    assert window(store) == []
    store.append_observations([component()])
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_oversized_batch_rolls_back_and_unknown_database_schema_is_rejected(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    with pytest.raises(ValueError, match="payload limit"):
        store.append_observations([component(), component(instance_id="x" * 9000)])
    assert window(store) == []
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        EvidenceStore(store.path)


def test_two_store_writers_share_atomic_database(tmp_path):
    path = tmp_path / "evidence.db"
    stores = [EvidenceStore(path), EvidenceStore(path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.append_observations, [component() for _ in range(8)]) for store in stores]
        for future in futures:
            future.result()
    assert len(window(stores[0])) == 16


def test_episode_survives_restart_unknown_and_recovers_after_two_successes(tmp_path):
    path = tmp_path / "evidence.db"
    store = EvidenceStore(path)
    base = datetime.now(timezone.utc) - timedelta(seconds=20)
    def send(outcome, n):
        return store.record_symptom(symptom(outcome, base + timedelta(seconds=n)))

    assert send("failed", 0)["active_id"] is None
    opened = send("failed", 1)["active_id"]
    store = EvidenceStore(path)
    assert send("unknown", 2)["active_id"] == opened
    assert send("healthy", 3)["active_id"] == opened
    assert send("unknown", 4)["active_id"] == opened
    assert send("healthy", 5)["active_id"] == opened
    assert send("healthy", 6)["active_id"] is None
    row = episodes(store)[0]
    assert row["state"] == "recovered" and row["mode"] == "shadow"
    assert row["root_cause_node"] is None and row["candidate_roots"] == []
    send("failed", 7)
    assert send("failed", 8)["active_id"] != opened
    assert len(episodes(store)) == 2


def test_duplicate_out_of_order_and_gap_do_not_double_count_failures(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    base = datetime.now(timezone.utc) - timedelta(seconds=50)
    first = symptom("failed", base)
    store.record_symptom(first)
    assert store.record_symptom(first)["ignored_out_of_order"]
    assert store.record_symptom(symptom("failed", base - timedelta(seconds=1)))["ignored_out_of_order"]
    assert len(window(store)) == 1
    after_gap = store.record_symptom(symptom("failed", base + timedelta(seconds=40)), max_gap=30)
    assert after_gap["active_id"] is None and after_gap["failures"] == 1
    assert episodes(store) == []


@pytest.mark.parametrize("seconds", [-61, 60])
def test_stale_or_future_symptoms_do_not_start_incidents(tmp_path, seconds):
    store = EvidenceStore(tmp_path / "evidence.db")
    stamp = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    with pytest.raises(ValueError, match="stale or future"):
        store.record_symptom(symptom("failed", stamp))
    assert window(store) == [] and episodes(store) == []


def test_component_anomaly_is_not_automatically_a_checkout_cause(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    store.append_observations([component(status="HIGH_CPU")])
    base = datetime.now(timezone.utc) - timedelta(seconds=3)
    store.record_symptom(symptom("failed", base))
    store.record_symptom(symptom("failed", base + timedelta(seconds=1)))
    row = episodes(store)[0]
    assert row["symptom_service"] == "frontend" and row["root_cause_node"] is None
    assert row["candidate_roots"] == []
    with pytest.raises(ValidationError):
        symptom("failed", base, root_cause_node="paymentservice")


def test_episode_evidence_and_debounce_counts_are_bounded(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    base = datetime.now(timezone.utc) - timedelta(seconds=50)
    for n in range(40):
        state = store.record_symptom(symptom("failed", base + timedelta(seconds=n)))
    assert state["failures"] == 2
    assert len(episodes(store)[0]["evidence_ids"]) == 32
