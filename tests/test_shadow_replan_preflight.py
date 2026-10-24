"""Offline layout parity and feasibility controls; no provider construction."""

import json
import os
import subprocess
import sys

import pytest

from eval.shadow_replan_preflight import compare_layouts
from eval.shadow_replay import save_inputs
from tests.test_replanning_profile import full_epochs


@pytest.mark.parametrize("after", [
    {"checkoutservice": "DEGRADED"}, {"paymentservice": "UNKNOWN"},
    {"paymentservice": "DOWN", "currencyservice": "DOWN"}, {},
])
def test_layout_preflight_matches_facts_and_fixed_decisions_without_claiming_model_success(after):
    result = compare_layouts(*full_epochs(after=after))
    assert result["matched_underlying_facts"] and result["matched_fixed_decisions"]
    assert all(row["complete_fixed_terminals"] for row in result["layouts"].values())
    assert all(row["calls_used"] == 16 for row in result["layouts"].values())
    assert all(row["fits_estimated_rate_allowance"] is None for row in result["layouts"].values())
    assert not result["provider_budget_verified"] and not result["model_trial_authorized"]
    assert result["real_model_calls"] == 0 and not result["execution_authorized"]


def test_explicit_local_estimates_can_reject_feasibility_without_raising_the_allowance():
    result = compare_layouts(*full_epochs(), estimate_input_tokens=lambda _: 8000)
    assert result["matched_underlying_facts"] and result["rate_allowance"] == 8000
    assert all(row["largest_estimated_rate_reservation"] == 8768 for row in result["layouts"].values())
    assert all(row["fits_estimated_rate_allowance"] is False for row in result["layouts"].values())
    assert result["real_model_calls"] == 0


@pytest.mark.parametrize("value", [None, True, -1, 0, 1.5])
def test_invalid_estimator_results_are_rejected_not_used_as_free_tokens(value):
    with pytest.raises(ValueError):
        compare_layouts(*full_epochs(), estimate_input_tokens=lambda _: value)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_invalid_rate_allowance_is_not_silently_coerced(value):
    with pytest.raises(ValueError):
        compare_layouts(*full_epochs(), rate_allowance=value)


def test_rejected_second_epoch_is_not_a_complete_preflight():
    first, _ = full_epochs()
    result = compare_layouts(first, first)
    assert all(not row["complete_fixed_terminals"] for row in result["layouts"].values())
    assert all(row["epoch_reasons"][-1] == "cutoff_not_advanced" for row in result["layouts"].values())


def test_cli_can_run_offline_without_credentials_and_preserves_prior_output(tmp_path):
    inputs = [tmp_path / f"epoch-{index}.json" for index in (1, 2)]
    for path, value in zip(inputs, full_epochs(), strict=True):
        save_inputs(path, value)
    output = tmp_path / "preflight.json"
    command = [sys.executable, "-m", "eval.shadow_replan_preflight", "--inputs", str(inputs[0]),
        "--inputs", str(inputs[1]), "--output", str(output)]
    environment = {key: value for key, value in os.environ.items() if "API_KEY" not in key and "PASSWORD" not in key}
    run = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text())["real_model_calls"] == 0
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    repeat = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
    assert repeat.returncode != 0 and output.read_bytes() == before
