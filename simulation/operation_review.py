"""Inspect controlled attempts or acknowledge an eligible terminal barrier; never executes an SOP."""

import argparse
import json
from pathlib import Path

from core.operation_journal import JournalDenied, OperationJournal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("audit/controlled_operations.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show")
    show.add_argument("--limit", type=int, default=20)
    show.add_argument("--include-receipt", action="store_true")
    review = commands.add_parser("acknowledge-terminal")
    review.add_argument("attempt_id")
    review.add_argument("--version", type=int, required=True)
    review.add_argument("--approve-review", action="store_true")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("attempt_id")
    reconcile.add_argument("--version", type=int, required=True)
    reconcile.add_argument("--approve-runtime-inspection", action="store_true")
    reconcile.add_argument("--approve-current-state-closure", action="store_true",
        help="Accept fresh scoped current-state evidence only; not proof of the old effect or permission to retry")
    args = parser.parse_args(argv)
    journal = OperationJournal(args.journal)
    try:
        if args.command == "show":
            result = {"attempts": journal.inspect(limit=args.limit, receipt=args.include_receipt),
                      "execution_authorized": False, "automatic_retry_allowed": False}
        elif args.command == "acknowledge-terminal":
            result = journal.acknowledge_terminal(args.attempt_id, expected_version=args.version,
                                                  approve_review=args.approve_review)
        else:
            if not args.approve_runtime_inspection:
                print(json.dumps({"outcome": "rejected", "reason": "explicit_runtime_inspection_required", "execution_authorized": False}))
                return 1
            from agent.tools.operation_reconciliation import reconcile_attempt

            result = reconcile_attempt(journal, args.attempt_id, expected_version=args.version,
                approve_runtime_inspection=args.approve_runtime_inspection,
                approve_current_state_closure=args.approve_current_state_closure)
            if result["outcome"] != "observed_healthy":
                print(json.dumps(result, indent=2))
                return 1
    except JournalDenied as error:
        print(json.dumps({"outcome": "rejected", "reason": str(error), "execution_authorized": False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
