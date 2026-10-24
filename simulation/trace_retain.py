"""Retain one validated normalized snapshot in an existing operator-owned evidence store."""

import argparse
import json
import os
from pathlib import Path
import stat
import sqlite3
from contextlib import closing

from core.diagnostics import ComponentReader
from core.evidence import EvidenceStore
from core.trace_evidence import OUTPUT_LIMIT, TraceImportError
from core.trace_store import decode_snapshot


def retain_trace_file(input_path, store_path):
    input_path, store_path = Path(input_path), Path(store_path)
    descriptor = os.open(input_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise TraceImportError("input_not_regular_file")
        snapshot = decode_snapshot(stream.read(OUTPUT_LIMIT + 1))
    # Do not silently create a new runtime store or follow a direct store symlink.
    if not stat.S_ISREG(store_path.lstat().st_mode):
        raise TraceImportError("store_not_regular_file")
    with closing(ComponentReader(store_path)._connect()) as db:
        db.execute("SELECT id FROM evidence LIMIT 0")
    return EvidenceStore(store_path).append_trace_snapshot(snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Normalized snapshot, not raw OTLP")
    parser.add_argument("--store", type=Path, required=True, help="Existing trusted evidence SQLite file")
    args = parser.parse_args()
    try:
        identifier = retain_trace_file(args.input, args.store)
    except (ValueError, OSError, sqlite3.Error) as error:
        reason = str(error) if isinstance(error, TraceImportError) else "trace_retention_failed"
        print(json.dumps({"outcome": "rejected", "reason": reason, "execution_authorized": False}))
        return 1
    print(json.dumps({"outcome": "retained-snapshot", "snapshot_id": identifier, "execution_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
