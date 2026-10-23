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


def compact_schema(schema):
    """Remove display titles only; retain defaults, descriptions and validation rules."""
    if isinstance(schema, list):
        return [compact_schema(value) for value in schema]
    if isinstance(schema, dict):
        return {key: compact_schema(value) for key, value in schema.items() if key != "title"}
    return schema
