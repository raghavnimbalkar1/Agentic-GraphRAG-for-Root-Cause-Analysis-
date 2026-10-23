"""Synthetic trace controls, not an instrumented application or a causal benchmark."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.trace_evidence import (
    INPUT_LIMIT,
    JSON_NODE_LIMIT,
    SPAN_LIMIT,
    TraceImportError,
    normalize_otlp,
)
from simulation.trace_import import import_trace_file

END = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
END_NS = 1767268860 * 10**9
TRACE = "a" * 32
CLIENT = "1" * 16
SERVER = "2" * 16


def span(identifier, *, kind=3, parent="", trace=TRACE, **changes):
    result = {
        "traceId": trace,
        "spanId": identifier,
        "parentSpanId": parent,
        "startTimeUnixNano": str(END_NS - 6 * 10**9),
        "endTimeUnixNano": str(END_NS - 3 * 10**9),
        "kind": kind,
        "status": {"code": 2, "message": "private-error"},
        "name": "private-operation",
        "attributes": [{"key": "http.url", "value": {"stringValue": "private-url"}}],
        "events": [{"name": "private-event"}],
        "traceState": "private-state",
    }
    result.update(changes)
    return result


def resource(service, spans, *, attributes=()):
    return {
        "resource": {
            "attributes": [
                {"key": "service.name", "value": {"stringValue": service}},
                {"key": "authorization", "value": {"stringValue": "private-token"}},
                *attributes,
            ]
        },
        "scopeSpans": [{"scope": {"name": "private-library"}, "spans": spans}],
    }


def exported():
    return {
        "resourceSpans": [
            resource("checkoutservice", [span(CLIENT)]),
            resource(
                "paymentservice",
                [
                    span(
                        SERVER,
                        kind=2,
                        parent=CLIENT,
                        startTimeUnixNano=str(END_NS - 5 * 10**9),
                        endTimeUnixNano=str(END_NS - 4 * 10**9),
                    )
                ],
            ),
        ]
    }


def normalize(data, **options):
    return normalize_otlp(
        json.dumps(data).encode(),
        window_end=END,
        source_kind="synthetic",
        imported_at=END + timedelta(seconds=1),
        **options,
    )


def test_direct_client_server_context_produces_evidence_backed_observed_call():
    report = normalize(exported())
    assert report["retained_spans"] == 2
    (edge,) = report["observed_calls"]
    assert (edge["caller"], edge["callee"]) == ("checkoutservice", "paymentservice")
    assert edge["span_pair_count"] == edge["server_error_pairs"] == 1
    assert edge["first_seen_ns"] == END_NS - 5 * 10**9
    assert edge["last_seen_ns"] == END_NS - 4 * 10**9
    assert edge["evidence_pairs"] == [[row["evidence_id"] for row in report["spans"]]]
    assert report["coverage"]["missing_parent_spans"] == 0
    assert "private-" not in json.dumps(report)
    for field in (
        "causal_attribution",
        "complete_topology",
        "execution_authorized",
        "declared_topology_changed",
        "source_authenticated",
        "runtime_identity_attested",
    ):
        assert report[field] is False


def test_missing_parent_is_not_guessed_from_peer_attribute_or_timing():
    data = exported()
    child = data["resourceSpans"][1]["scopeSpans"][0]["spans"][0]
    child["parentSpanId"] = "3" * 16
    child["attributes"] = [{"key": "peer.service", "value": {"stringValue": "checkoutservice"}}]
    result = normalize(data)
    assert not result["observed_calls"] and result["coverage"]["missing_parent_spans"] == 1


def test_same_span_id_in_different_trace_cannot_join():
    data = exported()
    data["resourceSpans"][1]["scopeSpans"][0]["spans"][0]["traceId"] = "b" * 32
    result = normalize(data)
    assert not result["observed_calls"] and result["coverage"]["missing_parent_spans"] == 1


@pytest.mark.parametrize("parent_kind,child_kind", [(2, 2), (1, 2), (4, 5), (0, 0), (3, 3)])
def test_unsupported_cross_service_links_are_disclosed_not_inferred(parent_kind, child_kind):
    data = exported()
    for group, kind in zip(data["resourceSpans"], (parent_kind, child_kind), strict=True):
        group["scopeSpans"][0]["spans"][0]["kind"] = kind
    result = normalize(data)
    assert (
        not result["observed_calls"] and result["coverage"]["unsupported_cross_service_links"] == 1
    )


def test_same_service_parent_does_not_create_a_network_edge():
    data = exported()
    data["resourceSpans"][1]["resource"]["attributes"][0]["value"][
        "stringValue"
    ] = "checkoutservice"
    result = normalize(data)
    assert not result["observed_calls"] and result["retained_spans"] == 2


def test_unknown_service_is_counted_without_retaining_its_name_or_granting_registration():
    data = exported()
    data["resourceSpans"][0]["resource"]["attributes"][0]["value"][
        "stringValue"
    ] = "private-renamed-service"
    result = normalize(data)
    assert (
        result["coverage"]["unregistered_spans"]
        == result["coverage"]["unregistered_resource_groups"]
        == 1
    )
    assert result["coverage"]["missing_parent_spans"] == 1 and not result["observed_calls"]
    assert "private-" not in json.dumps(result)


def test_no_service_identity_remains_explicitly_unregistered():
    data = exported()
    data["resourceSpans"][0]["resource"] = {}
    assert normalize(data)["coverage"]["unregistered_resource_groups"] == 1


def test_identical_duplicate_is_deduplicated_but_conflicting_id_is_rejected():
    data = exported()
    rows = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
    rows.append(deepcopy(rows[0]))
    result = normalize(data)
    assert result["retained_spans"] == 2 and result["coverage"]["duplicate_spans"] == 1
    rows[1]["status"]["code"] = 0
    with pytest.raises(TraceImportError, match="conflicting_span_identity"):
        normalize(data)


def test_raw_discarded_fields_do_not_change_normalized_identity_or_hash():
    data = exported()
    before = normalize(data)
    data["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
        "name"
    ] = "entirely-different-private-name"
    data["extraPrivateField"] = "private-unknown-field"
    assert normalize(data) == before


def test_uppercase_hex_and_integer_timestamps_are_canonicalized():
    data = exported()
    before = normalize(data)
    for group in data["resourceSpans"]:
        row = group["scopeSpans"][0]["spans"][0]
        row["traceId"] = row["traceId"].upper()
        row["startTimeUnixNano"] = int(row["startTimeUnixNano"])
        row["endTimeUnixNano"] = int(row["endTimeUnixNano"])
    assert normalize(data) == before


def test_nanosecond_identity_is_not_rounded_to_datetime_microseconds():
    data = exported()
    before = normalize(data)
    child = data["resourceSpans"][1]["scopeSpans"][0]["spans"][0]
    child["endTimeUnixNano"] = str(int(child["endTimeUnixNano"]) + 1)
    after = normalize(data)
    assert after["spans"][1]["end_ns"] == before["spans"][1]["end_ns"] + 1
    assert after["spans"][1]["evidence_id"] != before["spans"][1]["evidence_id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("traceId", "0" * 32),
        ("spanId", "0" * 16),
        ("traceId", "a" * 31),
        ("spanId", "not-hex"),
        ("parentSpanId", "0" * 16),
        ("parentSpanId", CLIENT),
        ("parentSpanId", None),
        ("kind", "SPAN_KIND_CLIENT"),
        ("kind", True),
        ("kind", 6),
        ("startTimeUnixNano", True),
        ("startTimeUnixNano", 1.5),
        ("startTimeUnixNano", "1e18"),
        ("startTimeUnixNano", str(2**64)),
        ("startTimeUnixNano", "0"),
        ("startTimeUnixNano", str(END_NS + 1)),
        ("status", {"code": True}),
        ("status", {"code": 3}),
        ("status", None),
    ],
)
def test_invalid_spans_fail_closed_with_fixed_errors(field, value):
    data = exported()
    data["resourceSpans"][0]["scopeSpans"][0]["spans"][0][field] = value
    with pytest.raises(TraceImportError) as failure:
        normalize(data)
    assert "private" not in str(failure.value)


def test_parent_cycles_fail_but_observed_service_graph_cycles_are_allowed():
    data = exported()
    data["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["parentSpanId"] = SERVER
    with pytest.raises(TraceImportError, match="cyclic_parent_context"):
        normalize(data)
    data = exported()
    data["resourceSpans"].extend(
        [
            resource("paymentservice", [span("3" * 16, trace="b" * 32)]),
            resource("checkoutservice", [span("4" * 16, trace="b" * 32, kind=2, parent="3" * 16)]),
        ]
    )
    assert len(normalize(data)["observed_calls"]) == 2


def test_clock_skew_is_disclosed_without_cross_host_latency_or_causality_claim():
    data = exported()
    data["resourceSpans"][1]["scopeSpans"][0]["spans"][0]["startTimeUnixNano"] = str(
        END_NS - 7 * 10**9
    )
    result = normalize(data)
    assert len(result["observed_calls"]) == 1 and result["coverage"]["clock_order_warnings"] == 1
    assert "latency" not in json.dumps(result) and not result["causal_attribution"]


def test_span_window_and_availability_are_separate():
    data = exported()
    data["resourceSpans"][0]["scopeSpans"][0]["spans"][0].update(
        startTimeUnixNano=str(END_NS - 62 * 10**9), endTimeUnixNano=str(END_NS - 61 * 10**9)
    )
    result = normalize(data)
    assert result["retained_spans"] == 1 and result["coverage"]["outside_window_spans"] == 1
    assert result["coverage"]["missing_parent_spans"] == 1
    assert datetime.fromisoformat(result["available_at"]) > datetime.fromisoformat(
        result["window_end"]
    )


def test_canonical_instance_is_an_unattested_claim_and_arbitrary_instance_text_is_removed():
    data = exported()
    attrs = data["resourceSpans"][0]["resource"]["attributes"]
    attrs.append({"key": "container.id", "value": {"stringValue": "C" * 64}})
    assert normalize(data)["spans"][0]["instance_id"] == "c" * 64
    attrs[-1]["value"]["stringValue"] = "private-short-instance"
    result = normalize(data)
    assert result["spans"][0]["instance_id"] is None
    assert result["coverage"]["invalid_instance_resource_groups"] == 1
    assert "private-" not in json.dumps(result)


def test_duplicate_resource_identity_is_not_resolved_by_last_value():
    data = exported()
    data["resourceSpans"][0]["resource"]["attributes"].append(
        {"key": "service.name", "value": {"stringValue": "paymentservice"}}
    )
    with pytest.raises(TraceImportError, match="invalid_resource_identity"):
        normalize(data)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"resourceSpans":[],"resourceSpans":[]}',
        b'{"resourceSpans":[],"ignored":NaN}',
        b'{"resourceSpans":[],"ignored":Infinity}',
        b'{"resourceSpans":[],"ignored":1e999}',
        b"[]",
        b"{}",
        b"not-json",
        b"\xff",
    ],
)
def test_invalid_or_ambiguous_json_does_not_leave_a_partial_result(raw):
    with pytest.raises(TraceImportError):
        normalize_otlp(raw, window_end=END, imported_at=END, source_kind="synthetic")


def test_empty_export_is_missing_coverage_not_healthy_topology():
    result = normalize({"resourceSpans": []})
    assert not result["observed_calls"] and result["retained_spans"] == 0
    assert result["coverage_kind"] == "sampled-export-only" and not result["complete_topology"]
    assert "HEALTHY" not in json.dumps(result)
    assert len(result["coverage"]["registered_services_without_spans"]) == 12


def test_unset_status_and_kind_remain_unknown_not_healthy_or_a_network_call():
    data = exported()
    for group in data["resourceSpans"]:
        row = group["scopeSpans"][0]["spans"][0]
        del row["kind"]
        del row["status"]
    result = normalize(data)
    assert all(row["kind"] == row["status_code"] == 0 for row in result["spans"])
    assert not result["observed_calls"] and "HEALTHY" not in json.dumps(result)


def test_server_error_count_does_not_promote_client_status_to_server_failure():
    data = exported()
    data["resourceSpans"][1]["scopeSpans"][0]["spans"][0]["status"] = {"code": 0}
    result = normalize(data)
    assert result["observed_calls"][0]["server_error_pairs"] == 0
    assert result["coverage"]["runtime_instance_missing_spans"] == 2


def test_child_root_has_no_inferred_remote_call_even_with_overlapping_client():
    data = exported()
    del data["resourceSpans"][1]["scopeSpans"][0]["spans"][0]["parentSpanId"]
    result = normalize(data)
    assert not result["observed_calls"] and not result["coverage"]["missing_parent_spans"]


def test_unregistered_spans_do_not_skip_global_byte_or_duplicate_json_validation():
    raw = b'{"resourceSpans":[],"ignored":{"secret":1,"secret":2}}'
    with pytest.raises(TraceImportError, match="duplicate_json_field"):
        normalize_otlp(raw, window_end=END, source_kind="synthetic", imported_at=END)


def test_deeply_nested_ignored_json_is_rejected_without_raw_exception():
    raw = b'{"resourceSpans":[],"ignored":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    with pytest.raises(TraceImportError, match="json_depth_limit|invalid_json"):
        normalize_otlp(raw, window_end=END, source_kind="synthetic", imported_at=END)


def test_total_json_nodes_are_bounded_even_in_ignored_fields():
    raw = json.dumps({"resourceSpans": [], "ignored": [0] * JSON_NODE_LIMIT}).encode()
    assert len(raw) < INPUT_LIMIT
    with pytest.raises(TraceImportError, match="json_node_limit"):
        normalize_otlp(raw, window_end=END, source_kind="synthetic", imported_at=END)


def test_span_byte_and_output_limits_apply_without_silent_truncation(monkeypatch):
    with pytest.raises(TraceImportError, match="input_limit"):
        normalize_otlp(
            b" " * (INPUT_LIMIT + 1), window_end=END, imported_at=END, source_kind="synthetic"
        )
    data = {"resourceSpans": [resource("unknown-service", [span(CLIENT)] * (SPAN_LIMIT + 1))]}
    with pytest.raises(TraceImportError, match="span_limit"):
        normalize(data)
    monkeypatch.setattr("core.trace_evidence.OUTPUT_LIMIT", 100)
    with pytest.raises(TraceImportError, match="output_limit"):
        normalize(exported())


@pytest.mark.parametrize(
    "window_end,imported_at,source,seconds",
    [
        (END.replace(tzinfo=None), END, "synthetic", 60),
        (END, END.replace(tzinfo=None), "synthetic", 60),
        (END + timedelta(seconds=1), END, "synthetic", 60),
        (END, END, "live-attested", 60),
        (END, END, "synthetic", 61),
        (END, END, "synthetic", True),
    ],
)
def test_clock_source_and_window_cannot_silently_change_contract(
    window_end, imported_at, source, seconds
):
    with pytest.raises(TraceImportError):
        normalize_otlp(
            b'{"resourceSpans":[]}',
            window_end=window_end,
            imported_at=imported_at,
            source_kind=source,
            window_seconds=seconds,
        )


def test_resource_order_does_not_change_canonical_report():
    data = exported()
    before = normalize(data)
    data["resourceSpans"].reverse()
    assert normalize(data) == before


def test_documented_fixture_is_synthetic_and_has_the_documented_pair():
    path = Path(__file__).resolve().parents[1] / "eval/fixtures/trace_example.json"
    report = normalize(json.loads(path.read_text()))
    assert report["source_kind"] == "synthetic" and report["retained_spans"] == 2
    assert [(edge["caller"], edge["callee"]) for edge in report["observed_calls"]] == [
        ("checkoutservice", "paymentservice")
    ]


def test_claiming_retained_source_does_not_attest_runtime_or_export():
    report = normalize_otlp(
        json.dumps(exported()).encode(),
        window_end=END,
        imported_at=END,
        source_kind="retained-export",
    )
    assert report["source_kind"] == "retained-export"
    assert not report["source_authenticated"] and not report["runtime_identity_attested"]


@pytest.mark.parametrize("imported_at", [False, 0, datetime(9999, 1, 1, tzinfo=timezone.utc)])
def test_invalid_optional_import_clock_is_not_replaced_with_now(imported_at):
    with pytest.raises(TraceImportError, match="invalid_clock"):
        normalize_otlp(
            b'{"resourceSpans":[]}',
            window_end=END,
            imported_at=imported_at,
            source_kind="synthetic",
        )


def test_equivalent_timezone_cutoff_keeps_logical_revision_and_ids():
    before = normalize(exported())
    alternate = END.astimezone(timezone(timedelta(hours=5, minutes=30)))
    after = normalize_otlp(
        json.dumps(exported()).encode(),
        window_end=alternate,
        imported_at=END + timedelta(seconds=1),
        source_kind="synthetic",
    )
    assert before["topology_revision"] == after["topology_revision"]
    assert before["spans"] == after["spans"]


def test_local_import_creates_private_snapshot_and_refuses_overwrite(tmp_path):
    source, target = tmp_path / "raw.json", tmp_path / "private" / "trace.json"
    source.write_text(json.dumps(exported()))
    result = import_trace_file(source, target, window_end=END, source_kind="synthetic")
    assert json.loads(target.read_text()) == result and "private-" not in target.read_text()
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert os.stat(target.parent).st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        import_trace_file(source, target, window_end=END, source_kind="synthetic")


def test_symlink_and_nonregular_input_are_refused_without_writing_output(tmp_path):
    source, link, target = tmp_path / "raw.json", tmp_path / "link", tmp_path / "result.json"
    source.write_text(json.dumps(exported()))
    link.symlink_to(source)
    with pytest.raises(OSError):
        import_trace_file(link, target, window_end=END, source_kind="synthetic")
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(TraceImportError, match="input_not_regular_file"):
        import_trace_file(fifo, target, window_end=END, source_kind="synthetic")
    assert not target.exists()
    target.symlink_to(tmp_path / "missing")
    with pytest.raises(FileExistsError):
        import_trace_file(source, target, window_end=END, source_kind="synthetic")


def test_failed_parse_writes_no_raw_or_partial_snapshot(tmp_path):
    source, target = tmp_path / "raw.json", tmp_path / "result.json"
    source.write_text("private-invalid-payload")
    with pytest.raises(TraceImportError, match="invalid_json"):
        import_trace_file(source, target, window_end=END, source_kind="synthetic")
    assert not target.exists()


def test_actual_oversized_file_is_not_fully_imported_or_copied(tmp_path):
    source, target = tmp_path / "raw.json", tmp_path / "result.json"
    source.write_bytes(b" " * (INPUT_LIMIT + 100))
    with pytest.raises(TraceImportError, match="input_limit"):
        import_trace_file(source, target, window_end=END, source_kind="synthetic")
    assert not target.exists() and source.stat().st_size == INPUT_LIMIT + 100


def test_cli_is_offline_and_sanitizes_bad_clock_or_file_errors(tmp_path):
    source, target = tmp_path / "private-source.json", tmp_path / "result.json"
    source.write_text(json.dumps(exported()))
    command = [
        sys.executable,
        "-m",
        "simulation.trace_import",
        "--input",
        str(source),
        "--output",
        str(target),
        "--window-end",
        END.isoformat(),
        "--source-kind",
        "synthetic",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout)["observed_calls"] == 1 and not completed.stderr
    command[-3] = "private-bad-clock"
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode == 1 and "private" not in rejected.stdout + rejected.stderr


def test_import_loads_no_settings_docker_provider_graph_or_executor():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import simulation.trace_import; "
            "assert not any(name in sys.modules for name in ('core.config','docker','neo4j',"
            "'agent.graph','agent.shadow_provider','agent.nodes.executor','langchain_openai','openai'))",
        ],
        capture_output=True,
        check=True,
    )
