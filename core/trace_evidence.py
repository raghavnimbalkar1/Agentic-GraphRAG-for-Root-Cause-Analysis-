"""Bounded OTLP/JSON projection. Exported trace context is evidence, not causality."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.registry import DEFAULT_DEPLOYMENT as REGISTRY

INPUT_LIMIT = 1024 * 1024
SPAN_LIMIT = 256
OUTPUT_LIMIT = 512 * 1024
JSON_DEPTH_LIMIT = 32
JSON_NODE_LIMIT = 65536
UINT64_MAX = 2**64 - 1
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class TraceImportError(ValueError):
    """Fixed reason only; never include an untrusted name, attribute or exception."""


class RetainedSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service_id: str
    instance_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    trace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    span_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    start_ns: int = Field(ge=1, le=UINT64_MAX)
    end_ns: int = Field(ge=1, le=UINT64_MAX)
    kind: Literal[0, 1, 2, 3, 4, 5]
    status_code: Literal[0, 1, 2]

    @field_validator("kind", "status_code", mode="before")
    @classmethod
    def integer_enum(cls, value):
        if type(value) is not int:
            raise ValueError("Trace enums must be integers")
        return value

    @model_validator(mode="after")
    def consistent_span(self):
        if self.service_id not in REGISTRY.by_name:
            raise ValueError("Unregistered trace service")
        if not int(self.trace_id, 16) or not int(self.span_id, 16):
            raise ValueError("Zero trace identifiers are invalid")
        if self.parent_span_id is not None and (
            not int(self.parent_span_id, 16) or self.parent_span_id == self.span_id
        ):
            raise ValueError("Invalid parent span")
        if self.end_ns < self.start_ns:
            raise ValueError("Reversed span interval")
        return self

    def projected(self):
        data = self.model_dump()
        encoded = json.dumps([REGISTRY.digest, data], sort_keys=True, separators=(",", ":"))
        return {"evidence_id": "SPAN-" + sha256(encoded.encode()).hexdigest(), **data}


def _object(value):
    if not isinstance(value, dict):
        raise TraceImportError("invalid_structure")
    return value


def _array(value):
    if not isinstance(value, list):
        raise TraceImportError("invalid_structure")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TraceImportError("duplicate_json_field")
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise TraceImportError("nonfinite_json")
    return result


def _bounded_structure(data):
    pending, visited = [(data, 0)], 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if depth > JSON_DEPTH_LIMIT:
            raise TraceImportError("json_depth_limit")
        values = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
        if visited + len(pending) + len(values) > JSON_NODE_LIMIT:
            raise TraceImportError("json_node_limit")
        pending.extend((value, depth + 1) for value in values)


def _uint64(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        value = int(value)
    if type(value) is not int or not 0 < value <= UINT64_MAX:
        raise TraceImportError("invalid_timestamp")
    return value


def _identifier(value, length):
    if not isinstance(value, str) or re.fullmatch(rf"[a-fA-F0-9]{{{length}}}", value) is None:
        raise TraceImportError("invalid_identifier")
    return value.lower()


def _resource_identity(resource):
    # Only these two resource fields survive; names/attributes/events are not hashed.
    known = {}
    for attribute in _array(_object(resource).get("attributes", [])):
        attribute = _object(attribute)
        key = attribute.get("key")
        if key not in ("service.name", "container.id"):
            continue
        value = _object(attribute.get("value"))
        if (
            key in known
            or set(value) != {"stringValue"}
            or not isinstance(value["stringValue"], str)
        ):
            raise TraceImportError("invalid_resource_identity")
        known[key] = value["stringValue"]
    instance = known.get("container.id")
    invalid_instance = instance is not None and re.fullmatch(r"[a-fA-F0-9]{64}", instance) is None
    return (
        known.get("service.name"),
        None if invalid_instance or instance is None else instance.lower(),
        invalid_instance,
    )


def _nanoseconds(stamp):
    if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
        raise TraceImportError("invalid_clock")
    delta = stamp.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def _check_cycles(spans):
    completed = set()
    for key in spans:
        path = set()
        while key in spans and key not in completed:
            if key in path:
                raise TraceImportError("cyclic_parent_context")
            path.add(key)
            span = spans[key]
            key = (span.trace_id, span.parent_span_id)
        completed.update(path)


def observed_calls(spans):
    """Join direct CLIENT -> SERVER context only; never infer peers from attributes."""
    indexed = {(span.trace_id, span.span_id): span for span in spans}
    edges = {}
    counts = {
        "missing_parent_spans": 0,
        "unsupported_cross_service_links": 0,
        "clock_order_warnings": 0,
    }
    for child in spans:
        if child.parent_span_id is None:
            continue
        parent = indexed.get((child.trace_id, child.parent_span_id))
        if parent is None:
            counts["missing_parent_spans"] += 1
            continue
        if parent.service_id == child.service_id:
            continue
        if (parent.kind, child.kind) != (3, 2):
            counts["unsupported_cross_service_links"] += 1
            continue
        skew = child.start_ns < parent.start_ns or child.end_ns > parent.end_ns
        counts["clock_order_warnings"] += int(skew)
        key = (parent.service_id, child.service_id)
        edge = edges.setdefault(
            key,
            {
                "caller": key[0],
                "callee": key[1],
                "span_pair_count": 0,
                "server_error_pairs": 0,
                "first_seen_ns": child.start_ns,
                "last_seen_ns": child.end_ns,
                "evidence_pairs": [],
            },
        )
        edge["span_pair_count"] += 1
        edge["server_error_pairs"] += int(child.status_code == 2)
        edge["first_seen_ns"] = min(edge["first_seen_ns"], child.start_ns)
        edge["last_seen_ns"] = max(edge["last_seen_ns"], child.end_ns)
        edge["evidence_pairs"].append(
            [parent.projected()["evidence_id"], child.projected()["evidence_id"]]
        )
    return [edges[key] for key in sorted(edges)], counts


def normalize_otlp(raw, *, window_end, source_kind, window_seconds=60, imported_at=None):
    """One local ExportTraceServiceRequest object, not a general OTLP receiver."""
    imported_at = datetime.now(timezone.utc) if imported_at is None else imported_at
    cutoff_ns, available_ns = _nanoseconds(window_end), _nanoseconds(imported_at)
    if type(window_seconds) is not int or window_seconds not in (60, 300, 900):
        raise TraceImportError("unsupported_window")
    if source_kind not in ("synthetic", "retained-export"):
        raise TraceImportError("invalid_source_kind")
    if not 0 < cutoff_ns <= available_ns <= UINT64_MAX:
        raise TraceImportError("invalid_clock")
    if not isinstance(raw, bytes) or len(raw) > INPUT_LIMIT:
        raise TraceImportError("input_limit")
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_float=_finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(TraceImportError("nonfinite_json")),
        )
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, TraceImportError):
            raise
        raise TraceImportError("invalid_json") from None
    _bounded_structure(data)
    groups = _array(_object(data).get("resourceSpans"))
    counts = {
        "input_spans": 0,
        "duplicate_spans": 0,
        "outside_window_spans": 0,
        "unregistered_resource_groups": 0,
        "unregistered_spans": 0,
        "invalid_instance_resource_groups": 0,
    }
    known = {}
    for group in groups:
        group = _object(group)
        service, instance, invalid_instance = _resource_identity(group.get("resource", {}))
        registered = service in REGISTRY.by_name if isinstance(service, str) else False
        counts["unregistered_resource_groups"] += int(not registered)
        counts["invalid_instance_resource_groups"] += int(invalid_instance)
        for scope in _array(group.get("scopeSpans", [])):
            for item in _array(_object(scope).get("spans", [])):
                item = _object(item)
                counts["input_spans"] += 1
                if counts["input_spans"] > SPAN_LIMIT:
                    raise TraceImportError("span_limit")
                if not registered:
                    counts["unregistered_spans"] += 1
                    continue
                parent = item.get("parentSpanId", "")
                try:
                    span = RetainedSpan(
                        service_id=service,
                        instance_id=instance,
                        trace_id=_identifier(item.get("traceId"), 32),
                        span_id=_identifier(item.get("spanId"), 16),
                        parent_span_id=_identifier(parent, 16) if parent != "" else None,
                        start_ns=_uint64(item.get("startTimeUnixNano")),
                        end_ns=_uint64(item.get("endTimeUnixNano")),
                        kind=item.get("kind", 0),
                        status_code=_object(item.get("status", {})).get("code", 0),
                    )
                except ValueError as error:
                    if isinstance(error, TraceImportError):
                        raise
                    raise TraceImportError("invalid_span") from None
                key = (span.trace_id, span.span_id)
                if key in known:
                    if known[key] != span:
                        raise TraceImportError("conflicting_span_identity")
                    counts["duplicate_spans"] += 1
                known[key] = span
    _check_cycles(known)
    start_ns = cutoff_ns - window_seconds * 10**9
    selected = sorted(
        (span for span in known.values() if start_ns <= span.end_ns <= cutoff_ns),
        key=lambda span: (span.trace_id, span.span_id),
    )
    counts["outside_window_spans"] = len(known) - len(selected)
    edges, link_counts = observed_calls(selected)
    link_counts["registered_services_without_spans"] = sorted(
        set(REGISTRY.by_name) - {span.service_id for span in selected}
    )
    link_counts["runtime_instance_missing_spans"] = sum(
        span.instance_id is None for span in selected
    )
    logical = {
        "registry_digest": REGISTRY.digest,
        "source_kind": source_kind,
        "window_start_ns": start_ns,
        "window_end_ns": cutoff_ns,
        "spans": [span.projected() for span in selected],
        "coverage": {**counts, **link_counts},
    }
    revision = sha256(
        json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = {
        "schema_version": 1,
        "kind": "normalized-trace-evidence",
        "source": "otlp-json-file",
        "source_kind": source_kind,
        "source_authenticated": False,
        "runtime_identity_attested": False,
        "environment_id": REGISTRY.environment_id,
        "registry_revision": REGISTRY.revision,
        "registry_digest": REGISTRY.digest,
        "interpretation_version": "otlp-client-server-v1",
        "window_start": (window_end - timedelta(seconds=window_seconds)).isoformat(),
        "window_end": window_end.isoformat(),
        "available_at": imported_at.isoformat(),
        "topology_revision": revision,
        "coverage_kind": "sampled-export-only",
        "complete_topology": False,
        "retained_spans": len(selected),
        "spans": logical["spans"],
        "coverage": logical["coverage"],
        "observed_calls": edges,
        "declared_topology_changed": False,
        "causal_attribution": False,
        "execution_authorized": False,
    }
    if len(json.dumps(result).encode()) > OUTPUT_LIMIT:
        raise TraceImportError("output_limit")
    return result
