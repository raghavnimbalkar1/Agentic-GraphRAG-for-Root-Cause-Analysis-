"""Frozen input integrity and parity controls, not causal accuracy measurements."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from agent.investigation import InvestigationSession
from agent.shadow import DriverReply, project_feedback
from core.diagnostics import METRICS
from core.log_signals import LogObservation, LogSignal, signal_id
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, seed_case
from eval.shadow_replay import (FrozenInputs, freeze_inputs, load_inputs, materialize,
                                run_frozen, run_pair, save_inputs)


@pytest.fixture
def frozen(tmp_path):
    store = seed_case(tmp_path / "source.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    inputs = freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff,
                           topology=fixture_topology(cutoff), source_kind="synthetic")
    return store, inputs


def _freeze(store, inputs):
    return freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=inputs.as_of,
                         topology=inputs.topology, source_kind="synthetic")


def _payload_update(store, identifier, change):
    with sqlite3.connect(store.path) as db:
        raw = json.loads(db.execute("SELECT payload FROM evidence WHERE id=?", (identifier,)).fetchone()[0])
        change(raw)
        db.execute("UPDATE evidence SET payload=? WHERE id=?", (json.dumps(raw), identifier))


def _view(session, request):
    return project_feedback(session.call(request))


def test_round_trip_is_private_exclusive_and_reproducible(frozen, tmp_path):
    _, inputs = frozen
    path = tmp_path / "private" / "inputs.json"
    save_inputs(path, inputs)
    loaded = load_inputs(path)
    assert loaded == inputs
    assert loaded.digest == inputs.digest
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_inputs(path, inputs)
    one, two = run_frozen(loaded, FixedProbePolicy()), run_frozen(loaded, FixedProbePolicy())
    assert one["input_digest"] == two["input_digest"]
    assert one["report"]["initial_context_digest"] == two["report"]["initial_context_digest"]
    assert one["report"]["turns"] == two["report"]["turns"]
    assert one["report"]["result"] == two["report"]["result"]
    assert one["report"]["provider_attempts"] == 0
    assert one["implementation"]["source_sha256"]["eval/shadow_replay.py"]


def test_source_pruning_and_edits_cannot_change_replay(frozen):
    store, inputs = frozen
    first = run_frozen(inputs, FixedProbePolicy())
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM evidence")
    second = run_frozen(inputs, FixedProbePolicy())
    assert first["report"]["result"] == second["report"]["result"]
    assert first["report"]["turns"] == second["report"]["turns"]


def test_every_allowed_tool_window_has_same_projected_feedback(frozen):
    store, inputs = frozen
    for index, (service, key, _) in enumerate(METRICS.values()):
        with sqlite3.connect(store.path) as db:
            identifier = db.execute("SELECT id FROM evidence WHERE service=? AND kind='component'", (service,)).fetchone()[0]
        _payload_update(store, identifier, lambda payload, key=key, value=index + 0.5:
                        payload["measurements"].update({key: value}))
    inputs = _freeze(store, inputs)
    requests = [{"tool": "component_window", "service_id": name, "window_seconds": window}
                for name in REGISTRY.by_name for window in (60, 300, 900)]
    requests += [{"tool": "resource_window", "service_id": spec[0], "metric": metric, "window_seconds": window}
                 for metric, spec in METRICS.items() for window in (60, 300, 900)]
    requests += [{"tool": tool, "service_id": "frontend", "window_seconds": window}
                 for tool in ("transaction_evidence", "error_signals") for window in (60, 300, 900)]
    with materialize(inputs) as path:
        for request in requests:
            original = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=inputs.as_of)
            replayed = InvestigationSession(path, symptom_evidence_id=SYMPTOM_ID, as_of=inputs.as_of)
            assert _view(original, request) == _view(replayed, request)


def test_reader_truncation_is_preserved_not_hidden_by_export(tmp_path):
    store = seed_case(tmp_path / "dense.sqlite", CASES["payment"][0])
    stamp = datetime.now(timezone.utc) - timedelta(seconds=25)
    store.append_observations([ObservationRecord(schema_version=2, environment_id=REGISTRY.environment_id,
        registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest,
        service_id="paymentservice", instance_id="a" * 64, evidence_id=f"OBS-{index + 100:032x}",
        measured_at=stamp + timedelta(milliseconds=index), collected_at=stamp + timedelta(milliseconds=index),
        status="CONNECTION_REFUSED", quality="observed", detail="synthetic") for index in range(121)])
    cutoff = datetime.now(timezone.utc)
    inputs = freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff,
                           topology=fixture_topology(cutoff), source_kind="synthetic")
    with materialize(inputs) as path:
        original = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
        replayed = InvestigationSession(path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
        request = {"tool": "component_window", "service_id": "paymentservice"}
        expected = _view(original, request)
        assert expected["data"]["truncated"] and expected["data"]["prompt_omitted_samples"] == 92
        assert expected == _view(replayed, request)


def test_sensitive_text_is_projected_out_without_changing_diagnostics(frozen):
    store, inputs = frozen
    identifier = "OBS-" + "1".zfill(32)

    def poison(payload):
        payload["detail"] = "SECRET-TEXT"
        payload["raw_artifact_ref"] = "/private/SECRET-TEXT.log"
        payload["measurements"] = {"unreviewed": "SECRET-TEXT", "cpu_percent": 12.5}
        payload["unexpected"] = "SECRET-TEXT"

    _payload_update(store, identifier, poison)
    sanitized = _freeze(store, inputs)
    assert "SECRET-TEXT" not in sanitized.model_dump_json()
    row = next(row for row in sanitized.records if row.payload["evidence_id"] == identifier)
    assert row.payload["measurements"] == {"cpu_percent": 12.5}
    assert row.payload["raw_artifact_ref"] is None


@pytest.mark.parametrize("change", [
    lambda data: data.update(sha256="0" * 64),
    lambda data: data["inputs"].update(expected_target="paymentservice"),
    lambda data: data["inputs"].update(source_kind="live-verified"),
    lambda data: data["inputs"]["records"][0]["payload"].update(detail="unreviewed text"),
    lambda data: data["inputs"]["records"].append(data["inputs"]["records"][0]),
    lambda data: data["inputs"].update(records=list(reversed(data["inputs"]["records"]))),
    lambda data: data["inputs"]["records"][0].update(stored_at=1.0),
    lambda data: data["inputs"].update(symptom_evidence_id="SYMOBS-" + "0" * 32),
])
def test_bundle_tampering_and_invalid_scope_are_rejected(frozen, tmp_path, change):
    _, inputs = frozen
    path = tmp_path / "inputs.json"
    save_inputs(path, inputs)
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_inputs(path)


@pytest.mark.parametrize("content", ['{"sha256":"a","sha256":"b","inputs":{}}',
                                    '{"sha256":NaN,"inputs":{}}', '[1,2]', '{}'])
def test_invalid_envelopes_are_rejected(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_inputs(path)


def test_future_late_and_foreign_rows_are_excluded(frozen):
    store, inputs = frozen
    with sqlite3.connect(store.path) as db:
        # A changed stored time represents a late/backdated arrival, not prior knowledge.
        db.execute("UPDATE evidence SET stored=? WHERE kind='component' AND service='adservice'",
                   (inputs.as_of.timestamp() + 1,))
        db.execute("UPDATE evidence SET measured=? WHERE service='emailservice'",
                   (inputs.as_of.timestamp() + 1,))
        db.execute("UPDATE evidence SET registry=? WHERE service='currencyservice'", ("0" * 64,))
    exported = _freeze(store, inputs)
    assert len(exported.records) == len(inputs.records) - 3
    assert all(row.payload["service_id"] not in {"adservice", "emailservice", "currencyservice"}
               for row in exported.records)


@pytest.mark.parametrize("sql", [
    "UPDATE evidence SET kind='new-adapter' WHERE service='adservice'",
    "UPDATE evidence SET service='adservice' WHERE service='paymentservice'",
    "UPDATE evidence SET measured=measured-1 WHERE service='paymentservice'",
    "UPDATE evidence SET payload=printf('%09000d',0) WHERE service='paymentservice'",
    "PRAGMA user_version=2",
])
def test_malformed_source_fails_instead_of_silent_truncation(frozen, sql):
    store, inputs = frozen
    with sqlite3.connect(store.path) as db:
        db.execute(sql)
    with pytest.raises(ValueError):
        _freeze(store, inputs)


@pytest.mark.parametrize("field,value", [("cpu_percent", True), ("bytes", -1), ("seconds", "1.5")])
def test_malformed_reviewed_measurements_fail_export(frozen, field, value):
    store, inputs = frozen
    _payload_update(store, "OBS-" + "1".zfill(32), lambda payload: payload.update(measurements={field: value}))
    with pytest.raises(ValueError):
        _freeze(store, inputs)


def test_bounds_fail_closed(frozen, tmp_path, monkeypatch):
    import eval.shadow_replay as replay

    store, inputs = frozen
    monkeypatch.setattr(replay, "MAX_ROWS", 2)
    with pytest.raises(ValueError):
        _freeze(store, inputs)
    monkeypatch.setattr(replay, "MAX_BYTES", 200)
    path = tmp_path / "huge.json"
    path.write_bytes(b" " * 201)
    with pytest.raises(ValueError):
        load_inputs(path)
    with pytest.raises(ValueError):
        save_inputs(tmp_path / "too-big.json", inputs)


def test_log_batches_retain_normalized_identity_and_feedback(tmp_path):
    store = seed_case(tmp_path / "logs.sqlite", CASES["payment"][0])
    stamp = datetime.now(timezone.utc) - timedelta(seconds=1)
    instance = "a" * 64
    signal = LogSignal(evidence_id=signal_id(instance, stamp, "order_submission", ["Unavailable"]),
                       measured_at=stamp, operation="order_submission", grpc_codes=["Unavailable"])
    store.append_log_observation(LogObservation(environment_id=REGISTRY.environment_id,
        registry_revision=REGISTRY.revision, registry_digest=REGISTRY.digest, instance_id=instance,
        window_start=stamp - timedelta(seconds=30), measured_at=stamp, collected_at=stamp,
        outcome="sampled", reason="none", lines_read=1, signals=[signal]))
    cutoff = datetime.now(timezone.utc)
    inputs = freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff,
                           topology=fixture_topology(cutoff), source_kind="synthetic")
    request = {"tool": "error_signals", "service_id": "frontend"}
    with materialize(inputs) as path:
        original = InvestigationSession(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
        replayed = InvestigationSession(path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff)
        assert _view(original, request) == _view(replayed, request)


def test_pair_has_equal_inputs_and_no_oracle(frozen):
    _, inputs = frozen
    pair = run_pair(inputs, FixedProbePolicy())
    assert pair["initial_context_equal"]
    assert pair["baseline"]["input_digest"] == pair["comparator"]["input_digest"]
    assert pair["baseline"]["investigation_limits"] == pair["comparator"]["investigation_limits"]
    assert pair["baseline"]["shadow_limits"] == pair["comparator"]["shadow_limits"]
    assert pair["baseline"]["report"]["turns"] == pair["comparator"]["report"]["turns"]
    assert "expected" not in inputs.model_dump_json()
    assert not pair["causal_accuracy_measured"] and not pair["execution_authorized"]


def test_provider_failure_stays_a_recorded_failure_not_an_excluded_trial(frozen):
    class FailingProvider:
        kind = "model"

        def complete(self, *args, **kwargs):
            raise RuntimeError("synthetic provider failure; not real traffic")

    _, inputs = frozen
    pair = run_pair(inputs, FailingProvider())
    assert pair["comparator"]["report"]["termination_reason"] == "provider_unavailable"
    assert pair["comparator"]["report"]["provider_attempts"] == 1
    assert pair["comparator"]["report"]["attempts"][0]["usage_origin"] == "provider-report"
    assert not pair["comparator"]["report"]["usage_complete"]


def test_nested_mutation_is_revalidated_before_replay(frozen):
    _, inputs = frozen
    inputs.records[0].payload["detail"] = "injected text"
    with pytest.raises(ValueError):
        run_frozen(inputs, FixedProbePolicy())


def test_scripted_policy_is_not_mislabelled_as_real_inference(frozen):
    class Abstain:
        kind = "scripted"

        def complete(self, *args, **kwargs):
            return DriverReply('{"action":"abstain","reason":"insufficient_evidence"}',
                               {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

    _, inputs = frozen
    result = run_pair(inputs, Abstain())
    assert result["comparator"]["report"]["driver_kind"] == "scripted"
    assert result["comparator"]["report"]["provider_attempts"] == 0


def test_cli_is_offline_with_clean_environment(tmp_path):
    root = Path(__file__).resolve().parents[1]
    inputs, report = tmp_path / "inputs.json", tmp_path / "report.json"
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(root), "HOME": str(tmp_path)}
    code = """
import sys
from eval.shadow_replay import main
sys.argv = ['replay', 'freeze-synthetic', '--case', 'payment', '--output', sys.argv[1]]
main()
for name in ('core.config', 'docker', 'agent.nodes.reasoner', 'agent.tools.sandbox_tools', 'openai'):
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", code, str(inputs)], cwd=tmp_path, env=env, check=True, capture_output=True)
    process = subprocess.run([sys.executable, "-m", "eval.shadow_replay", "replay", "--inputs", str(inputs),
                               "--output", str(report)], cwd=tmp_path, env=env, check=True, capture_output=True)
    summary = json.loads(process.stdout)
    assert summary["provider_attempts"] == 0 and summary["outcome"] == "proposal"
    assert json.loads(report.read_text())["report"]["result"]["proposal"]["service_id"] == "paymentservice"


def test_stale_missing_and_future_triggers_fail(frozen):
    _, inputs = frozen
    for replacement in (inputs.as_of + timedelta(seconds=100), inputs.as_of.replace(tzinfo=None)):
        data = inputs.model_dump(mode="json")
        data["as_of"] = replacement.isoformat()
        with pytest.raises(ValueError):
            FrozenInputs.model_validate(data)
