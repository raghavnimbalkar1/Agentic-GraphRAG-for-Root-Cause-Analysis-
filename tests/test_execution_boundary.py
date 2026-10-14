from pathlib import Path
from unittest.mock import Mock

import pytest
from docker import APIClient
from docker.models.containers import Container

from agent.tools import sandbox_tools
from agent.tools.control_gateway import ControlSession
from core.config import settings


def managed_target():
    target = Mock()
    target.id = "approved-container-id"
    target.name = "adservice"
    target.attrs = {"Config": {"Labels": {"rca.managed": "online-boutique"}},
                    "NetworkSettings": {"Networks": {"boutique-sim": {}}}}
    return target


def session(script="adservice/throttle.sh", target="adservice"):
    client = Mock()
    client.containers.get.return_value = managed_target()
    return ControlSession(client, settings.sops_dir / script, target, 30), client


def test_operation_grant_is_targeted_and_replay_does_not_repeat_action():
    control, client = session()
    first = control.authorize(control.token, "throttle_cpu")
    assert control.authorize(control.token, "throttle_cpu") == first
    client.containers.get.return_value.update.assert_called_once_with(cpu_period=100_000, cpu_quota=50_000)
    assert client.containers.get.call_args.args == ("approved-container-id",)


def test_cpu_throttle_serializes_through_the_installed_docker_sdk(monkeypatch):
    control, client = session()
    original = client.containers.get.return_value
    # Keep the real SDK validation and serialization, without contacting Docker.
    with APIClient(version="1.45") as api:
        response = Mock()
        response.json.return_value = {"Warnings": []}
        post = Mock(return_value=response)
        monkeypatch.setattr(api, "_post_json", post)
        target = Container(
            attrs={"Id": original.id, "Name": "/adservice", **original.attrs},
            client=Mock(api=api),
        )
        monkeypatch.setattr(target, "reload", Mock())
        client.containers.get.return_value = target

        result = control.authorize(control.token, "throttle_cpu")

        assert result == {"success": True, "action": "throttle_cpu", "target": "adservice"}
        post.assert_called_once()
        assert post.call_args.args[0].endswith("/containers/approved-container-id/update")
        assert post.call_args.kwargs["data"] == {"CpuPeriod": 100_000, "CpuQuota": 50_000}


@pytest.mark.parametrize("token,operation", [("wrong", "throttle_cpu"), (None, "restart")])
def test_wrong_credentials_or_operation_are_denied(token, operation):
    control, client = session()
    with pytest.raises(PermissionError):
        control.authorize(token or control.token, operation)
    client.containers.get.return_value.update.assert_not_called()


def test_sop_cannot_target_a_different_service():
    with pytest.raises(ValueError, match="not authorized"):
        session(target="frontend")


def test_unmanaged_container_is_rejected():
    client = Mock()
    client.containers.get.return_value.attrs = {"Config": {"Labels": {}}}
    with pytest.raises(ValueError, match="not owned"):
        ControlSession(client, settings.sops_dir / "adservice/throttle.sh", "adservice", 30)


def test_expired_or_revoked_grant_cannot_execute():
    control, client = session()
    control.deadline = 0
    with pytest.raises(PermissionError):
        control.authorize(control.token, "throttle_cpu")
    client.containers.get.return_value.update.assert_not_called()


def test_uncertain_operation_is_not_repeated():
    control, client = session()
    client.containers.get.return_value.update.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        control.authorize(control.token, "throttle_cpu")
    assert control.authorize(control.token, "throttle_cpu")["success"] is False
    assert client.containers.get.return_value.update.call_count == 1


def test_partition_repair_reconnects_only_after_restart():
    control, client = session(script="container/restart.sh", target="paymentservice")
    target = client.containers.get.return_value
    target.name = "paymentservice"
    target.attrs["NetworkSettings"]["Networks"] = {}
    order = []
    target.restart.side_effect = lambda **kwargs: order.append("restart")
    client.networks.get.return_value.connect.side_effect = lambda *args, **kwargs: order.append("connect")
    assert control.authorize(control.token, "restart")["success"]
    assert order == ["restart", "connect"]
    client.networks.get.return_value.connect.assert_called_once_with(target, aliases=["paymentservice"])


def sandbox(monkeypatch, failure=None):
    client = Mock()
    client.containers.get.return_value = managed_target()
    execution = client.containers.run.return_value
    execution.wait.return_value = {"StatusCode": 0}
    execution.logs.return_value = b"{}"
    if failure:
        execution.logs.side_effect = failure
    monkeypatch.setattr(sandbox_tools, "_docker_client", lambda: client)
    result = sandbox_tools.execute_sop(str(settings.sops_dir / "redis/config_reset.sh"), "bash",
                                       env_vars={"TARGET_CONTAINER": "redis-cart"})
    return result, client, execution


def test_sandbox_is_nonroot_without_socket_or_extra_capabilities(monkeypatch):
    result, client, execution = sandbox(monkeypatch)
    assert result.success
    arguments = client.containers.run.call_args.kwargs
    assert arguments["user"] == "1000:1000"
    assert arguments["cap_drop"] == ["ALL"]
    assert "cap_add" not in arguments
    assert all("docker.sock" not in path for path in arguments["volumes"])
    execution.remove.assert_called_once_with(force=True)


def test_log_failure_still_cleans_up(monkeypatch):
    result, client, execution = sandbox(monkeypatch, RuntimeError("failed logs"))
    assert not result.success
    execution.remove.assert_called_once_with(force=True)


def test_high_risk_policy_cannot_execute_automatically(monkeypatch):
    docker = Mock(side_effect=AssertionError("Must be rejected before Docker"))
    monkeypatch.setattr(sandbox_tools, "_docker_client", docker)
    result = sandbox_tools.execute_sop(str(settings.sops_dir / "redis/config_reset.sh"), "bash", "HIGH")
    assert not result.success
    docker.assert_not_called()


def test_injector_has_no_operational_graph_access():
    source = (Path(__file__).parents[1] / "simulation/fault_injector.py").read_text()
    assert "GraphClient" not in source
    assert "_update_graph_status" not in source
