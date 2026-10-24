"""Validated, bounded trace retention and read-only windows; no collection or repairs."""

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import math

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from core.diagnostics import ComponentReader, DiagnosticRequest
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.trace_evidence import (
    JSON_NODE_LIMIT, OUTPUT_LIMIT, SPAN_LIMIT, UINT64_MAX, RetainedSpan,
    TraceImportError, _bounded_structure, _check_cycles, _finite_float, _nanoseconds, _unique, observed_calls,
)

SNAPSHOT_LIMIT = 32
VIEW_SPAN_LIMIT = 32


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class _Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input_spans: int = Field(ge=0, le=SPAN_LIMIT)
    duplicate_spans: int = Field(ge=0, le=SPAN_LIMIT)
    outside_window_spans: int = Field(ge=0, le=SPAN_LIMIT)
    unregistered_resource_groups: int = Field(ge=0, le=JSON_NODE_LIMIT)
    unregistered_spans: int = Field(ge=0, le=SPAN_LIMIT)
    invalid_instance_resource_groups: int = Field(ge=0, le=JSON_NODE_LIMIT)
    missing_parent_spans: int = Field(ge=0, le=SPAN_LIMIT)
    unsupported_cross_service_links: int = Field(ge=0, le=SPAN_LIMIT)
    clock_order_warnings: int = Field(ge=0, le=SPAN_LIMIT)
    registered_services_without_spans: list[str] = Field(max_length=len(REGISTRY.services))
    runtime_instance_missing_spans: int = Field(ge=0, le=SPAN_LIMIT)


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(strict=True, ge=1, le=1)
    kind: str
    source: str
    source_kind: str
    source_authenticated: bool = Field(strict=True)
    runtime_identity_attested: bool = Field(strict=True)
    environment_id: str
    registry_revision: str
    registry_digest: str
    interpretation_version: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    available_at: AwareDatetime
    topology_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    coverage_kind: str
    complete_topology: bool = Field(strict=True)
    retained_spans: int = Field(strict=True, ge=0, le=SPAN_LIMIT)
    spans: list[dict] = Field(max_length=SPAN_LIMIT)
    coverage: _Coverage
    observed_calls: list[dict] = Field(max_length=SPAN_LIMIT)
    declared_topology_changed: bool = Field(strict=True)
    causal_attribution: bool = Field(strict=True)
    execution_authorized: bool = Field(strict=True)

    @field_validator("window_start", "window_end", "available_at", mode="before")
    @classmethod
    def explicit_datetime(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Explicit aware trace clocks are required")
        return value


def validate_snapshot(value):
    """Recompute identities, joins, coverage and revision at every trust boundary."""
    try:
        _bounded_structure(value)
        if len(_encoded(value)) > OUTPUT_LIMIT:
            raise ValueError("snapshot_limit")
        record = _Snapshot.model_validate(value)
        fixed = {"kind": "normalized-trace-evidence", "source": "otlp-json-file",
                 "environment_id": REGISTRY.environment_id, "registry_digest": REGISTRY.digest,
                 "registry_revision": REGISTRY.revision, "interpretation_version": "otlp-client-server-v1",
                 "coverage_kind": "sampled-export-only"}
        if any(getattr(record, key) != expected for key, expected in fixed.items()):
            raise ValueError("invalid_scope")
        if record.source_kind not in {"synthetic", "retained-export"} or any(
            getattr(record, key) for key in ("source_authenticated", "runtime_identity_attested",
                "complete_topology", "declared_topology_changed", "causal_attribution", "execution_authorized")
        ):
            raise ValueError("invalid_authority")
        start, end, available = map(_nanoseconds, (record.window_start, record.window_end, record.available_at))
        if end - start not in {60 * 10**9, 300 * 10**9, 900 * 10**9} or not 0 < end <= available <= UINT64_MAX:
            raise ValueError("invalid_window")
        spans = []
        for row in record.spans:
            span = RetainedSpan.model_validate({key: value for key, value in row.items() if key != "evidence_id"})
            if _encoded(row) != _encoded(span.projected()) or not start <= span.end_ns <= end:
                raise ValueError("invalid_span_identity")
            spans.append(span)
        keys = [(span.trace_id, span.span_id) for span in spans]
        if keys != sorted(set(keys)) or len(spans) != record.retained_spans:
            raise ValueError("invalid_span_order")
        _check_cycles(dict(zip(keys, spans)))
        edges, counts = observed_calls(spans)
        counts["registered_services_without_spans"] = sorted(set(REGISTRY.by_name) - {s.service_id for s in spans})
        counts["runtime_instance_missing_spans"] = sum(s.instance_id is None for s in spans)
        coverage = record.coverage.model_dump()
        if any(_encoded(coverage[key]) != _encoded(value) for key, value in counts.items()):
            raise ValueError("invalid_coverage")
        if (coverage["input_spans"] != len(spans) + coverage["duplicate_spans"]
                + coverage["outside_window_spans"] + coverage["unregistered_spans"]
                or _encoded(record.observed_calls) != _encoded(edges)):
            raise ValueError("invalid_joins")
        logical = {"registry_digest": REGISTRY.digest, "source_kind": record.source_kind,
                   "window_start_ns": start, "window_end_ns": end,
                   "spans": [span.projected() for span in spans], "coverage": coverage}
        if sha256(_encoded(logical)).hexdigest() != record.topology_revision:
            raise ValueError("invalid_revision")
        result = record.model_dump(mode="json")
        for field in ("window_start", "window_end", "available_at"):
            result[field] = getattr(record, field).astimezone(timezone.utc).isoformat()
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise TraceImportError("invalid_trace_snapshot") from None


def snapshot_id(snapshot):
    return "TRACE-" + sha256(_encoded(validate_snapshot(snapshot))).hexdigest()


def decode_snapshot(raw):
    if not isinstance(raw, bytes) or len(raw) > OUTPUT_LIMIT:
        raise TraceImportError("invalid_trace_snapshot")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_float=_finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite trace JSON")))
        return validate_snapshot(value)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise TraceImportError("invalid_trace_snapshot") from None


def create_trace_table(db):
    # Additive to evidence schema v1. Older stores/readers remain valid.
    db.execute("CREATE TABLE IF NOT EXISTS trace_snapshots (id TEXT PRIMARY KEY, environment TEXT NOT NULL, "
               "registry TEXT NOT NULL, measured REAL NOT NULL, available REAL NOT NULL, "
               "stored REAL NOT NULL, payload TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS trace_window ON trace_snapshots(environment, registry, measured, stored)")


def append_snapshot(db, value, *, stored_at):
    snapshot = validate_snapshot(value)
    stamp = datetime.fromisoformat(snapshot["available_at"]).timestamp()
    if (type(stored_at) not in (float, int) or not math.isfinite(stored_at)
            or not stamp <= stored_at <= UINT64_MAX / 10**9):
        raise TraceImportError("trace_not_yet_available")
    identifier, encoded = snapshot_id(snapshot), _encoded(snapshot).decode()
    create_trace_table(db)
    old = db.execute("SELECT payload FROM trace_snapshots WHERE id=?", (identifier,)).fetchone()
    if old is not None and old[0] != encoded:
        raise TraceImportError("trace_identity_conflict")
    db.execute("INSERT OR IGNORE INTO trace_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
               (identifier, REGISTRY.environment_id, REGISTRY.digest,
                datetime.fromisoformat(snapshot["window_end"]).timestamp(), stamp, stored_at, encoded))
    return identifier


def trace_rows(db, *, cutoff, window_end=None, window_seconds=900):
    """No automatic migration on reads. Reject oversized/invalid selected scopes."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trace_snapshots'").fetchone():
        return []
    end = cutoff if window_end is None else window_end
    rows = db.execute(
        "SELECT id, measured, available, stored, CASE WHEN length(CAST(payload AS BLOB))<=? "
        "THEN payload ELSE NULL END FROM trace_snapshots WHERE environment=? AND registry=? "
        "AND measured BETWEEN ? AND ? AND available<=? AND stored BETWEEN ? AND ? ORDER BY id LIMIT ?",
        (OUTPUT_LIMIT, REGISTRY.environment_id, REGISTRY.digest, end - window_seconds, end,
         cutoff, cutoff - 86400, cutoff, SNAPSHOT_LIMIT + 1)).fetchall()
    if len(rows) > SNAPSHOT_LIMIT:
        raise TraceImportError("trace_scope_limit")
    result = []
    for identifier, measured, available, stored, raw in rows:
        if not isinstance(raw, str):
            raise TraceImportError("trace_scope_limit")
        try:
            snapshot = decode_snapshot(raw.encode())
            if (snapshot_id(snapshot) != identifier
                    or datetime.fromisoformat(snapshot["window_end"]).timestamp() != measured
                    or datetime.fromisoformat(snapshot["available_at"]).timestamp() != available
                    or not measured <= available <= stored <= cutoff):
                raise ValueError("Trace column mismatch")
        except (ValueError, TypeError, RecursionError):
            raise TraceImportError("invalid_trace_snapshot") from None
        result.append({"snapshot_id": identifier, "stored_at": stored, "snapshot": snapshot})
    return result


class TraceReader:
    """Latest available export only. Never merge conflicting exporters into a topology."""

    def __init__(self, path):
        self.path = path

    def window(self, request: DiagnosticRequest, *, now: float):
        request = DiagnosticRequest.model_validate(request)
        if request.service_id not in REGISTRY.by_name or request.metric is not None:
            raise ValueError("Trace windows require a registered target and no metric")
        if type(now) not in (float, int) or not 0 < now <= UINT64_MAX / 10**9:
            raise ValueError("Invalid trace clock")
        end = request.end_at.timestamp() if request.end_at else now
        if end > now:
            raise ValueError("Trace windows cannot end in the future")
        start = end - request.window_seconds
        end_ns = _nanoseconds(request.end_at if request.end_at else datetime.fromtimestamp(now, timezone.utc))
        start_ns = end_ns - request.window_seconds * 10**9
        with closing(ComponentReader(self.path)._connect()) as db:
            rows = trace_rows(db, cutoff=now, window_end=end, window_seconds=request.window_seconds)
        # Choosing the newest complete export is explicit, not target-dependent fallback.
        rows = [row for row in rows if start <= datetime.fromisoformat(row["snapshot"]["window_end"]).timestamp() <= end]
        rows.sort(key=lambda row: (row["snapshot"]["window_end"], row["stored_at"], row["snapshot_id"]))
        selected = rows[-1] if rows else None
        spans, coverage, revision, edges, view_coverage = [], None, None, [], None
        matching = 0
        if selected:
            snapshot = selected["snapshot"]
            scope = [row for row in snapshot["spans"] if start_ns <= row["end_ns"] <= end_ns]
            scoped_ids = {row["evidence_id"] for row in scope}
            relevant_ids = {row["evidence_id"] for row in scope if row["service_id"] == request.service_id}
            for edge in snapshot["observed_calls"]:
                if request.service_id in {edge["caller"], edge["callee"]}:
                    for pair in edge["evidence_pairs"]:
                        if set(pair) <= scoped_ids:
                            relevant_ids.update(pair)
            relevant = [row for row in scope if row["evidence_id"] in relevant_ids]
            matching = len(relevant)
            relevant.sort(key=lambda row: (row["end_ns"], row["evidence_id"]))
            spans = relevant[-VIEW_SPAN_LIMIT:]
            edges, view_coverage = observed_calls([RetainedSpan.model_validate({key: value for key, value in row.items()
                                      if key != "evidence_id"}) for row in spans])
            coverage, revision = snapshot["coverage"], snapshot["topology_revision"]
        stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
        return {"schema_version": 1, "mode": "retained-evidence-only", "service_id": request.service_id,
                "environment_id": REGISTRY.environment_id, "registry_digest": REGISTRY.digest,
                "registry_revision": REGISTRY.revision, "source": "otlp-json-file",
                "read_at": stamp(now), "window_start": stamp(start), "window_end": stamp(end),
                "snapshot_id": selected["snapshot_id"] if selected else None,
                "snapshot_available_at": selected["snapshot"]["available_at"] if selected else None,
                "snapshot_stored_at": stamp(selected["stored_at"]) if selected else None,
                "snapshot_window_start": selected["snapshot"]["window_start"] if selected else None,
                "snapshot_window_end": selected["snapshot"]["window_end"] if selected else None,
                "source_kind": selected["snapshot"]["source_kind"] if selected else None,
                "topology_revision": revision, "selection": "latest-export-only-no-merge",
                "eligible_exports": len(rows), "omitted_exports": max(0, len(rows) - 1),
                "sample_limit": VIEW_SPAN_LIMIT, "matching_spans": matching, "truncated": matching > len(spans),
                "snapshot_coverage": coverage, "view_coverage": view_coverage, "spans": spans, "observed_calls": edges,
                "coverage_kind": "sampled-export-only", "complete_topology": False,
                "source_authenticated": False, "runtime_identity_attested": False,
                "declared_topology_changed": False, "causal_attribution": False, "execution_authorized": False}
