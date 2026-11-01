"""Inspect retained diagnostic workflow checkpoints. No execution or resume command."""

import argparse
import json
from pathlib import Path

from core.workflow_checkpoints import CheckpointDenied, DiagnosticCheckpoints


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("audit/diagnostic_workflows.sqlite"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--include-state", action="store_true", help="Include private sanitized evidence; historical only")
    args = parser.parse_args(argv)
    try:
        rows = DiagnosticCheckpoints(args.database).inspect(limit=args.limit, include_state=args.include_state)
    except CheckpointDenied as error:
        print(json.dumps({"outcome": "rejected", "reason": str(error), "execution_authorized": False}))
        return 1
    print(json.dumps({"workflows": rows, "automatic_resume_allowed": False, "execution_authorized": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
