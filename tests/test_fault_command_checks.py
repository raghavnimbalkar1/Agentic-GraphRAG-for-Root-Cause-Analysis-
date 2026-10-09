from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from simulation import fault_injector as injector


@pytest.mark.parametrize("code,output", [(1, b"failed"), (0, b"ERR failure"),
                                         (0, b"NOAUTH denied"), (0, b"READONLY replica"),
                                         (0, b"OOM out of memory")])
def test_failed_command_cannot_count_as_injection_or_reset(code, output):
    container = Mock()
    container.exec_run.return_value = SimpleNamespace(exit_code=code, output=output)
    with pytest.raises(RuntimeError):
        injector._exec_checked(container, ["redis-cli", "PING"])


def test_only_expected_oom_can_be_accepted():
    container = Mock()
    container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b"OOM cap reached")
    injector._exec_checked(container, ["redis-cli", "EVAL"], allow_oom=True)


def test_background_command_must_still_be_running(monkeypatch):
    container = Mock()
    container.client.api.exec_create.return_value = {"Id": "exec-id"}
    container.client.api.exec_inspect.return_value = {"Running": False, "ExitCode": 127}
    monkeypatch.setattr(injector.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Background injector"):
        injector._start_background(container, ["missing-command"])


def test_unsupported_latency_never_accesses_docker(monkeypatch):
    docker = Mock(side_effect=AssertionError("Unsupported fault must not touch Docker"))
    monkeypatch.setattr(injector, "_docker", docker)
    with pytest.raises(NotImplementedError):
        injector.inject_high_latency("frontend")
    with pytest.raises(NotImplementedError):
        injector.reset_high_latency("frontend")
    docker.assert_not_called()


def test_redis_fill_is_a_single_checked_batch(monkeypatch):
    container = Mock()
    monkeypatch.setattr(injector, "_docker", Mock())
    monkeypatch.setattr(injector, "_get_container", lambda *_: container)
    checked = Mock(return_value=SimpleNamespace(output=b"0"))
    monkeypatch.setattr(injector, "_exec_checked", checked)
    injector.inject_redis_oom()
    calls = [call for call in checked.call_args_list if call.args[1][1] == "EVAL"]
    assert len(calls) == 1
    assert calls[0].kwargs["allow_oom"] is True
    container.exec_run.assert_not_called()
