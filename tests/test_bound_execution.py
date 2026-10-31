"""Mocked controlled-action software checks. Never touches Docker, Groq or a live application."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import io
import json
from pathlib import Path
import pickle
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from docker import DockerClient
from docker.errors import NotFound
import pytest

from agent import procedure_execution as execution
from agent.procedure_execution import ExecutionDenied
from agent.tools import bound_executor as module
from core.health import Observation
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.procedure_contracts import PreflightPermissions, operation_policy
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import FrozenInputs, implementation_manifest
from eval.shadow_capture import declared_topology
from tests.test_procedure_binding import graph, inputs, prepare, select, setup, fresh_target
from tests.test_procedure_contracts import definition

EXECUTION_CONTEXT = module._execution_context


class Stream:
    def __init__(self, data):
        self.data = data
        self.close = Mock()

    def __iter__(self):
        yield self.data


@pytest.fixture
def factory(monkeypatch):
    def make(relative="container/restart.sh", *, synthetic=False, refreshed=False, **options):
        policy = operation_policy(relative)
        target, status = options.get("binding", policy.bindings[0])
        measurement = {"HIGH_CPU": {"cpu_percent": 95}, "DISK_PRESSURE": {"bytes": 110000000},
                       "OOM_KILLED": {"maxmemory": 1024}, "POOL_EXHAUSTION": {"clients": 80}}.get(status, {})
        origin = inputs(statuses={target: status}, measurements={target: measurement})
        data = origin.model_dump(mode="json")
        data["source_kind"] = "synthetic" if synthetic else "retained-observation"
        edges = [["checkoutservice", target]] if target != "checkoutservice" else [["checkoutservice", "paymentservice"]]
        export = {"services": list(REGISTRY.by_name), "edges": edges}
        data["topology"] = declared_topology(export, captured_at=origin.as_of).model_dump(mode="json")
        origin = FrozenInputs.model_validate(data)
        run, clock, origin, revision = setup(origin)
        start_cutoff = origin.as_of
        wall = lambda: start_cutoff + timedelta(seconds=clock.value - 100)
        monkeypatch.setattr(execution, "utc_now", wall)
        monkeypatch.setattr(module, "utc_now", wall)
        client_graph = graph(definition(relative, name="Reviewed_SOP", applies_to=[target], trigger_condition=status))
        client_graph.get_dependency_topology.return_value = export
        permissions = PreflightPermissions(**{policy.permission: True})
        if refreshed:
            clock.value += 60
            fresh = inputs(index=1, previous=origin, statuses={target: status},
                           measurements={target: measurement}, offset=60)
            data = fresh.model_dump(mode="json")
            data["source_kind"] = "retained-observation"
            data["topology"] = declared_topology(export, captured_at=fresh.as_of).model_dump(mode="json")
            fresh = FrozenInputs.model_validate(data)
            assert run.recheck_candidate(fresh, candidate_revision=revision)["outcome"] == "consistent"
            origin = fresh
        prepared = prepare(run, origin, revision, client=client_graph, permissions=permissions)
        selected = select(run, origin, prepared, client=client_graph, permissions=permissions,
                          item=fresh_target(origin, target=target))
        assert selected["outcome"] == "eligible"
        client, container, box = Mock(), Mock(), Mock()
        item = fresh_target(origin, target=target)
        container.id, container.name = item.instance_id, target
        container.attrs = {"Config": {"Labels": {"rca.managed": "online-boutique"}},
                           "HostConfig": {"CpuQuota": 10000}, "NetworkSettings": {"Networks": {"boutique-sim": {}}}}
        container.exec_run.return_value.exit_code = 0
        order = []
        container.restart.side_effect = lambda **kw: order.append("restart")
        container.update.side_effect = lambda **kw: order.append("update")
        container.exec_run.side_effect = lambda *a, **kw: (order.append("exec"), SimpleNamespace(exit_code=0))[1]
        box.wait.return_value = {"StatusCode": 0}
        stream = Stream(json.dumps({"operation": policy.operation}).encode())
        box.logs.return_value = stream
        box.remove.side_effect = lambda **kw: order.append("sandbox_removed")
        client.containers.create.return_value = box
        client.images.get.return_value.id = "sha256:" + "a" * 64

        def get(name):
            if name in {target, container.id}:
                return container
            raise NotFound("Not present")

        client.containers.get.side_effect = get
        monkeypatch.setattr(module, "_docker_client", Mock(return_value=client))

        def readings(_client, services):
            order.append("probe")
            records = {row.payload["service_id"]: row.payload for row in origin.records if row.kind == "component"}
            return {name: Observation(records[name]["status"], "Probe", measured_at=wall().isoformat(),
                measurements=records[name]["measurements"], instance_id=records[name]["instance_id"]) for name in services}

        observer = Mock(side_effect=readings)
        monkeypatch.setattr(module, "_observe_scope", observer)
        symptom = next(row.payload for row in origin.records if row.kind == "symptom")
        context_reader = Mock(side_effect=lambda *_: {"checked_at": wall().isoformat(), "symptom": deepcopy(symptom),
                                                     "topology_revision": origin.topology.revision})
        monkeypatch.setattr(module, "_execution_context", context_reader)
        from agent.tools import control_gateway
        monkeypatch.setattr(control_gateway, "ThreadingHTTPServer", Mock(side_effect=AssertionError("No HTTP gateway in bound path")))
        return SimpleNamespace(run=run, clock=clock, origin=origin, revision=revision, graph=client_graph,
            permissions=permissions, prepared=prepared, selected=selected, client=client, container=container,
            box=box, stream=stream, observer=observer, readings=readings, order=order, wall=wall,
            policy=policy, target=target, context_reader=context_reader,
            symptom=symptom)
    return make


def issue(case, **overrides):
    return case.run.authorize_execution(0, case.graph, **{ "approve_execution": True, "permissions": case.permissions, **overrides})


def assert_no_effect(case):
    case.container.restart.assert_not_called()
    case.container.update.assert_not_called()
    case.container.exec_run.assert_not_called()


def test_one_opaque_grant_runs_networkless_sop_then_rechecks_before_one_fixed_operation(factory):
    case = factory(binding=("paymentservice", "CONNECTION_REFUSED"))
    grant = issue(case)
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "operation_confirmed" and result["operation_confirmed"]
    assert result["execution_authorized"] and result["action_dispatched"] and not result["recovery_verified"]
    assert result["sandbox_cleaned"] is True and result["reason"] == "verification_required"
    assert case.order == ["sandbox_removed", "probe", "restart"]
    case.container.restart.assert_called_once_with(timeout=5)
    case.box.remove.assert_called_once_with(force=True)
    case.stream.close.assert_called_once()
    case.client.close.assert_called_once()
    args = case.client.containers.create.call_args.kwargs
    assert args["network_mode"] == "none" and "network" not in args and "extra_hosts" not in args
    assert args["image"] == result["image_id"] == "sha256:" + "a" * 64
    assert args["environment"] == {"RCA_CONTROL_MODE": "intent-only"}
    assert args["user"] == "1000:1000" and args["cap_drop"] == ["ALL"]
    assert args["read_only"] and not args["privileged"] and args["security_opt"] == ["no-new-privileges"]
    assert args["pids_limit"] == 32 and args["nano_cpus"] == 250000000
    assert len(args["volumes"]) == 2 and all("docker.sock" not in name for name in args["volumes"])
    assert all(not Path(name).exists() for name in args["volumes"])
    assert len(result["preflight"]["evidence"]) == 2
    assert case.run.snapshot()["procedure_execution"] == {key: value for key, value in result.items()
                                                         if key not in {"request_outcome", "request_action_dispatched"}}


@pytest.mark.parametrize("relative", ["container/restart.sh", "redis/restart.sh", "email/disk_cleanup.sh",
                                     "adservice/throttle.sh", "frontend/restore_cpu.sh"])
def test_all_five_controlled_implementations_dispatch_only_reviewed_parameters(factory, relative):
    case = factory(relative)
    result = module.execute_bound_procedure(issue(case), case.graph)
    assert result["operation_confirmed"], result
    if case.policy.operation in {"restart", "restart_redis"}:
        case.container.restart.assert_called_once_with(timeout=5)
    elif case.policy.operation == "cleanup_disk":
        case.container.exec_run.assert_called_once_with(["rm", "-f", "/tmp/diskfill.bin"])
    elif case.policy.operation == "throttle_cpu":
        case.container.update.assert_called_once_with(cpu_period=100000, cpu_quota=50000)
    else:
        case.container.update.assert_called_once_with(cpu_quota=-1)


@pytest.mark.parametrize("approval", [False, 1, "true", None])
def test_only_explicit_true_operator_approval_can_issue(factory, approval):
    case = factory()
    with pytest.raises(ExecutionDenied, match="explicit_operator_execution_approval_required"):
        issue(case, approve_execution=approval)
    module._docker_client.assert_not_called()


def test_synthetic_session_cannot_mint_an_execution_grant(factory):
    case = factory(synthetic=True)
    with pytest.raises(ExecutionDenied, match="synthetic_execution_forbidden"):
        issue(case)
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("index", [True, -1, 1, "0"])
def test_selection_must_be_owned_current_and_exactly_indexed(factory, index):
    case = factory()
    with pytest.raises(ExecutionDenied, match="current_owned_selection_required"):
        case.run.authorize_execution(index, case.graph, approve_execution=True, permissions=case.permissions)


def test_effect_permission_is_required_again_at_operator_authorization(factory):
    case = factory()
    with pytest.raises(ExecutionDenied, match="operator_permission_required"):
        issue(case, permissions=PreflightPermissions())


@pytest.mark.parametrize("value", [{}, {"execution_authorized": True}, "opaque", None])
def test_json_or_eligible_plan_is_not_an_execution_capability(factory, value):
    case = factory()
    result = module.execute_bound_procedure(value, case.graph)
    assert result["reason"] == "opaque_execution_grant_required" and not result["action_dispatched"]
    module._docker_client.assert_not_called()


def test_real_eligible_plan_dictionary_still_cannot_dispatch(factory):
    case = factory()
    assert module.execute_bound_procedure(case.selected["plan"], case.graph)["state"] == "refused"
    module._docker_client.assert_not_called()


def test_grant_cannot_be_copied_pickled_serialized_or_retargeted(factory):
    case = factory()
    grant = issue(case)
    for operation in (copy, deepcopy, pickle.dumps, json.dumps):
        with pytest.raises(TypeError):
            operation(grant)
    with pytest.raises(FrozenInstanceError):
        grant.plan = grant.plan.model_copy(update={"target": "redis-cart"})
    assert "paymentservice" not in repr(grant)


def test_second_issuance_is_denied_even_before_dispatch(factory):
    case = factory()
    issue(case)
    with pytest.raises(ExecutionDenied, match="incident_execution_attempt_already_reserved"):
        issue(case)


def test_fresh_consistency_receipt_can_support_a_grant_without_reviving_expired_proposal(factory):
    case = factory(refreshed=True)
    assert case.run.snapshot()["current_candidate"] is None
    assert case.prepared["offer"]["evidence_basis"] == "consistency_check"
    grant = issue(case)
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "operation_confirmed"
    assert case.run.snapshot()["candidates"][0]["state"] == "expired"
    assert not result["recovery_verified"]


def test_authorization_does_not_issue_a_grant_after_slow_catalog_capture(factory):
    case = factory()
    rows = case.graph._run.return_value

    def slow(*args, **kwargs):
        case.clock.value += 30
        return rows

    case.graph._run.side_effect = slow
    with pytest.raises(ExecutionDenied, match="execution_evidence_expired_or_changed"):
        issue(case)
    assert case.run.snapshot()["procedure_execution"] is None


def test_authorization_hides_raw_catalog_connection_failures(factory):
    case = factory()
    case.graph._run.side_effect = OSError("PRIVATE-CONNECTION")
    with pytest.raises(ExecutionDenied, match="execution_catalog_unavailable_or_rejected") as error:
        issue(case)
    assert "PRIVATE" not in str(error.value)


def test_constructor_clone_is_not_the_issuers_owned_grant(factory):
    case = factory()
    grant = issue(case)
    clone = replace(grant)
    result = module.execute_bound_procedure(clone, case.graph)
    assert result["request_rejected"] == "grant_not_startable"
    module._docker_client.assert_not_called()
    assert grant.snapshot()["state"] == "issued"


def test_network_redis_script_is_refused_even_with_a_supported_diagnosis_and_effect_permission(factory):
    case = factory("redis/pool_reset.sh")
    with pytest.raises(ExecutionDenied, match="network_procedure_execution_not_supported"):
        issue(case)
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("mode", ["expiry", "revoke", "refresh_failure", "new_epoch", "new_preparation", "provider_stop"])
def test_stale_or_revoked_grant_is_rejected_before_opening_docker(factory, mode):
    case = factory()
    grant = issue(case)
    if mode == "expiry":
        case.clock.value += 30
    elif mode == "revoke":
        case.run.revoke_execution()
    elif mode == "refresh_failure":
        case.run.invalidate_current()
    elif mode == "new_epoch":
        data = inputs(index=1, previous=case.origin).model_dump(mode="json")
        data["source_kind"] = "retained-observation"
        data["topology"]["edges"] = list(case.origin.topology.edges)
        case.run.run_epoch(FrozenInputs.model_validate(data), FixedProbePolicy())
    elif mode == "new_preparation":
        prepare(case.run, case.origin, case.revision, client=case.graph, permissions=case.permissions)
    else:
        case.run.budget.halt("usage_unknown")
    result = module.execute_bound_procedure(grant, case.graph)
    assert not result["action_dispatched"] and result["request_rejected"] == "grant_not_startable"
    module._docker_client.assert_not_called()
    assert_no_effect(case)


def test_concurrent_or_repeated_dispatch_cannot_repeat_the_target_effect(factory):
    case = factory()
    grant = issue(case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: module.execute_bound_procedure(grant, case.graph), range(2)))
    assert sum(row.get("request_rejected") is None for row in results) == 1
    assert sum(row["request_action_dispatched"] for row in results) == 1
    case.container.restart.assert_called_once()
    case.client.containers.create.assert_called_once()
    duplicate = module.execute_bound_procedure(grant, case.graph)
    assert duplicate["request_rejected"] == "grant_not_startable" and not duplicate["request_action_dispatched"]
    assert case.container.restart.call_count == 1


@pytest.mark.parametrize("stage", ["before_sandbox", "before_effect"])
def test_catalog_drift_never_reaches_mutation(factory, stage):
    case = factory()
    grant = issue(case)
    original = deepcopy(case.graph._run.return_value)
    changed = deepcopy(original)
    changed[0]["skill"]["description"] = "Changed after grant"
    case.graph._run.side_effect = [changed] if stage == "before_sandbox" else [original, changed]
    result = module.execute_bound_procedure(grant, case.graph)
    assert not result["action_dispatched"] and result["state"] == "not_dispatched"
    assert_no_effect(case)


@pytest.mark.parametrize("raw", [b'{}', b'{"operation":"redis_flush"}', b'{"operation":"restart","target":"other"}',
                                  b'{"operation":"restart","operation":"restart"}', b'[]', b'not json', b'x' * 513])
def test_sandbox_cannot_supply_another_operation_parameters_or_oversized_output(factory, raw):
    case = factory()
    grant = issue(case)
    case.stream.data = raw
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "not_dispatched" and not result["action_dispatched"]
    case.box.remove.assert_called_once_with(force=True)
    case.observer.assert_not_called()
    assert_no_effect(case)


@pytest.mark.parametrize("stage", ["start", "wait", "logs", "remove", "close_stream"])
def test_sandbox_failure_or_uncertain_cleanup_blocks_target_operation(factory, stage):
    case = factory()
    grant = issue(case)
    method = case.stream.close if stage == "close_stream" else getattr(case.box, stage)
    method.side_effect = RuntimeError("PRIVATE-RAW-DIAGNOSTIC")
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "not_dispatched" and not result["action_dispatched"]
    assert "PRIVATE" not in json.dumps(result)
    case.box.remove.assert_called_once_with(force=True)
    assert_no_effect(case)


@pytest.mark.parametrize("change", ["recovered", "unknown", "competing", "instance", "old", "missing"])
def test_fresh_full_scope_preflight_denies_changed_or_incomplete_state(factory, change):
    case = factory()
    grant = issue(case)

    def observe(client, services):
        rows = case.readings(client, services)
        if change == "recovered":
            rows[case.target].status = "HEALTHY"
        elif change == "unknown":
            rows["checkoutservice"].status = "UNKNOWN"
        elif change == "competing":
            rows["checkoutservice"].status = "DEGRADED"
        elif change == "instance":
            rows["checkoutservice"].instance_id = "f" * 64
        elif change == "old":
            rows[case.target].measured_at = (case.origin.as_of - timedelta(seconds=1)).isoformat()
        else:
            rows.pop("checkoutservice")
        return rows

    case.observer.side_effect = observe
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "not_dispatched" and not result["action_dispatched"]
    assert_no_effect(case)


@pytest.mark.parametrize("change", ["owner", "name", "quota", "replacement"])
def test_runtime_owner_identity_and_numeric_precondition_are_not_replaced_by_old_selection(factory, change):
    case = factory("frontend/restore_cpu.sh") if change == "quota" else factory()
    grant = issue(case)
    if change == "owner":
        case.container.attrs["Config"]["Labels"] = {}
    elif change == "name":
        case.container.name = "unrelated"
    elif change == "quota":
        case.container.attrs["HostConfig"]["CpuQuota"] = 0
    else:
        case.container.id = "b" * 64
        case.client.containers.get.side_effect = None
        case.client.containers.get.return_value = case.container
    result = module.execute_bound_procedure(grant, case.graph)
    assert not result["action_dispatched"]
    assert_no_effect(case)


def test_permission_revocation_during_sandbox_is_honored_before_fresh_probes(factory):
    case = factory()
    grant = issue(case)
    case.box.start.side_effect = case.run.revoke_execution
    result = module.execute_bound_procedure(grant, case.graph)
    assert not result["action_dispatched"] and result["revoked"]
    case.box.remove.assert_called_once()
    case.observer.assert_not_called()
    assert_no_effect(case)


def test_probe_or_catalog_latency_cannot_renew_expired_grant(factory):
    case = factory()
    grant = issue(case)

    def slow(client, services):
        rows = case.readings(client, services)
        case.clock.value += 30
        return rows

    case.observer.side_effect = slow
    result = module.execute_bound_procedure(grant, case.graph)
    assert not result["action_dispatched"]
    assert_no_effect(case)


def test_local_epoch_refresh_cannot_interleave_between_final_validation_and_fixed_operation(factory):
    case = factory()
    grant = issue(case)
    executing, release, refreshing = threading.Event(), threading.Event(), threading.Event()
    fresh = inputs(index=1, previous=case.origin).model_dump(mode="json")
    fresh["source_kind"] = "retained-observation"
    fresh["topology"] = declared_topology(case.graph.get_dependency_topology.return_value,
        captured_at=case.origin.as_of + timedelta(seconds=5)).model_dump(mode="json")
    fresh = FrozenInputs.model_validate(fresh)

    def effect(**kwargs):
        executing.set()
        assert release.wait(3)

    def refresh():
        refreshing.set()
        return case.run.run_epoch(fresh, FixedProbePolicy())

    case.container.restart.side_effect = effect
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(module.execute_bound_procedure, grant, case.graph)
        second = None
        try:
            assert executing.wait(3)
            second = pool.submit(refresh)
            assert refreshing.wait(3) and not second.done()
            assert len(case.run._events) == 1
        finally:
            release.set()
        assert first.result()["operation_confirmed"]
        assert second.result()["outcome"] == "investigated"
    case.container.restart.assert_called_once()
    assert grant.snapshot()["operation_confirmed"]


@pytest.mark.parametrize("stage", ["start", "wait", "logs"])
def test_sandbox_interruption_still_attempts_cleanup_and_never_mutates_target(factory, stage):
    case = factory()
    grant = issue(case)
    getattr(case.box, stage).side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        module.execute_bound_procedure(grant, case.graph)
    result = grant.snapshot()
    assert result["state"] == "not_dispatched" and result["reason"] == "execution_interrupted"
    assert result["sandbox_cleaned"]
    case.box.remove.assert_called_once_with(force=True)
    assert_no_effect(case)


def test_catalog_latency_counts_against_runtime_inspection_independently(factory):
    case = factory()
    grant = issue(case)
    rows = case.graph._run.return_value
    calls = []

    def read(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            case.clock.value += 5
        return rows

    case.graph._run.side_effect = read
    result = module.execute_bound_procedure(grant, case.graph)
    assert not result["action_dispatched"]
    assert result["preflight"]["reason"] == "identity_or_ownership_changed"
    assert_no_effect(case)


@pytest.mark.parametrize("matching", [True, False])
def test_lost_create_response_cleans_only_the_matching_owned_attempt(factory, matching):
    case = factory()
    grant = issue(case)

    def lost(**kwargs):
        case.box.attrs = {"Config": {"Labels": kwargs["labels"] if matching else {"rca.attempt": "unrelated"}}}
        case.client.containers.get.side_effect = lambda _: case.box
        raise TimeoutError("PRIVATE-LOST-RESPONSE")

    case.client.containers.create.side_effect = lost
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "not_dispatched" and not result["action_dispatched"]
    assert result["sandbox_cleaned"] is matching
    if matching:
        case.box.remove.assert_called_once_with(force=True)
    else:
        case.box.remove.assert_not_called()
    assert "PRIVATE" not in json.dumps(result)
    assert_no_effect(case)


def test_explicit_container_constraints_serialize_through_installed_docker_sdk(factory, monkeypatch):
    case = factory()
    module.execute_bound_procedure(issue(case), case.graph)
    args = case.client.containers.create.call_args.kwargs
    with closing(DockerClient(version="1.45")) as sdk:
        response = Mock()
        response.json.return_value = {"Id": "bound-container", "Warnings": []}
        post = Mock(return_value=response)
        monkeypatch.setattr(sdk.api, "_post_json", post)
        monkeypatch.setattr(sdk.api, "inspect_container", Mock(return_value={"Id": "bound-container"}))
        sdk.containers.create(**args)
    data = post.call_args.kwargs["data"]
    assert data["Cmd"] == ["bash", "/script/sop.sh"] and data["Entrypoint"] == []
    assert data["Env"] == ["RCA_CONTROL_MODE=intent-only"]
    assert data["Image"] == "sha256:" + "a" * 64 and data["User"] == "1000:1000"
    config = data["HostConfig"]
    assert config["NetworkMode"] == "none" and config["ReadonlyRootfs"]
    assert config["Memory"] == config["MemorySwap"] == 134217728
    assert config["NanoCpus"] == 250000000 and config["PidsLimit"] == 32
    assert config["CapDrop"] == ["ALL"] and config.get("Privileged", False) is False
    assert data["Healthcheck"]["Test"] == ["NONE"]


@pytest.mark.parametrize("kind", ["healthy", "unknown", "ambiguous", "changed_steps", "changed_topology", "delivery", "missing", "unavailable"])
def test_actual_context_reader_requires_latest_matching_failure_unchanged_graph_and_observe_only(factory, monkeypatch, kind):
    case = factory()
    grant = issue(case)
    from core import telemetry
    from core import diagnostics

    state = {"schema_version": 2, "delivery_enabled": kind == "delivery", "heartbeat_fresh": True,
             "state": "observe_only", "evidence_store": {"enabled": True, "state": "ready"}}
    monkeypatch.setattr(telemetry, "collector_status", lambda _: state)
    rows = [deepcopy(case.symptom)]
    if kind in {"healthy", "unknown"}:
        rows[0]["outcome"] = kind
        rows[0]["quality"] = "unknown" if kind == "unknown" else "observed"
    elif kind == "ambiguous":
        peer = deepcopy(rows[0])
        peer["evidence_id"] = "SYMOBS-" + "f" * 32
        rows.append(peer)
    elif kind == "changed_steps":
        rows[0]["steps"][0]["success"] = False
    elif kind == "changed_topology":
        case.graph.get_dependency_topology.return_value["edges"].append(["checkoutservice", "currencyservice"])
    elif kind == "missing":
        rows = []
    reader = Mock()
    reader.window.return_value = {"observations": rows}
    if kind == "unavailable":
        reader.window.side_effect = OSError("PRIVATE-STORE-ERROR")
    monkeypatch.setattr(diagnostics, "TransactionReader", Mock(return_value=reader))
    with pytest.raises(ExecutionDenied) as error:
        EXECUTION_CONTEXT(case.graph, grant)
    assert "PRIVATE" not in str(error.value)
    assert_no_effect(case)


def test_actual_context_reader_accepts_current_failed_transaction_without_creating_a_canary(factory, monkeypatch):
    case = factory()
    grant = issue(case)
    from core import telemetry, diagnostics
    monkeypatch.setattr(telemetry, "collector_status", lambda _: {
        "schema_version": 2, "delivery_enabled": False, "heartbeat_fresh": True, "state": "observe_only",
        "evidence_store": {"enabled": True, "state": "ready"}})
    reader = Mock()
    reader.window.return_value = {"observations": [case.symptom]}
    monkeypatch.setattr(diagnostics, "TransactionReader", Mock(return_value=reader))
    result = EXECUTION_CONTEXT(case.graph, grant)
    assert result["symptom"] == case.symptom and result["topology_revision"] == case.origin.topology.revision
    case.observer.assert_not_called()
    assert_no_effect(case)


@pytest.mark.parametrize("exception", [TimeoutError("PRIVATE"), KeyboardInterrupt()])
def test_uncertain_effect_is_retained_and_never_retried(factory, exception):
    case = factory()
    grant = issue(case)
    case.container.restart.side_effect = exception
    if isinstance(exception, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            module.execute_bound_procedure(grant, case.graph)
        result = grant.snapshot()
    else:
        result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "effect_uncertain" and result["action_dispatched"]
    assert not result["operation_confirmed"] and result["reason"] == "reconcile_before_retry"
    assert "PRIVATE" not in json.dumps(result)
    module.execute_bound_procedure(grant, case.graph)
    case.container.restart.assert_called_once()
    with pytest.raises(ExecutionDenied, match="incident_execution_attempt_already_reserved"):
        issue(case)


def test_control_client_intent_mode_has_no_url_credentials_or_http(monkeypatch, capsys):
    from sops import control_client
    monkeypatch.setenv("RCA_CONTROL_MODE", "intent-only")
    monkeypatch.delenv("RCA_CONTROL_URL", raising=False)
    monkeypatch.delenv("RCA_CONTROL_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["control_client.py", "restart"])
    network = Mock(side_effect=AssertionError("No HTTP"))
    monkeypatch.setattr(control_client, "urlopen", network)
    assert control_client.main() == 0
    assert json.loads(capsys.readouterr().out) == {"operation": "restart"}
    network.assert_not_called()


def test_control_client_legacy_http_mode_is_unchanged_without_intent_opt_in(monkeypatch, capsys):
    from sops import control_client
    monkeypatch.delenv("RCA_CONTROL_MODE", raising=False)
    monkeypatch.setenv("RCA_CONTROL_URL", "http://approved-controller.test/operate")
    monkeypatch.setenv("RCA_CONTROL_TOKEN", "DUMMY-TEST-TOKEN")
    monkeypatch.setattr(sys, "argv", ["control_client.py", "restart"])
    request = Mock(return_value=io.BytesIO(b'{"success":true}'))
    monkeypatch.setattr(control_client, "urlopen", request)
    assert control_client.main() == 0
    assert json.loads(capsys.readouterr().out) == {"success": True}
    value = request.call_args.args[0]
    assert value.full_url == "http://approved-controller.test/operate" and value.method == "POST"
    assert json.loads(value.data) == {"operation": "restart"}
    assert value.get_header("Authorization") == "Bearer DUMMY-TEST-TOKEN"


def test_new_execution_sources_are_bound_and_imports_do_not_load_runtime_configuration():
    files = implementation_manifest()["source_sha256"]
    assert {"agent/procedure_execution.py", "agent/tools/bound_executor.py"} <= files.keys()
    code = "import agent.procedure_execution, agent.tools.bound_executor, sys; assert not any(n in sys.modules for n in ('core.config','docker','openai','agent.tools.control_gateway'))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
