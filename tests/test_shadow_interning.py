"""Lossless two-epoch context encoding; no provider call or budget increase."""

from copy import deepcopy
import json

import pytest

from agent.shadow import ShadowRunner
from agent.shadow_context import pack_interned, pack_tabular, unpack_view
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import materialize
from tests.test_replanning_profile import full_epochs, run_profile
from tests.test_shadow_replan import Clock
from agent.investigation import InvestigationSession


def test_repeated_strings_roundtrip_all_nested_facts_including_exact_evidence_ids():
    value = "OBS-" + "a" * 32
    view = {"initial": {"registry_digest": "f" * 64}, "turns": [
        {"id": value, "registry_digest": "f" * 64, "metadata": {"measured_at": "2026-10-24T12:00:00+00:00"}}
        for _ in range(8)], "evidence_index": [{"id": value}] * 8, "checksums": ["d" * 64] * 8}
    frozen = deepcopy(view)
    packed = pack_interned(view)
    assert packed["context_encoding"] == "interned-strings-v1"
    assert unpack_view(packed) == view == frozen
    assert len(json.dumps(packed)) < len(json.dumps(pack_tabular(view)))
    assert value not in packed["strings"]


def test_decision_values_are_never_replaced_by_literal_objects():
    values = ["productcatalogservice", "recommendationservice", "readiness_failure", "resource_pressure",
              "transaction_evidence", "OBS-" + "a" * 32, "SYMOBS-" + "b" * 32]
    view = {"values": [values] * 10, "provenance": ["f" * 64] * 10}
    encoded = pack_interned(view)
    assert encoded["context_encoding"] == "interned-strings-v1"
    assert not set(values) & set(encoded["strings"])
    assert unpack_view(encoded) == view


def test_native_decision_and_request_history_never_uses_column_records():
    request = {"tool": "component_window", "service_id": "productcatalogservice",
               "metric": None, "end_at": None, "window_seconds": 60}
    decision = {"action": "read", "requests": [request] * 3}
    feedback_request = {"service_id": "productcatalogservice", "metric": None, "window_seconds": 60}
    feedback = {"call": 1, "tool": "component_window", "request": feedback_request, "outcome": "ok", "data": {}}
    view = {"turns": [{"decision": decision, "feedback": [feedback] * 3}] * 3,
            "digests": ["f" * 64] * 8}
    encoded = pack_interned(view)
    # Expand only opaque strings. Native examples must be ordinary objects in the
    # encoded prompt itself, not merely correct after our deterministic decoder.
    def inspect(node):
        if isinstance(node, list):
            for value in node:
                inspect(value)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key == "decision":
                    assert value == decision
                if key == "request":
                    assert value in (request, feedback_request)
                if key == "$record":
                    # A parent feedback record must not recursively encode its
                    # request, even when the parent itself uses a column layout.
                    table = encoded["view"]["columns"]
                    assert table[value[0]] not in (sorted(request), sorted(feedback_request))
                inspect(value)
    inspect(encoded)
    assert unpack_view(encoded) == view


@pytest.mark.parametrize("encoded", [
    {"strings": ["a", "a"], "view": {}}, {"strings": [42], "view": {}},
    {"strings": {}, "view": {}}, {"strings": ["a"], "view": {"$literal": True}},
    {"strings": ["a"], "view": {"$literal": -1}}, {"strings": ["a"], "view": {"$literal": 1}},
    {"strings": ["a"], "view": {"$literal": 0, "extra": 1}},
    {"strings": ["a"], "view": {}, "extra": 1},
])
def test_invalid_or_ambiguous_string_encoding_is_denied(encoded):
    with pytest.raises(ValueError):
        unpack_view({"context_encoding": "interned-strings-v1", **encoded})


def test_reserved_literal_cannot_be_injected_as_a_public_fact():
    with pytest.raises(ValueError):
        pack_interned({"value": {"$literal": 0}})


def test_small_nonrepeated_views_keep_existing_encoding():
    view = {"simple": True, "count": 1}
    assert pack_interned(view) == pack_tabular(view)
    assert unpack_view(pack_interned(view)) == view


def test_default_structured_context_does_not_silently_change_its_protocol():
    class Recording(FixedProbePolicy):
        def complete(self, messages, **kwargs):
            self.encoding = json.loads(messages[-1]["content"])["context_encoding"]
            return super().complete(messages, **kwargs)
    first, _ = full_epochs()
    with materialize(first) as path:
        policy = Recording()
        session = InvestigationSession(path, symptom_evidence_id=first.symptom_evidence_id, as_of=first.as_of, clock=Clock())
        report = ShadowRunner(session, first.topology, policy, screening="dependency", context_profile="structured").run()
    assert report["candidate_screened"] and policy.encoding == "column-records-v1"


def test_new_profile_encoding_has_fixed_policy_parity_and_keeps_every_read_charged():
    class Recording(FixedProbePolicy):
        def __init__(self):
            self.views, self.encodings = [], []
        def complete(self, messages, **kwargs):
            encoded = json.loads(messages[-1]["content"])
            self.encodings.append(encoded["context_encoding"])
            self.views.append(unpack_view(encoded))
            return super().complete(messages, **kwargs)
    first, second = full_epochs()
    a, b = Recording(), Recording()
    left, _, _ = run_profile(first, second, policy=a)
    right, _, _ = run_profile(first, second, policy=b)
    assert a.views == b.views and "interned-strings-v1" in a.encodings
    assert left.snapshot()["incident_budget"]["calls_used"] == right.snapshot()["incident_budget"]["calls_used"] == 16
    assert left.snapshot()["incident_budget"]["decisions_used"] == 8
