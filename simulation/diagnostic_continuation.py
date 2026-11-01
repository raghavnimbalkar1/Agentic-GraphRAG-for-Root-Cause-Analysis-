"""Explicit fixed-policy persistent diagnostic epochs; no model, Docker, probes or repairs."""

import argparse
import json
from pathlib import Path

from agent.diagnostic_continuation import continue_diagnosis, start_diagnosis
from core.operation_journal import OperationJournal
from eval.baselines.investigation import FixedProbePolicy
from eval.shadow_replay import _write_new, load_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "continue"))
    parser.add_argument("--origin", type=Path, required=True)
    parser.add_argument("--fresh", type=Path)
    parser.add_argument("--parent-thread")
    parser.add_argument("--workflow-database", type=Path, default=Path("audit/diagnostic_workflows.sqlite"))
    parser.add_argument("--operation-journal", type=Path, default=Path("audit/controlled_operations.sqlite"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approve-start", action="store_true")
    parser.add_argument("--approve-continuation", action="store_true")
    args = parser.parse_args(argv)
    if (args.command == "start" and (not args.approve_start or args.fresh or args.parent_thread)
            or args.command == "continue" and (not args.approve_continuation or not args.fresh or not args.parent_thread)):
        parser.error("Explicit approval and the exact start/continuation inputs are required")
    if args.output.exists() or args.output.is_symlink():
        parser.error("Use a new private output path")
    try:
        origin = load_inputs(args.origin)
        fresh = load_inputs(args.fresh) if args.command == "continue" else None
        _write_new(args.output.with_name(args.output.name + ".started.json"),
                   {"kind": "explicit-diagnostic-epoch", "command": args.command, "real_model_calls": 0,
                    "execution_authorized": False, "automatic_continuation_allowed": False})
    except (OSError, ValueError):
        print(json.dumps({"outcome": "rejected", "reason": "input_or_output_unavailable", "real_model_calls": 0}))
        return 1
    common = {"workflow_database": args.workflow_database, "operation_journal": OperationJournal(args.operation_journal)}
    try:
        if args.command == "start":
            result = start_diagnosis(origin, FixedProbePolicy(), approve_start=True, **common)
        else:
            result = continue_diagnosis(origin, fresh, FixedProbePolicy(), parent_thread_id=args.parent_thread,
                                         approve_continuation=True, **common)
    except KeyboardInterrupt:
        result = {"outcome": "interrupted", "execution_authorized": False}
    try:
        _write_new(args.output, result)
    except OSError:
        print(json.dumps({"outcome": "rejected", "reason": "private_result_write_unconfirmed", "real_model_calls": 0}))
        return 1
    print(json.dumps({key: result.get(key) for key in ("outcome", "reason", "thread_id", "parent_thread_id")}
                     | {"real_model_calls": 0, "execution_authorized": False, "automatic_continuation_allowed": False}))
    return 0 if result["outcome"] == "checkpointed" else 130 if result["outcome"] == "interrupted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
