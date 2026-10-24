"""Offline trace retention/replay controls; no real exporter, provider or repair."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from agent.investigation import InvestigationLimits, InvestigationSession
from agent.shadow import decision_schema
from agent.shadow_context import pack_tabular, unpack_view
from agent.shadow_provider import GroqShadowDriver
from core.diagnostics import DiagnosticRequest
from core.evidence import EvidenceStore
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.trace_evidence import TraceImportError, _nanoseconds, normalize_otlp
from core.trace_store import SNAPSHOT_LIMIT, TraceReader, append_snapshot, decode_snapshot, snapshot_id, validate_snapshot
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_controls import SYMPTOM_ID, fixture_topology, seed_case
from eval.shadow_replay import freeze_inputs, load_inputs, materialize, run_frozen, save_inputs
from eval.shadow_replay import FrozenInputs
from eval.shadow_diagnose import diagnose
from simulation.trace_retain import retain_trace_file
from tests.test_shadow_loop import ScriptedDriver


def trace_snapshot(end=None, *, trace_id="a" * 32, empty=False, pairs=1,
                   caller="checkoutservice", callee="paymentservice", server_end_shift_ns=0):
    end = end or datetime.now(timezone.utc) - timedelta(seconds=5)
    stamp = _nanoseconds(end)
    groups = []
    for service, kind in ((caller, 3), (callee, 2)):
        spans = []
        for index in range(pairs):
            parent, child = f"{2 * index + 1:016x}", f"{2 * index + 2:016x}"
            spans.append({"traceId": trace_id, "spanId": parent if kind == 3 else child,
                "parentSpanId": "" if kind == 3 else parent,
                "startTimeUnixNano": str(stamp - (3 if kind == 3 else 2) * 10**9 - (1000 if kind == 2 else 0)),
                "endTimeUnixNano": str(stamp - (1 if kind == 3 else 2) * 10**9
                                       + (server_end_shift_ns if kind == 2 else 0)),
                "kind": kind, "status": {"code": 2, "message": "PRIVATE-ERROR"},
                "name": "PRIVATE-NAME", "attributes": [{"key": "token", "value": {"stringValue": "PRIVATE-TOKEN"}}]})
        groups.append({"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                       "scopeSpans": [{"spans": spans}]})
    return normalize_otlp(json.dumps({"resourceSpans": [] if empty else groups}).encode(),
                          window_end=end, imported_at=end + timedelta(seconds=1), source_kind="synthetic")


@pytest.fixture
def source(tmp_path):
    store = seed_case(tmp_path / "evidence.sqlite", {"paymentservice": "CONNECTION_REFUSED"})
    snapshot = trace_snapshot()
    store.append_trace_snapshot(snapshot)
    return store, snapshot, datetime.now(timezone.utc)


def window(source, service="paymentservice", **kwargs):
    return TraceReader(source[0].path).window(DiagnosticRequest(service_id=service, **kwargs), now=source[2].timestamp())


def freeze(source, *, include_traces=True):
    return freeze_inputs(source[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=source[2],
                         topology=fixture_topology(source[2]), source_kind="synthetic", include_traces=include_traces)


def test_normalized_snapshot_round_trip_revalidates_and_remains_private(source):
    snapshot = source[1]
    assert validate_snapshot(snapshot) == snapshot
    assert decode_snapshot(json.dumps(snapshot).encode()) == snapshot
    result = window(source)
    assert result["matching_spans"] == 2 and len(result["observed_calls"]) == 1
    assert {row["service_id"] for row in result["spans"]} == {"paymentservice", "checkoutservice"}
    assert not result["complete_topology"] and not result["execution_authorized"]
    assert result["snapshot_id"] == snapshot_id(snapshot)
    assert "PRIVATE" not in json.dumps(result)
    assert datetime.fromisoformat(result["snapshot_available_at"]) <= datetime.fromisoformat(result["snapshot_stored_at"])


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(execution_authorized=True),
    lambda data: data.update(source_authenticated=True),
    lambda data: data.update(complete_topology=True),
    lambda data: data.update(runtime_identity_attested=True),
    lambda data: data.update(declared_topology_changed=True),
    lambda data: data.update(causal_attribution=True),
    lambda data: data.update(schema_version=True),
    lambda data: data.update(source_kind="live-verified"),
    lambda data: data.update(environment_id="other"),
    lambda data: data.update(registry_digest="0" * 64),
    lambda data: data.update(registry_revision="unreviewed"),
    lambda data: data.update(interpretation_version="new"),
    lambda data: data.update(raw_text="PRIVATE"),
    lambda data: data.update(window_end=0),
    lambda data: data.update(window_start=data["window_end"]),
    lambda data: data.update(available_at=data["window_start"]),
    lambda data: data.update(retained_spans=0),
    lambda data: data.update(topology_revision="0" * 64),
    lambda data: data["spans"][0].update(evidence_id="SPAN-" + "0" * 64),
    lambda data: data["spans"][0].update(span_id="f" * 16),
    lambda data: data["spans"][0].update(message="PRIVATE"),
    lambda data: data["spans"].reverse(),
    lambda data: data["spans"].append(data["spans"][0]),
    lambda data: data["observed_calls"][0].update(server_error_pairs=0),
    lambda data: data["observed_calls"][0].update(span_pair_count=True),
    lambda data: data["coverage"].update(input_spans=True),
    lambda data: data["coverage"].update(input_spans=3),
    lambda data: data["coverage"].update(missing_parent_spans=1),
    lambda data: data["coverage"].update(registered_services_without_spans=[]),
])
def test_modified_snapshot_fails_without_echoing_untrusted_data(mutate):
    value = trace_snapshot()
    mutate(value)
    with pytest.raises(TraceImportError, match="^invalid_trace_snapshot$"):
        validate_snapshot(value)


@pytest.mark.parametrize("raw", [b'{}', b'[]', b'\xff', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e10000}',
                               b'{"a":' + b'[' * 40 + b'0' + b']' * 40 + b'}', b' ' * (512 * 1024 + 1)])
def test_bad_snapshot_json_is_bounded_and_fixed_reason(raw):
    with pytest.raises(TraceImportError, match="^invalid_trace_snapshot$"):
        decode_snapshot(raw)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_normalized_snapshot_decoder_requires_utf8(encoding):
    with pytest.raises(TraceImportError, match="^invalid_trace_snapshot$"):
        decode_snapshot(json.dumps(trace_snapshot()).encode(encoding))


def test_empty_and_old_store_are_unknown_not_healthy(tmp_path):
    store = EvidenceStore(tmp_path / "empty.sqlite")
    result = TraceReader(store.path).window(DiagnosticRequest(service_id="paymentservice"),
                                           now=datetime.now(timezone.utc).timestamp())
    assert result["snapshot_id"] is None and result["snapshot_coverage"] is None
    assert result["spans"] == result["observed_calls"] == [] and not result["complete_topology"]
    with sqlite3.connect(store.path) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='trace_snapshots'").fetchone()


def test_duplicate_retention_never_moves_original_availability(source):
    store, snapshot, _ = source
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT * FROM trace_snapshots").fetchall()
    assert store.append_trace_snapshot(snapshot) == snapshot_id(snapshot)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM trace_snapshots").fetchall() == before


def test_future_import_rejected_and_no_partial_trace_table(source):
    snapshot = trace_snapshot(datetime.now(timezone.utc) + timedelta(seconds=10))
    with pytest.raises(TraceImportError, match="trace_not_yet_available"):
        source[0].append_trace_snapshot(snapshot)
    assert window(source)["snapshot_id"] == snapshot_id(source[1])


@pytest.mark.parametrize("column", ["stored", "available", "measured"])
def test_late_or_future_evidence_is_not_available_at_cutoff(source, column):
    with sqlite3.connect(source[0].path) as db:
        db.execute(f"UPDATE trace_snapshots SET {column}=?", (source[2].timestamp() + 1,))
    assert window(source)["snapshot_id"] is None
    assert freeze(source).trace_records == ()


@pytest.mark.parametrize("sql", [
    "UPDATE trace_snapshots SET measured=measured-1",
    "UPDATE trace_snapshots SET available=available-1",
    "UPDATE trace_snapshots SET id='TRACE-bad'",
    "UPDATE trace_snapshots SET payload='{}'",
    "UPDATE trace_snapshots SET payload=CAST(payload AS BLOB)",
    "UPDATE trace_snapshots SET payload=printf('%0600000d',0)",
])
def test_malformed_selected_store_fails_read_and_freeze(source, sql):
    with sqlite3.connect(source[0].path) as db:
        db.execute(sql)
    with pytest.raises(TraceImportError):
        window(source)
    with pytest.raises(TraceImportError):
        freeze(source)
    broker = InvestigationSession(source[0].path, symptom_evidence_id=SYMPTOM_ID,
                                  as_of=source[2], include_traces=True)
    result = broker.call({"tool": "trace_window", "service_id": "paymentservice"})
    assert result["outcome"] == "unavailable" and result["data"] is None


def test_latest_empty_export_does_not_fall_back_to_target_matching_older_export(source):
    newer = trace_snapshot(datetime.fromisoformat(source[1]["window_end"]) + timedelta(seconds=1), empty=True)
    source[0].append_trace_snapshot(newer)
    updated = (source[0], newer, datetime.now(timezone.utc))
    result = window(updated)
    assert result["snapshot_id"] == snapshot_id(newer) and result["spans"] == []
    assert result["eligible_exports"] == 2 and result["omitted_exports"] == 1
    assert result["selection"] == "latest-export-only-no-merge"


def test_service_without_spans_does_not_borrow_another_services_error(source):
    result = window(source, "adservice")
    assert result["snapshot_id"] and not result["spans"] and not result["observed_calls"]
    assert "adservice" in result["snapshot_coverage"]["registered_services_without_spans"]


def test_span_truncation_is_explicit_and_edges_only_reference_returned_spans(source):
    snapshot = trace_snapshot(pairs=40)
    source[0].append_trace_snapshot(snapshot)
    result = window((source[0], snapshot, datetime.now(timezone.utc)))
    assert result["matching_spans"] == 80 and len(result["spans"]) == 32 and result["truncated"]
    ids = {row["evidence_id"] for row in result["spans"]}
    assert all(set(pair) <= ids for edge in result["observed_calls"] for pair in edge["evidence_pairs"])


def test_retention_is_capped_and_trace_table_does_not_pollute_component_evidence(source):
    end = datetime.fromisoformat(source[1]["window_end"])
    for index in range(SNAPSHOT_LIMIT + 4):
        source[0].append_trace_snapshot(trace_snapshot(end, trace_id=f"{index + 1:032x}"))
    with sqlite3.connect(source[0].path) as db:
        assert db.execute("SELECT count(*) FROM trace_snapshots").fetchone()[0] == SNAPSHOT_LIMIT
        assert set(row[0] for row in db.execute("SELECT DISTINCT kind FROM evidence")) == {"component", "symptom"}


def test_expired_duplicate_is_retained_with_a_new_local_stored_time(source):
    with sqlite3.connect(source[0].path) as db:
        db.execute("UPDATE trace_snapshots SET stored=?", (source[2].timestamp() - 86500,))
    source[0].append_trace_snapshot(source[1])
    cutoff = datetime.now(timezone.utc)
    assert window((source[0], source[1], cutoff))["snapshot_id"]


def test_broker_tool_is_opt_in_and_uses_existing_shared_limits(source):
    args = {"symptom_evidence_id": SYMPTOM_ID, "as_of": source[2]}
    request = {"tool": "trace_window", "service_id": "paymentservice"}
    default = InvestigationSession(source[0].path, **args)
    assert "trace_window" not in default.initial_context()["tools"]
    assert default.call(request)["outcome"] == "denied" and default.snapshot()["calls_used"] == 1
    enabled = InvestigationSession(source[0].path, **args, include_traces=True,
                                   limits=InvestigationLimits(max_tool_calls=1))
    assert enabled.call(request)["outcome"] == "ok"
    assert enabled.call(request)["outcome"] == "budget_exhausted"
    assert enabled.snapshot()["issued_evidence_ids"] == [SYMPTOM_ID]


def test_trace_error_cannot_be_used_as_a_readiness_or_causal_citation(source):
    broker = InvestigationSession(source[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=source[2], include_traces=True)
    result = broker.call({"tool": "trace_window", "service_id": "paymentservice"})
    identifier = result["data"]["spans"][-1]["evidence_id"]
    assert broker.propose({"service_id": "paymentservice", "condition": "readiness_failure",
                           "supporting_evidence": [identifier]})["outcome"] == "denied"


def test_trace_responses_consume_existing_byte_budget(source):
    broker = InvestigationSession(source[0].path, symptom_evidence_id=SYMPTOM_ID, as_of=source[2], include_traces=True,
                                  limits=InvestigationLimits(max_response_bytes=512))
    assert broker.call({"tool": "trace_window", "service_id": "paymentservice"})["outcome"] == "output_limit"
    assert broker.snapshot()["calls_used"] == 1


def test_frozen_v2_trace_reader_parity_survives_deletion_of_source(source, tmp_path):
    inputs = freeze(source)
    path = tmp_path / "inputs.json"
    save_inputs(path, inputs)
    assert load_inputs(path) == inputs and inputs.schema_version == 2
    assert len(inputs.trace_records) == 1
    with materialize(inputs) as replay:
        for service in REGISTRY.by_name:
            for duration in (60, 300, 900):
                original = TraceReader(source[0].path).window(DiagnosticRequest(service_id=service, window_seconds=duration),
                                                             now=source[2].timestamp())
                retained = TraceReader(replay).window(DiagnosticRequest(service_id=service, window_seconds=duration),
                                                     now=source[2].timestamp())
                assert original == retained
    with sqlite3.connect(source[0].path) as db:
        db.execute("DELETE FROM trace_snapshots")
    with materialize(inputs) as replay:
        assert TraceReader(replay).window(DiagnosticRequest(service_id="paymentservice"),
                                         now=source[2].timestamp())["matching_spans"] == 2


def test_v1_wire_digest_stays_compatible_and_never_exports_traces_implicitly(source, tmp_path):
    inputs = freeze(source, include_traces=False)
    assert inputs.schema_version == 1 and not inputs.trace_records
    old_wire = inputs.model_dump(mode="json", exclude={"trace_records"})
    old_digest = sha256(json.dumps(old_wire, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert inputs.digest == old_digest
    path = tmp_path / "legacy.json"
    save_inputs(path, inputs)
    assert "trace_records" not in json.loads(path.read_text())["inputs"]
    assert load_inputs(path).digest == old_digest
    result = run_frozen(inputs, FixedProbePolicy())
    assert result["report"]["provider_attempts"] == 0 and not result["execution_authorized"]


def test_v1_exact_byte_ceiling_uses_original_wire_without_new_default_fields(source, monkeypatch):
    legacy = freeze(source, include_traces=False).wire_dump()
    size = len(json.dumps(legacy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    monkeypatch.setattr("eval.shadow_replay.MAX_BYTES", size + 256)
    assert FrozenInputs.model_validate(legacy).schema_version == 1


@pytest.mark.parametrize("version", [True, 2.0, "2"])
def test_trace_bundle_requires_an_explicit_integer_version(source, version):
    data = freeze(source).wire_dump()
    data["schema_version"] = version
    with pytest.raises(ValueError):
        FrozenInputs.model_validate(data)


@pytest.mark.parametrize("mutate", [
    lambda data: data["inputs"].update(schema_version=1),
    lambda data: data["inputs"]["trace_records"][0].update(stored_at=1.0),
    lambda data: data["inputs"]["trace_records"][0]["snapshot"].update(execution_authorized=True),
    lambda data: data["inputs"]["trace_records"].append(data["inputs"]["trace_records"][0]),
])
def test_frozen_trace_tampering_is_rejected(source, tmp_path, mutate):
    path = tmp_path / "inputs.json"
    save_inputs(path, freeze(source))
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_inputs(path)


def test_shadow_can_choose_trace_read_and_no_trace_citation_is_issued(source):
    inputs = freeze(source)
    driver = ScriptedDriver([
        {"action": "read", "requests": [{"tool": "trace_window", "service_id": "paymentservice"}]},
        {"action": "abstain", "reason": "insufficient_evidence"},
    ])
    result = run_frozen(inputs, driver)
    report = result["report"]
    assert report["termination_reason"] == "abstained" and report["session"]["calls_used"] == 1
    assert report["session"]["issued_evidence_ids"] == [SYMPTOM_ID]
    assert report["provider_attempts"] == 0 and not report["execution_authorized"]
    view = json.loads(driver.messages[-1][-1]["content"])
    assert view["turns"][0]["feedback"][0]["data"]["spans"]
    assert "trace_window" in view["initial"]["tools"]
    assert "context only" in driver.messages[-1][0]["content"]
    assert unpack_view(pack_tabular(view)) == view


def test_native_schema_uses_trusted_session_tool_inventory_without_a_provider(source):
    tools = ["component_window", "resource_window", "transaction_evidence", "error_signals", "trace_window"]
    assert "trace_window" not in decision_schema()["$defs"]["ToolRequest"]["properties"]["tool"]["enum"]
    assert "trace_window" in decision_schema(tools=tools)["$defs"]["ToolRequest"]["properties"]["tool"]["enum"]
    view = {"initial": {"tools": tools}, "decision_protocol": {"allowed_actions": ["read", "abstain"]}}
    schema = GroqShadowDriver._schema(None, [{"role": "user", "content": json.dumps(view)}])
    assert "trace_window" in schema["$defs"]["ToolRequest"]["properties"]["tool"]["enum"]


def test_operator_retention_refuses_symlinks_missing_stores_and_raw_exports(source, tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(source[1]))
    assert retain_trace_file(path, source[0].path) == snapshot_id(source[1])
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        retain_trace_file(link, source[0].path)
    store_link = tmp_path / "store-link.sqlite"
    store_link.symlink_to(source[0].path)
    with pytest.raises(TraceImportError):
        retain_trace_file(path, store_link)
    with pytest.raises(FileNotFoundError):
        retain_trace_file(path, tmp_path / "missing.sqlite")
    path.write_text('{"resourceSpans":[]}')
    with pytest.raises(TraceImportError):
        retain_trace_file(path, source[0].path)


def test_retention_cli_imports_no_provider_settings_or_mutation_modules(tmp_path):
    code = """
import sys
import simulation.trace_retain
for name in ('core.config', 'docker', 'agent.nodes.reasoner', 'agent.tools.sandbox_tools', 'openai'):
    assert name not in sys.modules, name
"""
    process = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                             env={**os.environ, "GROQ_API_KEY": ""}, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


def test_backdated_export_keeps_actual_retention_time_out_of_historical_replay(source):
    cutoff = source[2]
    newer = trace_snapshot(datetime.fromisoformat(source[1]["window_end"]) + timedelta(microseconds=1))
    source[0].append_trace_snapshot(newer)
    assert freeze(source).trace_records[0].snapshot_id == snapshot_id(source[1])
    assert len(freeze(source).trace_records) == 1
    assert window((source[0], source[1], cutoff))["snapshot_id"] == snapshot_id(source[1])


@pytest.mark.parametrize("shift,expected", [(-1, 0), (0, 2), (1, 2)])
def test_window_boundaries_preserve_nanoseconds_and_do_not_pull_orphan_peers(tmp_path, shift, expected):
    end = datetime.now(timezone.utc) - timedelta(seconds=100)
    # The SERVER ends exactly at (or one nanosecond around) the read-window start.
    snapshot = trace_snapshot(end, server_end_shift_ns=shift)
    store = EvidenceStore(tmp_path / "boundary.sqlite")
    store.append_trace_snapshot(snapshot)
    result = TraceReader(store.path).window(DiagnosticRequest(service_id="paymentservice", window_seconds=60,
        end_at=end + timedelta(seconds=58)), now=datetime.now(timezone.utc).timestamp())
    assert len(result["spans"]) == expected
    later = TraceReader(store.path).window(DiagnosticRequest(service_id="paymentservice", window_seconds=60,
        end_at=end + timedelta(seconds=58, microseconds=1)), now=datetime.now(timezone.utc).timestamp())
    assert later["spans"] == [] and later["observed_calls"] == []


def test_invalid_or_unbounded_selected_trace_scope_does_not_silently_truncate(source):
    with sqlite3.connect(source[0].path) as db:
        row = db.execute("SELECT * FROM trace_snapshots").fetchone()
        for index in range(SNAPSHOT_LIMIT):
            db.execute("INSERT INTO trace_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (f"TRACE-{index:064x}", *row[1:]))
    with pytest.raises(TraceImportError, match="trace_scope_limit"):
        window(source)
    with pytest.raises(TraceImportError, match="trace_scope_limit"):
        freeze(source)


def test_declared_topology_never_changes_to_match_observed_trace_edges(source):
    source[0].append_trace_snapshot(trace_snapshot(caller="paymentservice", callee="checkoutservice"))
    inputs = freeze((source[0], source[1], datetime.now(timezone.utc)))
    original_edges = inputs.topology.edges
    assert ("paymentservice", "checkoutservice") not in original_edges
    driver = ScriptedDriver([
        {"action": "read", "requests": [{"tool": "trace_window", "service_id": "paymentservice"}]},
        {"action": "abstain", "reason": "insufficient_evidence"},
    ])
    result = run_frozen(inputs, driver)
    view = json.loads(driver.messages[-1][-1]["content"])
    observed = view["turns"][0]["feedback"][0]["data"]["observed_calls"][0]
    assert (observed["caller"], observed["callee"]) == ("paymentservice", "checkoutservice")
    assert tuple(tuple(edge) for edge in view["initial"]["topology"]["edges"]) == original_edges
    assert result["report"]["session"]["root_cause_node"] is None


def test_old_external_approval_cannot_construct_provider_for_trace_bundle(source, tmp_path, monkeypatch):
    inputs = tmp_path / "inputs.json"
    save_inputs(inputs, freeze(source))
    def forbidden(*args, **kwargs):
        pytest.fail("Provider construction must not occur before explicit trace transfer approval")
    monkeypatch.setattr("agent.shadow_provider.configured_groq_driver", forbidden)
    output = tmp_path / "never.json"
    with pytest.raises(ValueError, match="Trace context requires separate"):
        diagnose(inputs, output, provider="groq", approve_external=True)
    assert not output.exists() and not output.with_name("never.json.started.json").exists()


def test_only_fixed_policy_runs_offline_on_trace_inputs_with_no_approval_needed(source, tmp_path):
    inputs = tmp_path / "inputs.json"
    save_inputs(inputs, freeze(source))
    result = diagnose(inputs, tmp_path / "result.json")
    assert result["report"]["provider_attempts"] == 0
    assert result["report"]["candidate_screened"]
    assert result["report"]["result"]["proposal"]["service_id"] == "paymentservice"
    assert not result["execution_authorized"]


def test_retention_cli_rejects_arbitrary_empty_files_as_evidence_stores(source, tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(source[1]))
    empty = tmp_path / "not-a-store.sqlite"
    empty.touch()
    with pytest.raises(ValueError):
        retain_trace_file(snapshot_path, empty)
    assert empty.stat().st_size == 0


def test_retention_cli_success_and_fixed_failure_output(source, tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(source[1]))
    args = [sys.executable, "-m", "simulation.trace_retain", "--input", str(path), "--store", str(source[0].path)]
    good = subprocess.run(args, capture_output=True, text=True)
    assert good.returncode == 0 and json.loads(good.stdout)["outcome"] == "retained-snapshot"
    path.write_text('"PRIVATE-INVALID-INPUT"')
    bad = subprocess.run(args, capture_output=True, text=True)
    assert bad.returncode == 1 and bad.stderr == ""
    assert "PRIVATE" not in bad.stdout and json.loads(bad.stdout)["reason"] == "invalid_trace_snapshot"


@pytest.mark.parametrize("now", [True, float("nan"), float("inf"), -1, "0"])
def test_invalid_reader_clocks_fail_closed(source, now):
    with pytest.raises(ValueError):
        TraceReader(source[0].path).window(DiagnosticRequest(service_id="paymentservice"), now=now)


@pytest.mark.parametrize("stored", [True, float("nan"), float("inf"), -1, "0"])
def test_invalid_retention_clocks_fail_closed(source, stored):
    with sqlite3.connect(source[0].path) as db, pytest.raises(TraceImportError):
        append_snapshot(db, source[1], stored_at=stored)
