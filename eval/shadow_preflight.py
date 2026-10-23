"""Offline prompt-size comparison on frozen inputs, not provider-token certification."""

import argparse
from copy import deepcopy
import json
from math import ceil
from pathlib import Path

from agent.shadow import ShadowLimits
from agent.shadow_context import compact_schema, unpack_view
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import FrozenInputs, _write_new, load_inputs, run_frozen


class RecordingPolicy(FixedProbePolicy):
    """Record actual fixed-policy prompts without constructing any model client."""

    def __init__(self):
        self.prompts = []

    def complete(self, messages, **limits):
        self.prompts.append(deepcopy(messages))
        return super().complete(messages, **limits)


def _logical_prompt(messages):
    schema = json.loads(messages[0]["content"].split("\nDECISION SCHEMA:\n", 1)[1])
    return {"view": unpack_view(json.loads(messages[-1]["content"])), "schema": compact_schema(schema)}


def compare_profiles(inputs: FrozenInputs, *, shadow_limits=None):
    """Same frozen data/fixed decisions/limits; never predicts a model's read choices."""
    limits = ShadowLimits.model_validate((shadow_limits or ShadowLimits()).model_dump())
    runs, prompts = {}, {}
    for profile in ("standard", "compact"):
        policy = RecordingPolicy()
        runs[profile] = run_frozen(inputs, policy, screening="dependency", context_profile=profile,
                                   shadow_limits=limits)
        prompts[profile] = policy.prompts
    equal_facts = ([_logical_prompt(row) for row in prompts["standard"]]
                   == [_logical_prompt(row) for row in prompts["compact"]])
    a, b = (runs[name]["report"] for name in ("standard", "compact"))
    equal_decisions = a["turns"] == b["turns"] and a["result"] == b["result"]
    complete = (equal_facts and equal_decisions
                and a["termination_reason"] == b["termination_reason"]
                and a["termination_reason"] in {"proposal", "abstained"})
    profiles = {}
    for profile in ("standard", "compact"):
        report = runs[profile]["report"]
        attempts = []
        for messages, recorded in zip(prompts[profile], report["attempts"], strict=True):
            text_bytes = sum(len(item["content"].encode()) for item in messages)
            attempts.append({"number": recorded["number"], "prompt_sha256": recorded["prompt_sha256"],
                             "prompt_envelope_bytes": recorded["prompt_bytes"], "content_utf8_bytes": text_bytes,
                             "output_token_cap": recorded["max_output_tokens"]})
        # Sensitivity assumptions, NOT a confidence interval, tokenizer or bound.
        scenarios = [{"assumed_utf8_bytes_per_input_token": ratio,
                      "assumed_input_tokens": sum(ceil(row["content_utf8_bytes"] / ratio) for row in attempts),
                      "reserved_output_tokens": sum(row["output_token_cap"] for row in attempts)}
                     for ratio in (4, 3, 2, 1)]
        for scenario in scenarios:
            scenario["assumed_total_tokens"] = scenario["assumed_input_tokens"] + scenario["reserved_output_tokens"]
            scenario["within_reported_threshold_under_assumption"] = (
                scenario["assumed_total_tokens"] <= limits.max_reported_tokens)
        profiles[profile] = {"attempts": attempts, "admission_units": report["admission_units"],
                             "prompt_envelope_bytes": sum(row["prompt_envelope_bytes"] for row in attempts),
                             "termination_reason": report["termination_reason"],
                             "candidate_screened": report["candidate_screened"],
                             "session_calls": report["session"]["calls_used"], "token_sensitivity": scenarios}
    standard_bytes = profiles["standard"]["prompt_envelope_bytes"]
    compact_bytes = profiles["compact"]["prompt_envelope_bytes"]
    return {"schema_version": 1, "kind": "offline-shadow-context-preflight", "input_digest": inputs.digest,
            "source_kind": inputs.source_kind, "implementation": runs["standard"]["implementation"],
            "shadow_limits": limits.model_dump(), "matched_public_facts": equal_facts,
            "matched_fixed_decisions": equal_decisions, "matched_complete_control": complete,
            "profiles": profiles, "envelope_bytes_saved": standard_bytes - compact_bytes,
            "envelope_reduction_fraction": (standard_bytes - compact_bytes) / standard_bytes if standard_bytes else None,
            "real_model_calls": 0, "provider_budget_verified": False, "model_trial_authorized": False,
            "causal_accuracy_measured": False, "execution_authorized": False,
            "limitations": ["Fixed-policy prompts only; a model can choose different reads or invalid replies",
                            "Token scenarios are arithmetic assumptions, not measured tokens or safe bounds",
                            "Provider formatting, hidden input overhead and output/reasoning behavior are unverified",
                            "No tokenizer, provider request, Docker operation, fault or repair was used",
                            "Byte savings do not establish model comprehension or diagnosis acceptance"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True, help="Previously frozen, sanitized local input bundle")
    parser.add_argument("--output", type=Path, required=True, help="New owner-only local report; cannot overwrite")
    args = parser.parse_args()
    result = compare_profiles(load_inputs(args.inputs))
    _write_new(args.output, result)
    print(json.dumps({key: result[key] for key in ("matched_public_facts", "matched_fixed_decisions",
        "matched_complete_control", "envelope_bytes_saved", "real_model_calls", "provider_budget_verified")}))
    raise SystemExit(0 if result["matched_complete_control"] else 1)


if __name__ == "__main__":
    main()
