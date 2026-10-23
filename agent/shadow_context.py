"""Lossless, opt-in prompt encoding. No evidence selection or provider dependency."""

from collections import Counter
from copy import deepcopy
import json

ENCODING = "shared-metadata-v1"
METADATA = frozenset({"environment_id", "registry_revision", "registry_digest", "evaluated_as_of",
                      "measured_at", "collected_at", "window_start", "window_end", "instance_id"})
COMPACT_INSTRUCTIONS = """
The user message uses shared-metadata-v1. Read its view as the investigation
context. Each object with "$shared": N inherits the exact fields in
shared_metadata[N]; its other fields remain unchanged. This is lossless metadata
factoring, not missing evidence. No fields, samples or IDs are dropped. The
decision response must still use the DECISION SCHEMA, not this context encoding.
"""


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def pack_view(view):
    """Factor only identical, repeated metadata groups; leave all other facts intact."""
    counts = Counter()

    def group(node):
        return {key: value for key, value in node.items() if key in METADATA}

    def visit(node):
        if isinstance(node, dict):
            if "$shared" in node:
                raise ValueError("Reserved context encoding field")
            fields = group(node)
            if fields:
                counts[_encoded(fields)] += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(view)
    # Include the table cost and a reference per use; never factor singletons.
    groups = sorted(key for key, count in counts.items() if count > 1
                    and (len(key.encode()) - 2) * count > len(key.encode()) + 24 * count)
    indices = {key: index for index, key in enumerate(groups)}

    def replace(node):
        if isinstance(node, list):
            return [replace(value) for value in node]
        if not isinstance(node, dict):
            return node
        key = _encoded(group(node))
        if key in indices:
            return {"$shared": indices[key], **{name: replace(value) for name, value in node.items()
                                               if name not in METADATA}}
        return {name: replace(value) for name, value in node.items()}

    return {"context_encoding": ENCODING, "shared_metadata": [json.loads(key) for key in groups],
            "view": replace(view)}


def unpack_view(encoded):
    """Read the exact public facts in either profile, including the fixed-policy arm."""
    if encoded.get("context_encoding") == "column-records-v1":
        if set(encoded) != {"context_encoding", "columns", "view"} or not isinstance(encoded["columns"], list):
            raise ValueError("Invalid column context")
        columns = encoded["columns"]
        if any(not isinstance(row, list) or not row or any(not isinstance(key, str) for key in row)
               or len(row) != len(set(row)) for row in columns):
            raise ValueError("Invalid context columns")
        def expand_record(node):
            if isinstance(node, list):
                return [expand_record(value) for value in node]
            if not isinstance(node, dict):
                return node
            if "$record" in node:
                row = node["$record"]
                if set(node) != {"$record"} or not isinstance(row, list) or not row:
                    raise ValueError("Invalid column record")
                index = row[0]
                if type(index) is not int or not 0 <= index < len(columns) or len(row) != len(columns[index]) + 1:
                    raise ValueError("Invalid column reference or row length")
                return {key: expand_record(value) for key, value in zip(columns[index], row[1:], strict=True)}
            return {key: expand_record(value) for key, value in node.items()}
        return unpack_view(expand_record(encoded["view"]))
    if "context_encoding" not in encoded:
        return deepcopy(encoded)
    if (set(encoded) != {"context_encoding", "shared_metadata", "view"}
            or encoded["context_encoding"] != ENCODING or not isinstance(encoded["shared_metadata"], list)):
        raise ValueError("Invalid context encoding")
    shared = encoded["shared_metadata"]
    if any(not isinstance(row, dict) or not row or not set(row) <= METADATA
           or any(isinstance(value, (dict, list)) for value in row.values()) for row in shared):
        raise ValueError("Invalid shared metadata")

    def expand(node):
        if isinstance(node, list):
            return [expand(value) for value in node]
        if not isinstance(node, dict):
            return node
        fields = {}
        if "$shared" in node:
            index = node["$shared"]
            if type(index) is not int or not 0 <= index < len(shared):
                raise ValueError("Invalid shared metadata reference")
            fields = shared[index]
            if set(fields) & (set(node) - {"$shared"}):
                raise ValueError("Conflicting shared metadata")
        return {**fields, **{key: expand(value) for key, value in node.items() if key != "$shared"}}

    return expand(encoded["view"])


TABLE_INSTRUCTIONS = """
The outer context uses column-records-v1. columns[N] lists field names. An object
{"$record":[N,value1,value2,...]} has exactly those fields and values in that
order. Expand these records first, then apply shared-metadata-v1 inheritance.
This factors repeated field names only; every evidence fact and ID remains.
"""


def pack_tabular(view):
    """Lossless column layouts for repeated object shapes, including nested evidence."""
    shared = pack_view(view)
    counts = Counter()
    def visit(node):
        if isinstance(node, dict):
            if "$record" in node:
                raise ValueError("Reserved column record field")
            keys = tuple(sorted(node))
            if len(keys) >= 3:
                counts[keys] += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(shared)
    layouts = sorted(keys for keys, count in counts.items() if count >= 2)
    indices = {keys: index for index, keys in enumerate(layouts)}
    def replace(node):
        if isinstance(node, list):
            return [replace(value) for value in node]
        if not isinstance(node, dict):
            return node
        keys = tuple(sorted(node))
        if keys in indices:
            return {"$record": [indices[keys], *[replace(node[key]) for key in keys]]}
        return {key: value if key in {"evidence_index", "dependency_index"} else replace(value) for key, value in node.items()}
    return {"context_encoding": "column-records-v1", "columns": [list(keys) for keys in layouts], "view": replace(shared)}


def observation_index(turns):
    """Readable duplicate of already displayed component facts; no candidate ranking."""
    rows = []
    for turn in turns:
        for feedback in turn.get("feedback", []):
            if feedback.get("tool") not in {"component_window", "resource_window"}:
                continue
            data = feedback.get("data")
            row = {"service": (feedback.get("request") or {}).get("service_id"),
                   "read_outcome": feedback.get("outcome")}
            if data:
                summary = data["summary"]
                row.update(window_status=summary["status_at_window_end"], fresh=summary["fresh_at_cutoff"],
                    conflict=summary["latest_conflict"], observations=[{"id": sample["evidence_id"],
                        "status": sample["status"], "quality": sample["quality"]} for sample in data["samples"]])
            rows.append(row)
    return rows


def compact_schema(schema):
    """Remove display titles only; retain defaults, descriptions and validation rules."""
    if isinstance(schema, list):
        return [compact_schema(value) for value in schema]
    if isinstance(schema, dict):
        return {key: compact_schema(value) for key, value in schema.items() if key != "title"}
    return schema
