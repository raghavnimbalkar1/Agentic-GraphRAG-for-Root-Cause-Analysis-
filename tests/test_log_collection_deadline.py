"""Supervised log-collection checks use synthetic data and a killed local sleeper, never Docker."""

from datetime import datetime, timedelta, timezone
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import simulation.log_collector as collector
from core.log_signals import LogObservation
from tests.test_log_evidence import client_for, frame, line


@pytest.fixture
def record():
    end = datetime.now(timezone.utc)
    client, _, _ = client_for(frame(line(end - timedelta(seconds=1))))
    return collector.collect_frontend_logs(lambda: client, now=end)


def responder(monkeypatch, payload, *, code=0):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write(payload)
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr(collector.subprocess, "run", run)
    return calls


def test_validated_projection_crosses_the_worker_boundary_without_raw_logs(monkeypatch, record):
    calls = responder(monkeypatch, record.model_dump_json().encode())
    result = collector.collect_supervised_frontend_logs(now=record.measured_at)
    assert result == record
    command, options = calls[0]
    assert command[:3] == [sys.executable, "-m", "simulation.log_collector"]
    assert options["timeout"] == 5 and options["stderr"] == subprocess.DEVNULL
    assert options["stdin"] == subprocess.DEVNULL and options["check"] is False
    assert "private-" not in result.model_dump_json()


@pytest.mark.parametrize("payload", [b"private-error", b"x" * 16385, b"{}",
    b'{"message":"private-token"}'])
def test_malformed_or_oversized_worker_output_is_unavailable(monkeypatch, record, payload):
    responder(monkeypatch, payload)
    result = collector.collect_supervised_frontend_logs(now=record.measured_at)
    assert result.outcome == "unavailable" and result.reason == "docker_unavailable"
    assert not result.signals and result.instance_id is None
    assert "private-" not in result.model_dump_json()


def test_failed_worker_cannot_supply_partial_success(monkeypatch, record):
    responder(monkeypatch, record.model_dump_json().encode(), code=1)
    result = collector.collect_supervised_frontend_logs(now=record.measured_at)
    assert result.outcome == "unavailable" and not result.signals


def test_worker_self_deadline_remains_a_deadline(monkeypatch, record):
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("POSIX worker alarm is unavailable")
    responder(monkeypatch, b"", code=-signal.SIGALRM)
    result = collector.collect_supervised_frontend_logs(now=record.measured_at)
    assert result.reason == "deadline" and not result.signals


@pytest.mark.parametrize("field,value", [("environment_id", "other-env"),
    ("registry_revision", "other-revision"), ("registry_digest", "b" * 64),
    ("measured_at", "2020-01-01T00:00:00Z"), ("collected_at", "2099-01-01T00:00:00Z")])
def test_mismatched_worker_scope_is_rejected(monkeypatch, record, field, value):
    payload = record.model_dump(mode="json")
    payload[field] = value
    responder(monkeypatch, json.dumps(payload).encode())
    assert collector.collect_supervised_frontend_logs(now=record.measured_at).outcome == "unavailable"


def test_timeout_kills_and_reaps_a_real_stalled_worker_and_discards_partial_output(monkeypatch, record, tmp_path):
    original_run = subprocess.run
    pid_file = tmp_path / "worker.pid"
    program = ("import os,sys,time; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        f"sys.stdout.write({record.model_dump_json()!r}); sys.stdout.flush(); time.sleep(30)")
    def stalled(command, **kwargs):
        assert "--scan-worker" in command
        return original_run([sys.executable, "-c", program], **kwargs)
    monkeypatch.setattr(collector.subprocess, "run", stalled)
    monkeypatch.setattr(collector, "READ_SECONDS", 0.5)
    started = time.monotonic()
    result = collector.collect_supervised_frontend_logs(now=record.measured_at)
    elapsed = time.monotonic() - started
    assert result.outcome == "limited" and result.reason == "deadline"
    assert not result.signals and result.instance_id is None and result.lines_read == 0
    assert elapsed < 3
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.parametrize("kwargs", [{"window_seconds": True}, {"window_seconds": 5},
    {"now": datetime(2020, 1, 1)}])
def test_invalid_window_or_naive_time_never_starts_a_worker(monkeypatch, kwargs):
    run = Mock(side_effect=AssertionError("Unexpected worker"))
    monkeypatch.setattr(collector.subprocess, "run", run)
    with pytest.raises(ValueError):
        collector.collect_supervised_frontend_logs(**kwargs)
    run.assert_not_called()


def test_retained_read_mode_starts_no_worker_and_persists_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("core.config.settings", SimpleNamespace(audit_dir=tmp_path))
    worker, store = Mock(side_effect=AssertionError("Unexpected worker")), Mock()
    monkeypatch.setattr(collector, "collect_supervised_frontend_logs", worker)
    monkeypatch.setattr("core.evidence.EvidenceStore", store)
    monkeypatch.setattr(collector, "LogReader", lambda _: SimpleNamespace(window=lambda _: {"batches": []}))
    monkeypatch.setattr(sys, "argv", ["log_collector", "--read"])
    collector.main()
    assert json.loads(capsys.readouterr().out) == {"batches": []}
    worker.assert_not_called()
    store.assert_not_called()


def test_cli_persists_only_the_completed_projection_once_and_restores_signal(monkeypatch, record, tmp_path, capsys):
    monkeypatch.setattr("core.config.settings", SimpleNamespace(audit_dir=tmp_path))
    worker, store = Mock(return_value=record), Mock()
    monkeypatch.setattr(collector, "collect_supervised_frontend_logs", worker)
    monkeypatch.setattr("core.evidence.EvidenceStore", lambda _: store)
    monkeypatch.setattr(sys, "argv", ["log_collector", "--window-seconds", "60"])
    previous = signal.getsignal(signal.SIGTERM)
    collector.main()
    assert signal.getsignal(signal.SIGTERM) == previous
    assert LogObservation.model_validate(json.loads(capsys.readouterr().out)) == record
    worker.assert_called_once_with(window_seconds=60)
    store.append_log_observation.assert_called_once_with(record)


def test_internal_worker_emits_projection_without_database_writes(monkeypatch, record, capsys):
    scan, store = Mock(return_value=record), Mock()
    monkeypatch.setattr(collector, "collect_frontend_logs", scan)
    monkeypatch.setattr("core.evidence.EvidenceStore", store)
    monkeypatch.setattr(signal, "signal", Mock())
    monkeypatch.setattr(signal, "alarm", Mock(), raising=False)
    monkeypatch.setattr(sys, "argv", ["log_collector", "--scan-worker", "--window-seconds", "60",
                                    "--scan-end", record.measured_at.isoformat()])
    collector.main()
    assert LogObservation.model_validate_json(capsys.readouterr().out) == record
    assert scan.call_args.kwargs["now"] == record.measured_at
    store.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX CLI termination check")
def test_sigterm_of_cli_reaps_its_worker_without_persisting_partial_evidence(tmp_path):
    pid_file = tmp_path / "child.pid"
    child = ("import os,time; from pathlib import Path; "
             f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)")
    parent_program = "\n".join([
        "import subprocess,sys",
        "import simulation.log_collector as collector",
        "original = subprocess.run",
        f"def stalled(command, **kwargs): return original([sys.executable, '-c', {child!r}], **kwargs)",
        "collector.subprocess.run = stalled",
        "sys.argv = ['log_collector']",
        "collector.main()",
    ])
    parent = subprocess.Popen([sys.executable, "-c", parent_program],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists(), "Worker did not start"
        parent.send_signal(signal.SIGTERM)
        assert parent.wait(timeout=3) == 128 + signal.SIGTERM
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
