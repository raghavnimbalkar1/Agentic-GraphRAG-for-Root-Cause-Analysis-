"""Operator-owned retained-evidence diagnosis. Offline by default; never executes repairs."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from agent.shadow import ShadowLimits
from agent.investigation import InvestigationLimits
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import _write_new, implementation_manifest, load_inputs, run_frozen


def diagnose(inputs_path, output_path, *, provider="fixed", approve_external=False,
             max_reported_tokens=32768, max_seconds=360, max_decisions=6):
    """A new exclusive marker is required before constructing any external driver."""
    if provider not in {"fixed", "groq"}:
        raise ValueError("Unsupported diagnosis provider")
    if provider == "groq" and not approve_external:
        raise ValueError("Groq transfers sanitized deployment evidence; explicit approval is required")
    output_path = Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError("Use a new output path; previous results must be preserved")
    inputs = load_inputs(Path(inputs_path))
    limits = ShadowLimits(max_reported_tokens=max_reported_tokens, max_seconds=max_seconds, max_decisions=max_decisions)
    evidence_limits = InvestigationLimits(max_seconds=max_seconds)
    marker = output_path.with_name(output_path.name + ".started.json")
    _write_new(marker, {"input_digest": inputs.digest, "provider": provider,
        "external_transfer_approved": bool(provider == "groq" and approve_external),
        "started_at": datetime.now(timezone.utc).isoformat(), "limits": limits.model_dump(),
        "implementation": implementation_manifest(), "execution_authorized": False})
    try:
        if provider == "groq":
            from agent.shadow_provider import configured_groq_driver

            driver = configured_groq_driver(structured=True)
            driver.model.cache = False
        else:
            driver = FixedProbePolicy()
        result = run_frozen(inputs, driver, screening="dependency", context_profile="structured", shadow_limits=limits,
                            investigation_limits=evidence_limits)
    except Exception as error:
        _write_new(output_path, {"kind": "shadow-diagnosis-failed", "input_digest": inputs.digest,
            "exception_class": type(error).__name__, "raw_exception_retained": False,
            "execution_authorized": False, "result_is_diagnosis": False})
        raise
    _write_new(output_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True, help="Sanitized frozen input bundle, synthetic or retained")
    parser.add_argument("--output", type=Path, required=True, help="New private local result; interrupted runs cannot be reset")
    parser.add_argument("--provider", choices=["fixed", "groq"], default="fixed")
    parser.add_argument("--approve-external", action="store_true", help="Explicitly approve sending this bundle's projected evidence to Groq")
    parser.add_argument("--max-reported-tokens", type=int, default=32768,
                        help="Explicit incident threshold, not a guaranteed provider billing cap (default: 32768)")
    parser.add_argument("--max-seconds", type=int, default=360, help="Explicit wall-time allowance including rate waits (default: 360)")
    parser.add_argument("--max-decisions", type=int, default=6, help="Bounded decisions including corrections (default: 6)")
    args = parser.parse_args()
    if args.provider == "groq" and not args.approve_external:
        parser.error("--approve-external is required for Groq; fixed-policy diagnosis is offline")
    result = diagnose(args.inputs, args.output, provider=args.provider, approve_external=args.approve_external,
                      max_reported_tokens=args.max_reported_tokens, max_seconds=args.max_seconds, max_decisions=args.max_decisions)
    report = result["report"]
    print(json.dumps({"outcome": report["result"]["outcome"], "termination_reason": report["termination_reason"],
        "candidate": report["result"].get("proposal", {}).get("service_id"), "candidate_screened": report["candidate_screened"],
        "provider_attempts": report["provider_attempts"], "tool_calls": report["session"]["calls_used"],
        "known_reported_tokens": report["known_reported_tokens"], "execution_authorized": False}))


if __name__ == "__main__":
    main()
