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
    args = parser.parse_args(argv)
    journal = OperationJournal(args.journal)
    try:
        if args.command == "show":
            result = {"attempts": journal.inspect(limit=args.limit, receipt=args.include_receipt),
                      "execution_authorized": False, "automatic_retry_allowed": False}
        else:
            result = journal.acknowledge_terminal(args.attempt_id, expected_version=args.version,
                                                  approve_review=args.approve_review)
    except JournalDenied as error:
        print(json.dumps({"outcome": "rejected", "reason": str(error), "execution_authorized": False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
