"""
Multi-fault orchestration logic (agent/multi_root.py) — the outer loop that
dispatches the single-root agent at each independent root. Mocked GraphClient +
HTTP, so no Neo4j / agent / LLM needed.
"""

from __future__ import annotations

import agent.multi_root as mr
import pytest


@pytest.fixture(autouse=True)
def manual_mode(monkeypatch):
    monkeypatch.setattr(mr, "collector_ready", lambda: False)


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class _FakeGC:
    """Stubs the two GraphClient methods the orchestrator uses."""
    def __init__(self, roots, remaining_after):
        self._roots = roots
        self._remaining = remaining_after
        self.dispatched = []
        self.snapshots = 0

    def get_independent_roots(self):
        return self._roots

    def count_all_unhealthy(self):
        return self._remaining

    def get_service_snapshot(self):
        from core.health import SERVICES
        self.snapshots += 1
        snapshot = {name: {"status": "HEALTHY", "age_seconds": 1} for name in SERVICES}
        roots = self._roots if self.snapshots <= len(self._roots) + 1 else self._roots[:self._remaining]
        for root in roots:
            snapshot[root["name"]] = {"status": root["status"], "age_seconds": 1}
        return snapshot


def _patch_http(monkeypatch, responses_by_service):
    def fake_post(url, json, timeout):  # noqa: A002 - mirrors httpx signature
        svc = json["service"]
        return _FakeResp({**responses_by_service[svc], "alert_id": json["alert_id"]})
    monkeypatch.setattr(mr.httpx, "post", fake_post)


def test_dispatches_agent_at_each_independent_root(monkeypatch):
    gc = _FakeGC(
        roots=[{"name": "redis-cart", "status": "STALE_DATA"},
                {"name": "adservice", "status": "HIGH_CPU"}],
        remaining_after=0,
    )
    seen = []
    def fake_post(url, json, timeout):  # noqa: A002
        seen.append(json["service"])
        return _FakeResp({"status": "RESOLVED",
                          "alert_id": json["alert_id"],
                          "skills_executed": [f"{json['service']}_SOP"]})
    monkeypatch.setattr(mr.httpx, "post", fake_post)

    report = mr.resolve_all_roots(gc, agent_url="http://x/alert")

    assert seen == ["redis-cart", "adservice"]        # one dispatch per root
    assert report["roots_detected"] == 2
    assert report["resolved_count"] == 2
    assert report["all_healthy"] is True
    assert report["services_still_unhealthy"] == 0


def test_partial_resolution_is_reported_honestly(monkeypatch):
    gc = _FakeGC(
        roots=[{"name": "redis-cart", "status": "STALE_DATA"},
                {"name": "paymentservice", "status": "CONNECTION_REFUSED"}],
        remaining_after=1,   # one root did not clear
    )
    _patch_http(monkeypatch, {
        "redis-cart": {"status": "RESOLVED", "skills_executed": ["Redis_Flush_SOP"]},
        "paymentservice": {"status": "ESCALATED", "skills_executed": []},
    })

    report = mr.resolve_all_roots(gc, agent_url="http://x/alert")

    assert report["resolved_count"] == 1
    assert report["all_healthy"] is False
    assert report["services_still_unhealthy"] == 1


def test_uncertain_agent_error_stops_the_batch(monkeypatch):
    gc = _FakeGC(
        roots=[{"name": "adservice", "status": "HIGH_CPU"},
                {"name": "redis-cart", "status": "STALE_DATA"}],
        remaining_after=1,
    )
    def fake_post(url, json, timeout):  # noqa: A002
        if json["service"] == "adservice":
            raise RuntimeError("agent unreachable")
        return _FakeResp({"status": "RESOLVED", "skills_executed": ["X"]})
    monkeypatch.setattr(mr.httpx, "post", fake_post)

    report = mr.resolve_all_roots(gc, agent_url="http://x/alert")

    assert [i["root"] for i in report["incidents"]] == ["adservice"]
    assert report["incidents"][0]["resolution"] == "ERROR"
    assert not report["all_healthy"]


def test_no_roots_yields_empty_healthy_report(monkeypatch):
    gc = _FakeGC(roots=[], remaining_after=0)
    monkeypatch.setattr(mr.httpx, "post",
                        lambda *a, **k: _FakeResp({"status": "RESOLVED"}))
    report = mr.resolve_all_roots(gc, agent_url="http://x/alert")
    assert report["roots_detected"] == 0
    assert report["resolved_count"] == 0
    assert report["all_healthy"] is True


def test_active_collector_prevents_manual_duplicate_dispatch(monkeypatch):
    monkeypatch.setattr(mr, "collector_ready", lambda: True)
    with pytest.raises(ValueError, match="owns incident dispatch"):
        mr.resolve_all_roots(_FakeGC([], 0))


def test_unknown_health_is_not_an_empty_healthy_batch(monkeypatch):
    gc = _FakeGC([], 0)
    monkeypatch.setattr(gc, "get_service_snapshot", lambda: {})
    with pytest.raises(ValueError, match="fresh health evidence"):
        mr.resolve_all_roots(gc)


def test_mismatched_report_id_stops_dispatch(monkeypatch):
    gc = _FakeGC([{"name": "redis-cart", "status": "STALE_DATA"},
                  {"name": "adservice", "status": "HIGH_CPU"}], 1)
    monkeypatch.setattr(mr.httpx, "post", lambda *a, **k: _FakeResp({"status": "RESOLVED", "alert_id": "wrong"}))
    report = mr.resolve_all_roots(gc)
    assert len(report["incidents"]) == 1
    assert report["incidents"][0]["resolution"] == "ERROR"
