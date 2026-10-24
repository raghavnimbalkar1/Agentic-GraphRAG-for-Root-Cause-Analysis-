"""Offline layout parity and prompt feasibility; never calls a model or runtime."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from agent.shadow_context import EVIDENCE_LAYOUTS, unpack_view
from eval.shadow_preflight import RecordingPolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import FrozenInputs, _write_new, implementation_manifest, load_inputs


def _facts(messages):
    view = unpack_view(json.loads(messages[-1]["content"]))
    view.pop("evidence_index")
    view["initial"].pop("evidence_layout", None)
    return view


def compare_layouts(origin, continuation, *, estimate_input_tokens=None, rate_allowance=8000):
    """Two fixed-policy arms. Token estimates are advisory, not model acceptance."""
    if type(rate_allowance) is not int or rate_allowance <= 0:
        raise ValueError("A positive explicit local rate allowance is required")
    epochs = [FrozenInputs.model_validate(value.model_dump(mode="json")) for value in (origin, continuation)]
    profiles, prompts, decisions = {}, {}, {}
    for layout in EVIDENCE_LAYOUTS:
        policy = RecordingPolicy()
        controller = ReplanningInvestigation(epochs[0], profile="two-epoch-v1", evidence_layout=layout)
        events = [controller.run_epoch(inputs, policy) for inputs in epochs]
        prompts[layout] = policy.prompts
        decisions[layout] = [(event.get("report") or {}).get("turns", []) for event in events]
        attempts = [attempt for event in events for attempt in (event.get("report") or {}).get("attempts", [])]
        rows = []
        for messages, attempt in zip(policy.prompts, attempts, strict=True):
            estimate = estimate_input_tokens(deepcopy(messages)) if estimate_input_tokens is not None else None
            if estimate_input_tokens is not None and (type(estimate) is not int or estimate <= 0):
                raise ValueError("Invalid input-token estimate")
            rows.append({"prompt_sha256": attempt["prompt_sha256"], "prompt_bytes": attempt["prompt_bytes"],
                "estimated_input_tokens": estimate, "output_token_allowance": attempt["max_output_tokens"],
                "estimated_rate_reservation": estimate + attempt["max_output_tokens"] if estimate is not None else None})
        reservations = [row["estimated_rate_reservation"] for row in rows if row["estimated_rate_reservation"] is not None]
        profiles[layout] = {"attempts": rows, "complete_fixed_terminals": all(
            (event.get("report") or {}).get("termination_reason") in {"proposal", "abstained"} for event in events),
            "epoch_reasons": [event["reason"] for event in events],
            "calls_used": controller.snapshot()["incident_budget"]["calls_used"],
            "decisions_used": controller.snapshot()["incident_budget"]["decisions_used"],
            "largest_estimated_rate_reservation": max(reservations, default=None),
            "fits_estimated_rate_allowance": bool(reservations) and max(reservations) <= rate_allowance
                if estimate_input_tokens is not None else None}
    a, b = EVIDENCE_LAYOUTS
    return {"schema_version": 1, "kind": "offline-replanning-layout-preflight",
        "input_digests": [value.digest for value in epochs], "implementation": implementation_manifest(),
        "profile": "two-epoch-v1", "rate_allowance": rate_allowance,
        "matched_underlying_facts": [_facts(value) for value in prompts[a]] == [_facts(value) for value in prompts[b]],
        "matched_fixed_decisions": decisions[a] == decisions[b], "layouts": profiles,
        "real_model_calls": 0, "provider_budget_verified": False, "model_trial_authorized": False,
        "causal_accuracy_measured": False, "execution_authorized": False,
        "limitations": ["Derived display and instructions differ; underlying retained evidence and tools are matched",
            "Fixed-policy read plans only; a model may choose different reads, refuse or return invalid output",
            "Local estimates include caller-supplied estimator overhead; they are not billing bounds",
            "No measured diagnosis benefit, live freshness, physical fault or recovery result"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", action="append", type=Path, required=True, help="Exactly two ordered frozen epochs")
    parser.add_argument("--output", type=Path, required=True, help="New owner-only result; never overwrite")
    parser.add_argument("--estimate-gpt-oss-tokens", action="store_true", help="Use local tokenizer/schema estimation; no SDK/model client")
    args = parser.parse_args()
    if len(args.inputs) != 2 or args.output.exists() or args.output.is_symlink():
        parser.error("Two frozen inputs and a new output path are required")
    try:
        _write_new(args.output.with_name(args.output.name + ".started.json"),
            {"kind": "replanning-layout-preflight-start", "real_model_calls": 0, "execution_authorized": False})
        estimator = None
        if args.estimate_gpt_oss_tokens:
            from types import SimpleNamespace
            from agent.shadow_provider import GroqShadowDriver

            # Only local token/schema methods are used. This object has no SDK,
            # credentials, bind/invoke methods or ability to dispatch a request.
            estimator = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True).estimate_input_tokens
        result = compare_layouts(*[load_inputs(path) for path in args.inputs], estimate_input_tokens=estimator)
        _write_new(args.output, result)
    except (ValueError, OSError, TypeError, ImportError):
        print(json.dumps({"outcome": "rejected", "reason": "preflight_unavailable_or_invalid", "real_model_calls": 0}))
        return 1
    print(json.dumps({"matched_underlying_facts": result["matched_underlying_facts"],
        "matched_fixed_decisions": result["matched_fixed_decisions"], "real_model_calls": 0,
        "largest_reservations": {key: value["largest_estimated_rate_reservation"] for key, value in result["layouts"].items()}}))
    return 0 if (result["matched_underlying_facts"] and result["matched_fixed_decisions"]
        and all(row["complete_fixed_terminals"] for row in result["layouts"].values())
        and all(row["fits_estimated_rate_allowance"] is not False for row in result["layouts"].values())) else 2


if __name__ == "__main__":
    raise SystemExit(main())
