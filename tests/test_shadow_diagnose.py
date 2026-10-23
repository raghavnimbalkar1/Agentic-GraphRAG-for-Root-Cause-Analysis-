"""Safe operator entry point: all provider behavior here is stubbed or forbidden."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, seed_case
from eval.shadow_diagnose import diagnose
from eval.shadow_replay import freeze_inputs, save_inputs


@pytest.fixture
def inputs(tmp_path):
    store = seed_case(tmp_path / "source.sqlite", CASES["payment"][0])
    cutoff = datetime.now(timezone.utc)
    frozen = freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff,
                           topology=fixture_topology(cutoff), source_kind="synthetic")
    path = tmp_path / "inputs.json"
    save_inputs(path, frozen)
    return path


def test_default_diagnosis_is_offline_screened_and_private(inputs, tmp_path):
    output = tmp_path / "diagnosis.json"
    result = diagnose(inputs, output)
    assert result["report"]["candidate_screened"]
    assert result["report"]["result"]["proposal"]["service_id"] == "paymentservice"
    assert result["report"]["provider_attempts"] == 0
    assert not result["execution_authorized"]
    assert os.stat(output).st_mode & 0o777 == 0o600
    marker = Path(str(output) + ".started.json")
    assert os.stat(marker).st_mode & 0o777 == 0o600
    assert not json.loads(marker.read_text())["external_transfer_approved"]


def test_external_driver_is_not_constructed_without_approval(inputs, tmp_path, monkeypatch):
    import agent.shadow_provider as provider

    monkeypatch.setattr(provider, "configured_groq_driver", lambda **_: pytest.fail("Unauthorized driver construction"))
    with pytest.raises(ValueError, match="approval"):
        diagnose(inputs, tmp_path / "result.json", provider="groq")
    assert not (tmp_path / "result.json.started.json").exists()


def test_completed_or_interrupted_output_cannot_reset_calls(inputs, tmp_path):
    output = tmp_path / "result.json"
    diagnose(inputs, output)
    old = output.read_bytes()
    with pytest.raises(FileExistsError):
        diagnose(inputs, output)
    assert output.read_bytes() == old
    output.unlink()
    with pytest.raises(FileExistsError):
        diagnose(inputs, output)


def test_initialization_error_is_retained_without_secrets_or_retries(inputs, tmp_path, monkeypatch):
    import agent.shadow_provider as provider

    calls = []
    def unavailable(**_):
        calls.append(1)
        raise ValueError("PRIVATE-KEY-TEXT")
    monkeypatch.setattr(provider, "configured_groq_driver", unavailable)
    output = tmp_path / "failed.json"
    with pytest.raises(ValueError):
        diagnose(inputs, output, provider="groq", approve_external=True)
    data = json.loads(output.read_text())
    assert calls == [1] and data["kind"] == "shadow-diagnosis-failed"
    assert "PRIVATE-KEY-TEXT" not in output.read_text()
    assert not data["result_is_diagnosis"]


def test_cli_refuses_groq_without_approval_before_loading_input(tmp_path):
    result = subprocess.run([sys.executable, "-m", "eval.shadow_diagnose", "--provider", "groq",
        "--inputs", str(tmp_path / "missing.json"), "--output", str(tmp_path / "result.json")], capture_output=True, text=True)
    assert result.returncode == 2 and "--approve-external is required" in result.stderr
    assert not (tmp_path / "result.json.started.json").exists()


def test_import_does_not_load_settings_model_sdk_or_executor():
    subprocess.run([sys.executable, "-c", "import sys; import eval.shadow_diagnose; "
        "assert not any(name in sys.modules for name in ('core.config','docker','agent.graph',"
        "'agent.nodes.reasoner','agent.nodes.executor','agent.shadow_provider','langchain_openai','openai'))"],
        check=True, capture_output=True)
