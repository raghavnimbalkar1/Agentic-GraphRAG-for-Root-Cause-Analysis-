"""Synthetic capture controls; no live probes, external model, fault or repair."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
from unittest.mock import Mock
from uuid import uuid4

import pytest

from agent.shadow import DependencySnapshot
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation
from eval.shadow_capture import (CaptureRejected, capture_latest_inputs, capture_observations,
                                 declared_topology)
from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, seed_case
from eval.shadow_replay import load_inputs
from graph.graph_client import GraphClient
from core.exceptions import GraphError


@pytest.fixture
def source(tmp_path):
    audit = tmp_path / "audit"
    store = seed_case(audit / "evidence.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    export = {"services": list(REGISTRY.by_name), "edges": list(fixture_topology(cutoff).edges)}
    topology = declared_topology(export, captured_at=cutoff)
    return audit, store, cutoff, export, topology


def status():
    return {"schema_version": 2, "heartbeat_fresh": True, "state": "observe_only",
            "delivery_enabled": False, "evidence_store": {"enabled": True, "state": "ready"}}


def collect(source, output, **kwargs):
    audit, _, _, export, _ = source
    return capture_observations(audit, output, topology_loader=lambda: export,
                                status_reader=status, **kwargs)


def add_transaction(store, *, outcome="healthy", stamp=None, identifier="a" * 32):
    with sqlite3.connect(store.path) as db:
        data = json.loads(db.execute("SELECT payload FROM evidence WHERE id=?", (SYMPTOM_ID,)).fetchone()[0])
    stamp = stamp or datetime.now(timezone.utc) - timedelta(milliseconds=10)
    data.update(evidence_id="SYMOBS-" + identifier, measured_at=stamp.isoformat(),
                collected_at=stamp.isoformat(), started_at=(stamp - timedelta(seconds=1)).isoformat(),
                outcome=outcome, quality="unknown" if outcome == "unknown" else "observed")
    if outcome == "healthy":
        for step in data["steps"]:
            step.update(success=True, status_code=200, error=None)
    if outcome == "unknown":
        data["steps"] = []
    record = SymptomObservation.model_validate(data)
    store.record_symptom(record)
    return record


def test_live_graph_export_has_bounded_one_query_and_no_status_or_skill_fields():
    client = object.__new__(GraphClient)
    client._run = Mock(return_value=[{"services": ["frontend"], "edges": []}])
    assert client.get_dependency_topology() == {"services": ["frontend"], "edges": []}
    client._run.assert_called_once()
    query = client._run.call_args.args[0]
    assert "LIMIT 1001" in query and "LIMIT 129" in query
    assert all(name not in query for name in ("status", "error_code", "Skill", "APPLIES_TO", "SET "))


def test_graph_export_requires_one_aggregate_row():
    client = object.__new__(GraphClient)
    client._run = Mock(return_value=[])
    with pytest.raises(GraphError, match="one snapshot"):
        client.get_dependency_topology()


def test_topology_revision_binds_content_not_order_or_capture_time(source):
    _, _, cutoff, export, original = source
    reordered = {"services": list(reversed(export["services"])), "edges": list(reversed(export["edges"]))}
    changed_time = declared_topology(reordered, captured_at=cutoff + timedelta(seconds=1))
    assert changed_time.revision == original.revision and changed_time.edges == original.edges
    changed = deepcopy(export)
    changed["edges"].pop()
    assert declared_topology(changed, captured_at=cutoff).revision != original.revision


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(status="HEALTHY"),
    lambda d: d["services"].pop(),
    lambda d: d["services"].append("foreign-app"),
    lambda d: d["services"].__setitem__(0, d["services"][1]),
    lambda d: d["services"].__setitem__(0, None),
    lambda d: d.update(edges=[]),
    lambda d: d["edges"].append(d["edges"][0]),
    lambda d: d["edges"].append(("frontend", "foreign-app")),
    lambda d: d["edges"].append(("frontend",)),
    lambda d: d.update(edges=[["frontend", "paymentservice"]] * 129),
])
def test_unknown_duplicate_incomplete_or_oversized_graph_is_rejected(source, mutate):
    _, _, cutoff, export, _ = source
    mutate(export)
    with pytest.raises(CaptureRejected):
        declared_topology(export, captured_at=cutoff)


def test_fresh_failed_transaction_freezes_sanitized_retained_inputs_read_only(source):
    audit, store, cutoff, _, topology = source
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT * FROM evidence ORDER BY id").fetchall()
    inputs = capture_latest_inputs(store.path, topology=topology, as_of=cutoff)
    assert inputs.source_kind == "retained-observation" and inputs.symptom_evidence_id == SYMPTOM_ID
    assert inputs.schema_version == 1 and not inputs.trace_records
    assert inputs.topology.source == "neo4j-export"
    assert "Synthetic offline component observation" not in inputs.model_dump_json()
    with sqlite3.connect(audit / "evidence.sqlite") as db:
        assert before == db.execute("SELECT * FROM evidence ORDER BY id").fetchall()


@pytest.mark.parametrize("outcome", ["healthy", "unknown"])
def test_newer_healthy_or_unknown_never_falls_back_to_an_older_failure(source, outcome):
    _, store, _, _, topology = source
    add_transaction(store, outcome=outcome)
    with pytest.raises(CaptureRejected, match="latest_transaction_not_failed"):
        capture_latest_inputs(store.path, topology=topology, as_of=datetime.now(timezone.utc))


def test_latest_failure_is_selected_without_an_operator_root_or_trigger(source):
    _, store, _, _, topology = source
    latest = add_transaction(store, outcome="failed")
    inputs = capture_latest_inputs(store.path, topology=topology, as_of=datetime.now(timezone.utc))
    assert inputs.symptom_evidence_id == latest.evidence_id
    assert not {"root", "expected_target", "injected_fault"} & inputs.wire_dump().keys()


def test_latest_time_tie_is_rejected_even_when_both_transactions_fail(source):
    _, store, _, _, topology = source
    with sqlite3.connect(store.path) as db:
        row = list(db.execute("SELECT * FROM evidence WHERE id=?", (SYMPTOM_ID,)).fetchone())
        payload = json.loads(row[-1])
        payload["evidence_id"] = row[0] = "SYMOBS-" + "b" * 32
        row[-1] = json.dumps(payload)
        # The monitor rejects duplicate times; inject a malformed retained-store peer directly.
        db.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
    with pytest.raises(CaptureRejected, match="ambiguous_latest_transaction"):
        capture_latest_inputs(store.path, topology=topology, as_of=datetime.now(timezone.utc))


@pytest.mark.parametrize("field,value", [("source", "declared-fixture"), ("seconds", -31), ("seconds", 1)])
def test_replay_topology_stale_or_future_graph_is_not_a_live_capture(source, field, value):
    _, store, cutoff, _, topology = source
    data = topology.model_dump()
    if field == "seconds":
        data["captured_at"] = cutoff + timedelta(seconds=value)
    else:
        data[field] = value
    with pytest.raises(CaptureRejected, match="fresh_graph_export_required"):
        capture_latest_inputs(store.path, topology=DependencySnapshot.model_validate(data), as_of=cutoff)


@pytest.mark.parametrize("value", [datetime(2020, 1, 1), datetime.now(timezone.utc) + timedelta(days=1)])
def test_invalid_clock_is_rejected_before_store_access(source, value):
    _, _, _, _, topology = source
    with pytest.raises(CaptureRejected, match="invalid_capture_clock"):
        capture_latest_inputs(source[0] / "missing.sqlite", topology=topology, as_of=value)


def test_no_transaction_cannot_create_a_bundle(source):
    _, store, _, _, topology = source
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM evidence WHERE kind='symptom'")
    with pytest.raises(CaptureRejected, match="no_recent_transaction"):
        capture_latest_inputs(store.path, topology=topology, as_of=datetime.now(timezone.utc))


def test_stale_failure_is_not_a_current_trigger(source):
    _, store, cutoff, _, topology = source
    stamp = cutoff - timedelta(seconds=91)
    with sqlite3.connect(store.path) as db:
        payload = json.loads(db.execute("SELECT payload FROM evidence WHERE id=?", (SYMPTOM_ID,)).fetchone()[0])
        payload.update(measured_at=stamp.isoformat(), collected_at=stamp.isoformat(),
                       started_at=(stamp - timedelta(seconds=1)).isoformat())
        db.execute("UPDATE evidence SET payload=?, measured=? WHERE id=?",
                   (json.dumps(payload), stamp.timestamp(), SYMPTOM_ID))
    with pytest.raises(CaptureRejected, match="no_recent_transaction"):
        capture_latest_inputs(store.path, topology=topology, as_of=cutoff)


def test_captured_inputs_enter_existing_diagnosis_and_recheck_without_provider_or_executor(source, tmp_path):
    from eval.baselines.investigation import FixedProbePolicy
    from eval.shadow_replan import ReplanningInvestigation

    output = tmp_path / "diagnose"
    result = collect(source, output)
    inputs = load_inputs(output / "inputs.json")
    run = ReplanningInvestigation(inputs, profile="two-epoch-v1", evidence_layout="time-indexed-v1",
                                 investigation_protocol="bounded-coverage-v1")
    event = run.run_epoch(inputs, FixedProbePolicy())
    assert event["outcome"] == "investigated" and event["report"]["candidate_screened"]
    candidate = run.snapshot()["candidates"][-1]
    assert candidate["proposal"]["service_id"] == "paymentservice"
    assert event["report"]["provider_attempts"] == 0
    assert result["input_digest"] == event["input_digest"]
    assert not candidate["execution_authorized"]
    # Reusing the same capture cannot refresh a proposal or grant authority.
    receipt = run.recheck_candidate(inputs, candidate_revision=candidate["revision"])
    assert receipt["outcome"] == "rejected" and receipt["reason"] == "recheck_cutoff_behind_clock"
    assert run.snapshot()["current_consistency_check"] is None


@pytest.mark.parametrize("change,expected", [
    (None, "same_candidate_new_observations"),
    ("instance", "runtime_instance_changed"),
    ("condition", "scope_state_changed"),
])
def test_new_capture_rechecks_new_measurements_and_binds_instance_and_condition(source, tmp_path, change, expected):
    from eval.baselines.investigation import FixedProbePolicy
    from eval.shadow_replan import ReplanningInvestigation

    collect(source, tmp_path / "original")
    origin = load_inputs(tmp_path / "original/inputs.json")
    run = ReplanningInvestigation(origin, profile="two-epoch-v1", evidence_layout="time-indexed-v1",
                                 investigation_protocol="bounded-coverage-v1", clock=lambda: 0)
    run.run_epoch(origin, FixedProbePolicy())
    candidate = run.snapshot()["candidates"][-1]
    stamp = datetime.now(timezone.utc)
    observations = []
    for row in origin.records:
        if row.kind != "component":
            continue
        payload = {**row.payload, "evidence_id": "OBS-" + uuid4().hex,
                   "measured_at": stamp, "collected_at": stamp}
        if payload["service_id"] == "paymentservice":
            if change == "instance":
                payload["instance_id"] = "f" * 64
            if change == "condition":
                payload["status"] = "HEALTHY"
        observations.append(ObservationRecord.model_validate(payload))
    source[1].append_observations(observations)
    add_transaction(source[1], outcome="failed", stamp=datetime.now(timezone.utc))
    collect(source, tmp_path / "refresh")
    fresh = load_inputs(tmp_path / "refresh/inputs.json")
    receipt = run.recheck_candidate(fresh, candidate_revision=candidate["revision"])
    assert receipt["reason"] == expected
    assert receipt["outcome"] == ("consistent" if change is None else "rejected")
    assert not receipt["execution_authorized"] and run.snapshot()["current_candidate"] is None
    if change is None:
        assert run.snapshot()["current_consistency_check"]
        old_ids = {row.payload["evidence_id"] for row in origin.records}
        assert old_ids.isdisjoint(receipt["report"]["evidence_ids"])
    else:
        assert run.snapshot()["current_consistency_check"] is None


def test_frozen_selection_is_reconciled_if_store_changes_during_capture(source, monkeypatch):
    import eval.shadow_capture as capture

    _, store, cutoff, _, topology = source
    original = capture.TransactionReader.window
    def changed(reader, *args, **kwargs):
        result = original(reader, *args, **kwargs)
        with sqlite3.connect(store.path) as db:
            db.execute("DELETE FROM evidence WHERE kind='symptom'")
        return result
    monkeypatch.setattr(capture.TransactionReader, "window", changed)
    with pytest.raises(ValueError):
        capture_latest_inputs(store.path, topology=topology, as_of=cutoff)


def test_capture_is_private_exclusive_has_no_authority_and_leaks_no_source_path(source, tmp_path):
    output = tmp_path / "capture"
    result = collect(source, output)
    assert result["outcome"] == "captured" and result["real_model_calls"] == 0
    assert result["new_application_probes"] == 0 and result["execution_authorized"] is False
    assert not result["source_authenticated"] and not result["live_freshness_verified"]
    inputs = load_inputs(output / "inputs.json")
    assert inputs.digest == result["input_digest"]
    assert os.stat(output).st_mode & 0o777 == 0o700
    assert all(os.stat(path).st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert str(source[0]) not in (output / "result.json").read_text()
    marker = json.loads((output / "started.json").read_text())
    assert "eval/shadow_capture.py" in marker["capture_source_sha256"]
    with pytest.raises(FileExistsError):
        collect(source, output)


@pytest.mark.parametrize("change", [
    {"delivery_enabled": True}, {"delivery_enabled": None}, {"schema_version": 1},
    {"schema_version": 2.0}, {"schema_version": True}, {"state": {}},
    {"heartbeat_fresh": False}, {"heartbeat_fresh": 1}, {"state": "stopped"}, {"state": "stale"},
    {"evidence_store": {"enabled": False, "state": "ready"}},
    {"evidence_store": {"enabled": True, "state": "unavailable"}},
])
def test_bad_collector_state_blocks_before_graph_or_database(source, tmp_path, change):
    loader = Mock(side_effect=AssertionError("Graph should not be contacted"))
    result = capture_observations(source[0], tmp_path / "blocked", topology_loader=loader,
                                  status_reader=lambda: {**status(), **change})
    assert result["outcome"] == "not_eligible" and result["input_digest"] is None
    assert not (tmp_path / "blocked/inputs.json").exists()
    loader.assert_not_called()


def test_unknown_component_health_is_not_reclassified_as_a_collector_outage(source, tmp_path):
    result = capture_observations(source[0], tmp_path / "degraded", topology_loader=lambda: source[3],
                                  status_reader=lambda: {**status(), "state": "degraded"})
    assert result["outcome"] == "captured"


def test_delivery_enabled_during_capture_discards_inputs(source, tmp_path):
    statuses = iter([status(), {**status(), "delivery_enabled": True}])
    output = tmp_path / "changed"
    result = capture_observations(source[0], output, topology_loader=lambda: source[3],
                                  status_reader=lambda: next(statuses))
    assert result["reason"] == "automatic_delivery_not_disabled"
    assert not (output / "inputs.json").exists()


def test_slow_capture_discards_inputs_instead_of_publishing_an_old_cutoff(source, tmp_path):
    cutoff = datetime.now(timezone.utc)
    times = iter([cutoff, cutoff, cutoff, cutoff + timedelta(seconds=10)])
    output = tmp_path / "slow"
    result = collect(source, output, utc_now=lambda: next(times))
    assert result["reason"] == "capture_time_exhausted" and not (output / "inputs.json").exists()


def test_source_error_is_recorded_without_exception_text_and_cannot_retry_in_place(source, tmp_path):
    output = tmp_path / "failure"
    result = capture_observations(source[0], output, status_reader=status,
        topology_loader=Mock(side_effect=OSError("SECRET-URL-AND-PASSWORD")))
    assert result["outcome"] == "failed" and result["reason"] == "invalid_or_unavailable_source"
    assert "SECRET" not in (output / "result.json").read_text()
    with pytest.raises(FileExistsError):
        collect(source, output)


def test_interruption_remains_recorded_and_consumes_the_output_directory(source, tmp_path):
    output = tmp_path / "interrupted"
    with pytest.raises(KeyboardInterrupt):
        capture_observations(source[0], output, status_reader=status,
                             topology_loader=Mock(side_effect=KeyboardInterrupt()))
    assert json.loads((output / "result.json").read_text())["outcome"] == "interrupted"
    assert not (output / "inputs.json").exists()
    with pytest.raises(FileExistsError):
        collect(source, output)
