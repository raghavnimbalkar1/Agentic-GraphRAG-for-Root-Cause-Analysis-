"""Mocked recovery-window controls; no Docker, checkout transactions or provider I/O."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock
import threading

import pytest

from agent.tools import recovery_verifier as module
from agent.tools.bound_executor import execute_bound_procedure
from core.health import Observation
from tests.test_bound_execution import factory as factory, issue

CONTEXT = module._context
READ_COMPONENTS = module._read_components


def application(*, failed=None, unknown=False):
    steps = [{"step": name, "success": name != failed, "status_code": 500 if name == failed else 200,
              "marker_found": name != failed if name in {"add_cart", "read_cart", "checkout"} else None,
              "latency_s": 0.01} for name in ["home", "add_cart", "read_cart", "checkout", "cleanup_cart"]]
    if unknown:
        steps[3].update(success=False, status_code=None, marker_found=None, error="ProbeBudgetExceeded")
    return {"success": failed is None and not unknown, "steps": steps}


@pytest.fixture
def prepared(factory, monkeypatch):
    def make(relative="container/restart.sh"):
        case = factory(relative)
        grant = issue(case)
        assert execute_bound_procedure(grant, case.graph)["operation_confirmed"]
        case.grant = grant
        case.recovery_client = Mock()
        monkeypatch.setattr(module, "utc_now", case.wall)
        monkeypatch.setattr(module, "monotonic", lambda: case.clock.value)
        monkeypatch.setattr(module, "sleep", lambda seconds: setattr(case.clock, "value", case.clock.value + seconds))
        monkeypatch.setattr(module, "_docker_client", Mock(return_value=case.recovery_client))

        def readings(client, expected):
            return {name: Observation("HEALTHY", "Ready", measured_at=case.wall().isoformat(), instance_id=identifier)
                    for name, identifier in expected.items()}

        case.healthy = readings
        case.recovery_observer = Mock(side_effect=readings)
        monkeypatch.setattr(module, "_read_components", case.recovery_observer)
        case.recovery_context = Mock(side_effect=lambda *args, **kwargs: {"checked_at": case.wall().isoformat(),
            "topology_revision": case.origin.topology.revision, "collector_observe_only": True,
            "graph": {name: {"status": "HEALTHY", "age_seconds": 1} for name in case.origin.topology.edges[0]}})
        monkeypatch.setattr(module, "_context", case.recovery_context)
        case.canary = Mock(side_effect=lambda _: application())
        monkeypatch.setattr(module, "_transaction", case.canary)
        return case
    return make


def verify(case, **kwargs):
    return module.verify_bound_recovery(case.grant, case.graph, approve_transaction_probes=True, **kwargs)


def test_two_confirmations_soak_and_two_final_checkouts_are_retained(prepared):
    case = prepared()
    result = verify(case)
    assert result["outcome"] == "verified_recovery_window", result
    assert result["soak_observed_seconds"] == 120
    assert len(result["component_rounds"]) == 28
    assert len(result["transactions"]) == case.canary.call_count == 4
    assert all(row["outcome"] == "healthy" for row in result["transactions"])
    assert all(row["healthy"] for row in result["component_rounds"])
    assert not result["durable_repair_established"]
    assert case.grant.snapshot()["recovery_verified"]
    assert case.grant.snapshot()["recovery"] == result
    assert case.grant.snapshot()["state"] == "operation_confirmed"
    case.recovery_client.close.assert_called_once()
    case.graph.update_service_observations.assert_not_called()
    case.container.restart.assert_called_once()


def test_temporary_cpu_containment_is_not_reported_as_defect_repair(prepared):
    case = prepared("adservice/throttle.sh")
    result = verify(case)
    assert result["outcome"] == "verified_mitigation"
    assert result["effect_durability"] == "temporary_containment"
    assert not result["durable_repair_established"]
    case.container.update.assert_called_once_with(cpu_period=100000, cpu_quota=50000)


@pytest.mark.parametrize("approval", [False, 1, "true", None])
def test_approval_required_before_any_probe(prepared, approval):
    case = prepared()
    result = module.verify_bound_recovery(case.grant, case.graph, approve_transaction_probes=approval)
    assert result["outcome"] == "rejected"
    module._docker_client.assert_not_called()
    case.canary.assert_not_called()
    assert "recovery" not in case.grant.snapshot()


@pytest.mark.parametrize("fake", [None, {}, {"operation_confirmed": True}, "grant"])
def test_reports_cannot_authorize_verification(prepared, fake):
    case = prepared()
    assert module.verify_bound_recovery(fake, case.graph, approve_transaction_probes=True)["outcome"] == "rejected"
    module._docker_client.assert_not_called()


def test_constructor_clone_is_not_owned(prepared):
    case = prepared()
    clone = replace(case.grant)
    result = module.verify_bound_recovery(clone, case.graph, approve_transaction_probes=True)
    assert result["reason"] == "owned_execution_grant_required"
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("state", ["issued", "preparing", "refused", "not_dispatched", "effect_uncertain"])
def test_only_confirmed_operations_can_begin(prepared, state):
    case = prepared()
    case.grant._event["state"] = state
    assert verify(case)["reason"] == "confirmed_cleaned_operation_required"
    module._docker_client.assert_not_called()


def test_unknown_intent_sandbox_cleanup_cannot_be_recovery(prepared):
    case = prepared()
    case.grant._event["sandbox_cleaned"] = None
    assert verify(case)["outcome"] == "rejected"
    module._docker_client.assert_not_called()


def test_expired_or_revoked_intent_does_not_prevent_read_only_effect_verification(prepared):
    case = prepared()
    case.clock.value += 90
    case.grant.revoke()
    result = verify(case)
    assert result["outcome"] == "verified_recovery_window"
    assert case.grant.snapshot()["revoked"]
    case.container.restart.assert_called_once()


@pytest.mark.parametrize("first_outcome", ["healthy", "failed", "unknown"])
def test_verification_attempt_cannot_repeat_until_success(prepared, first_outcome):
    case = prepared()
    if first_outcome != "healthy":
        case.canary.side_effect = lambda _: application(failed="checkout" if first_outcome == "failed" else None,
                                                      unknown=first_outcome == "unknown")
    first = verify(case)
    calls = case.canary.call_count
    assert verify(case)["reason"] == "recovery_attempt_already_reserved"
    assert case.canary.call_count == calls
    assert case.grant.snapshot()["recovery"] == first


@pytest.mark.parametrize("problem", ["unknown", "missing", "extra", "replacement", "stale", "future"])
def test_incomplete_or_unreliable_component_evidence_is_inconclusive(prepared, problem):
    case = prepared()

    def reading(client, expected):
        rows = case.healthy(client, expected)
        name = next(iter(rows))
        if problem == "unknown":
            rows[name].status = "UNKNOWN"
        elif problem == "missing":
            rows.pop(name)
        elif problem == "extra":
            rows["not-registered"] = Observation("HEALTHY", "Ready")
        elif problem == "replacement":
            rows[name].instance_id = "f" * 64
        else:
            rows[name].measured_at = (case.wall() + timedelta(seconds=1 if problem == "future" else -31)).isoformat()
        return rows

    case.recovery_observer.side_effect = reading
    result = verify(case)
    assert result["outcome"] == "inconclusive"
    assert not case.grant.snapshot()["recovery_verified"]
    case.canary.assert_not_called()
    assert len(result["component_rounds"]) == 1


def test_slow_component_probe_makes_early_measurement_stale(prepared):
    case = prepared()

    def reading(client, expected):
        rows = case.healthy(client, expected)
        case.clock.value += 31
        return rows

    case.recovery_observer.side_effect = reading
    assert verify(case)["reason"] == "recovery_component_not_fresh_or_identified"


def test_initial_unhealthy_samples_are_retained_until_settle_limit(prepared):
    case = prepared()

    def unhealthy(client, expected):
        rows = case.healthy(client, expected)
        next(iter(rows.values())).status = "CONNECTION_REFUSED"
        return rows

    case.recovery_observer.side_effect = unhealthy
    result = verify(case)
    assert result["outcome"] == "not_recovered"
    assert result["reason"] == "recovery_settle_deadline_exhausted"
    assert len(result["component_rounds"]) == 13
    assert all(not row["healthy"] for row in result["component_rounds"])
    case.canary.assert_not_called()


def test_brief_initial_settling_is_not_hidden(prepared):
    case = prepared()

    def readings(client, expected):
        rows = case.healthy(client, expected)
        if case.recovery_observer.call_count == 1:
            next(iter(rows.values())).status = "CONNECTION_REFUSED"
        return rows

    case.recovery_observer.side_effect = readings
    result = verify(case)
    assert result["outcome"] == "verified_recovery_window"
    assert not result["component_rounds"][0]["healthy"]
    assert result["component_rounds"][1]["healthy"]


@pytest.mark.parametrize("at_round", [3, 10, 27])
def test_component_recurrence_blocks_success_without_repair_retry(prepared, at_round):
    case = prepared()

    def readings(client, expected):
        rows = case.healthy(client, expected)
        if case.recovery_observer.call_count == at_round:
            next(iter(rows.values())).status = "CONNECTION_REFUSED"
        return rows

    case.recovery_observer.side_effect = readings
    result = verify(case)
    assert result["outcome"] == "recurrence_observed"
    assert not case.grant.snapshot()["recovery_verified"]
    assert case.recovery_observer.call_count == at_round
    case.container.restart.assert_called_once()


@pytest.mark.parametrize("at_call,expected", [(1, "not_recovered"), (2, "not_recovered"),
                                            (3, "recurrence_observed"), (4, "recurrence_observed")])
def test_failed_checkout_blocks_component_only_recovery(prepared, at_call, expected):
    case = prepared()
    case.canary.side_effect = lambda _: application(failed="checkout" if case.canary.call_count == at_call else None)
    result = verify(case)
    assert result["outcome"] == expected
    assert result["reason"] == "recovery_transaction_failed"
    assert len(result["transactions"]) == at_call
    assert not case.grant.snapshot()["recovery_verified"]


@pytest.mark.parametrize("problem", ["unknown", "cleanup", "missing_marker", "extra_field", "empty", "nan", "wrong_order"])
def test_unknown_or_malformed_transaction_never_verifies(prepared, problem):
    case = prepared()
    raw = application(unknown=problem == "unknown", failed="cleanup_cart" if problem == "cleanup" else None)
    if problem == "missing_marker":
        raw["steps"][3]["marker_found"] = None
    elif problem == "extra_field":
        raw["raw_body"] = "PRIVATE-BODY"
    elif problem == "empty":
        raw["steps"] = []
    elif problem == "nan":
        raw["steps"][0]["latency_s"] = float("nan")
    elif problem == "wrong_order":
        raw["steps"].reverse()
    case.canary.side_effect = lambda _: deepcopy(raw)
    result = verify(case)
    assert result["outcome"] == "inconclusive"
    assert "PRIVATE" not in str(result)
    assert len(result["transactions"]) == 1
    case.canary.assert_called_once()


@pytest.mark.parametrize("when", ["first", "final"])
def test_context_or_graph_failure_is_not_recovery(prepared, when):
    case = prepared()
    normal = case.recovery_context.side_effect

    def context(*args, **kwargs):
        if when == "first" or kwargs.get("final"):
            raise module.RecoveryStopped("recovery_graph_not_fresh_healthy")
        return normal(*args, **kwargs)

    case.recovery_context.side_effect = context
    result = verify(case)
    assert result["outcome"] == "inconclusive"
    assert not case.grant.snapshot()["recovery_verified"]
    assert case.canary.call_count == (0 if when == "first" else 4)


def test_context_latency_is_bounded(prepared):
    case = prepared()
    normal = case.recovery_context.side_effect

    def slow(*args, **kwargs):
        result = normal(*args, **kwargs)
        case.clock.value += 5
        return result

    case.recovery_context.side_effect = slow
    assert verify(case)["reason"] == "recovery_context_expired"


@pytest.mark.parametrize("location", ["components", "transaction"])
def test_interruption_is_retained_and_attempt_consumed(prepared, location):
    case = prepared()
    (case.recovery_observer if location == "components" else case.canary).side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        verify(case)
    receipt = case.grant.snapshot()["recovery"]
    assert receipt["outcome"] == "inconclusive" and receipt["reason"] == "recovery_verification_interrupted"
    assert verify(case)["reason"] == "recovery_attempt_already_reserved"
    case.recovery_client.close.assert_called_once()


def test_cooperative_deadline_and_failure_retention(prepared):
    case = prepared()

    def slow(_):
        case.clock.value += 301
        return application()

    case.canary.side_effect = slow
    result = verify(case)
    assert result["reason"] == "recovery_deadline_exhausted"
    assert result["outcome"] == "inconclusive"
    assert len(result["transactions"]) == 1
    assert result["transactions"][0]["outcome"] == "unknown"


def test_backward_wall_clock_cannot_verify(prepared, monkeypatch):
    case = prepared()
    monkeypatch.setattr(module, "utc_now", lambda: case.wall() - timedelta(seconds=1 if case.canary.call_count else 0))
    assert verify(case)["reason"] == "recovery_clock_discontinuity"


def test_client_close_uncertainty_downgrades_success(prepared):
    case = prepared()
    case.recovery_client.close.side_effect = RuntimeError("PRIVATE")
    result = verify(case)
    assert result["outcome"] == "inconclusive" and result["reason"] == "recovery_client_close_unconfirmed"
    assert not case.grant.snapshot()["recovery_verified"]


@pytest.mark.parametrize("data", [{"soak_seconds": 119}, {"total_seconds": 200}, {"max_rounds": 4},
                                 {"poll_seconds": True}, {"unknown": 1}])
def test_infeasible_or_weakened_limits_are_rejected(data):
    with pytest.raises(ValueError):
        module.RecoveryLimits(**data)


def test_untyped_limit_dictionary_cannot_dispatch(prepared):
    case = prepared()
    assert verify(case, limits={})["reason"] == "typed_recovery_limits_required"
    module._docker_client.assert_not_called()


def test_concurrent_attempt_is_rejected_before_probes(prepared):
    case = prepared()
    entered, release = threading.Event(), threading.Event()
    normal = case.recovery_observer.side_effect

    def blocked(*args):
        if case.recovery_observer.call_count == 1:
            entered.set()
            assert release.wait(5)
        return normal(*args)

    case.recovery_observer.side_effect = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(verify, case)
        assert entered.wait(5)
        try:
            assert verify(case)["reason"] == "recovery_attempt_already_reserved"
        finally:
            release.set()
        assert first.result()["outcome"] == "verified_recovery_window"
    assert case.canary.call_count == 4


def test_nonfinite_component_values_do_not_poison_receipt(prepared):
    case = prepared()

    def bad(*args):
        rows = case.healthy(*args)
        next(iter(rows.values())).measurements = {"seconds": float("nan")}
        return rows

    case.recovery_observer.side_effect = bad
    result = verify(case)
    assert result["outcome"] == "inconclusive"
    assert case.grant.snapshot()["recovery"] == result


def test_slow_initial_confirmations_do_not_escape_settle_deadline(prepared):
    case = prepared()

    def slow(_):
        case.clock.value += 29
        return application()

    case.canary.side_effect = slow
    result = verify(case)
    assert result["reason"] == "recovery_settle_deadline_exhausted"
    assert result["outcome"] == "not_recovered"
    assert len(result["transactions"]) == 2


@pytest.mark.parametrize("problem", [None, "wrong_name", "wrong_id", "ownership", "missing"])
def test_runtime_adapter_rechecks_exact_owned_instances(prepared, monkeypatch, problem):
    case = prepared()
    expected = {case.target: case.container.id}
    monkeypatch.setattr("core.health.observe_many", Mock(return_value={case.target: Observation("HEALTHY", "Ready")}))
    case.recovery_client.containers.get.return_value = case.container
    if problem == "wrong_name":
        case.container.name = "other-service"
    elif problem == "wrong_id":
        case.container.id = "f" * 64
    elif problem == "ownership":
        case.container.attrs["Config"]["Labels"] = {}
    elif problem == "missing":
        case.recovery_client.containers.get.side_effect = RuntimeError("Missing instance")
    if problem:
        with pytest.raises((module.RecoveryStopped, RuntimeError)):
            READ_COMPONENTS(case.recovery_client, expected)
    else:
        assert READ_COMPONENTS(case.recovery_client, expected)[case.target].healthy


@pytest.mark.parametrize("problem", [None, "delivery", "stale_collector", "topology", "catalog", "stale_graph",
                                     "unknown_graph", "absent_graph", "unsynced", "predates_effect"])
def test_current_graph_collector_catalog_adapter(prepared, monkeypatch, problem):
    case = prepared()
    from core.registry import DEFAULT_DEPLOYMENT as registry

    status = {"schema_version": 2, "state": "observe_only", "heartbeat_fresh": True,
              "sensing_ready": True, "delivery_enabled": False, "graph_synced": True,
              "evidence_store": {"enabled": True, "state": "ready"}}
    snapshot = {name: {"status": "HEALTHY", "age_seconds": 1} for name in registry.by_name}
    case.clock.value += 10
    if problem == "delivery":
        status["delivery_enabled"] = True
    elif problem == "stale_collector":
        status["heartbeat_fresh"] = False
    elif problem == "topology":
        case.graph.get_dependency_topology.return_value = {"services": list(registry.by_name),
                                                          "edges": [["frontend", "adservice"]]}
    elif problem == "catalog":
        case.graph._run.return_value = []
    elif problem == "stale_graph":
        snapshot[case.target]["age_seconds"] = 31
    elif problem == "predates_effect":
        snapshot[case.target]["age_seconds"] = 11
    elif problem == "unknown_graph":
        snapshot[case.target]["status"] = "UNKNOWN"
    elif problem == "absent_graph":
        del snapshot[case.target]
    elif problem == "unsynced":
        status["graph_synced"] = False
    monkeypatch.setattr("core.telemetry.collector_status", Mock(return_value=status))
    case.graph.get_service_snapshot.return_value = snapshot
    if problem:
        with pytest.raises(ValueError):
            CONTEXT(case.grant, case.graph, {case.target}, final=True)
    else:
        result = CONTEXT(case.grant, case.graph, {case.target}, final=True)
        assert result["graph"] == {case.target: {"status": "HEALTHY", "age_seconds": 1}}
    case.graph.update_service_observations.assert_not_called()
