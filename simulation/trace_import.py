"""Normalize one local OTLP/JSON export; no listener, probes, provider or repairs."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import stat

from core.trace_evidence import INPUT_LIMIT, TraceImportError, normalize_otlp


def import_trace_file(input_path, output_path, *, window_end, source_kind, window_seconds=60):
    """Only trusted operator code supplies paths. Existing results are never overwritten."""
    input_path, output_path = Path(input_path), Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError("Use a new trace evidence output")
    descriptor = os.open(input_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise TraceImportError("input_not_regular_file")
        raw = stream.read(INPUT_LIMIT + 1)
    result = normalize_otlp(
        raw, window_end=window_end, source_kind=source_kind, window_seconds=window_seconds
    )
    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="One bounded OTLP/JSON object in a regular file"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private normalized snapshot, never raw export",
    )
    parser.add_argument(
        "--window-end",
        required=True,
        help="Timezone-aware ISO timestamp, not later than import time",
    )
    parser.add_argument("--window-seconds", type=int, choices=[60, 300, 900], default=60)
    parser.add_argument("--source-kind", choices=["synthetic", "retained-export"], required=True)
    args = parser.parse_args()
    try:
        result = import_trace_file(
            args.input,
            args.output,
            window_end=datetime.fromisoformat(args.window_end),
            source_kind=args.source_kind,
            window_seconds=args.window_seconds,
        )
    except (ValueError, OSError) as error:
        reason = str(error) if isinstance(error, TraceImportError) else "trace_import_failed"
        print(json.dumps({"outcome": "rejected", "reason": reason, "execution_authorized": False}))
        return 1
    print(
        json.dumps(
            {
                "outcome": "normalized-export",
                "retained_spans": result["retained_spans"],
                "observed_calls": len(result["observed_calls"]),
                "topology_revision": result["topology_revision"],
                "complete_topology": False,
                "execution_authorized": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
