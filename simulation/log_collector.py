"""Opt-in read-only frontend log scan. It never alerts, calls a model or repairs."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time

from core.log_signals import EVENT_LIMIT, LogObservation, LogReader, frontend_signal
from core.registry import DEFAULT_DEPLOYMENT

IMAGE = "us-central1-docker.pkg.dev/google-samples/microservices-demo/frontend:v0.10.5"
BYTE_LIMIT = 262144
LINE_LIMIT = 500
LINE_BYTES = 16384
READ_SECONDS = 5


class ScanLimit(Exception):
    pass


class InvalidFrame(Exception):
    pass


def _payload_chunks(response, stats, deadline, monotonic):
    """Bound Docker multiplex framing without the SDK's unbounded frame allocation."""
    header, remaining = b"", 0
    for chunk in response.iter_content(chunk_size=4096):
        if monotonic() > deadline:
            raise ScanLimit("deadline")
        if len(chunk) > BYTE_LIMIT - stats["bytes_read"]:
            raise ScanLimit("byte_limit")
        stats["bytes_read"] += len(chunk)
        while chunk:
            if not remaining:
                take = min(8 - len(header), len(chunk))
                header, chunk = header + chunk[:take], chunk[take:]
                if len(header) != 8:
                    continue
                stream, remaining = struct.unpack(">BxxxL", header)
                if stream != 1 or header[1:4] != b"\0\0\0":
                    raise InvalidFrame()
                header = b""
                if remaining > BYTE_LIMIT:
                    raise ScanLimit("byte_limit")
            if remaining:
                take = min(remaining, len(chunk))
                yield chunk[:take]
                remaining, chunk = remaining - take, chunk[take:]
    if remaining or header:
        raise InvalidFrame()


def _lines(chunks, stats):
    pending, oversized = b"", False
    for chunk in chunks:
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            if len(pending) + len(part) > LINE_BYTES:
                pending, oversized = b"", True
            elif not oversized:
                pending += part
            if index < len(parts) - 1:
                stats["lines_read"] += 1
                if oversized:
                    stats["discarded_lines"] += 1
                else:
                    yield pending
                pending, oversized = b"", False
                if stats["lines_read"] >= LINE_LIMIT:
                    raise ScanLimit("line_limit")
    if pending or oversized:
        stats["lines_read"] += 1
        if oversized:
            stats["discarded_lines"] += 1
        else:
            yield pending


def _owned(target):
    labels = target.attrs.get("Config", {}).get("Labels") or {}
    return (target.name == DEFAULT_DEPLOYMENT.by_name["frontend"].container_name
            and isinstance(target.id, str) and re.fullmatch(r"[a-f0-9]{64}", target.id) is not None
            and isinstance(labels, dict) and labels.get(DEFAULT_DEPLOYMENT.ownership_label)
            == DEFAULT_DEPLOYMENT.ownership_value)


def collect_frontend_logs(client_factory, *, window_seconds=60, now=None, monotonic=time.monotonic):
    if type(window_seconds) is not int or window_seconds not in (60, 300, 900):
        raise ValueError("Unsupported log collection window")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        raise ValueError("Log collection requires an aware clock")
    start = end - timedelta(seconds=window_seconds)
    stats = {"bytes_read": 0, "lines_read": 0, "discarded_lines": 0}
    signals, instance, client = [], None, None
    outcome, reason = "sampled", "none"
    try:
        client = client_factory()
        target = client.containers.get(DEFAULT_DEPLOYMENT.by_name["frontend"].container_name)
        if not _owned(target):
            outcome, reason = "denied", "ownership_mismatch"
        elif target.attrs.get("Config", {}).get("Image") != IMAGE or target.attrs.get("Config", {}).get("Tty"):
            outcome, reason = "unsupported", "unsupported_image"
        else:
            instance = target.id
            # Keep private SDK transport access here: public logs(stream=True) disables read timeouts.
            url = client.api._url("/containers/{0}/logs", instance)
            params = {"stdout": 1, "stderr": 0, "timestamps": 1, "follow": 0, "tail": LINE_LIMIT,
                      "since": int(start.timestamp()), "until": int(end.timestamp()) + 1}
            deadline = monotonic() + READ_SECONDS
            with client.api._get(url, params=params, stream=True, timeout=(2, 2)) as response:
                response.raise_for_status()
                for line in _lines(_payload_chunks(response, stats, deadline, monotonic), stats):
                    supported, signal = frontend_signal(line, instance, start, end)
                    if not supported:
                        stats["discarded_lines"] += 1
                    if signal is not None:
                        signals.append(signal)
                        if len(signals) >= EVENT_LIMIT:
                            raise ScanLimit("event_limit")
            if stats["lines_read"] and stats["discarded_lines"] == stats["lines_read"]:
                outcome, reason = "unsupported", "unsupported_format"
    except ScanLimit as exc:
        outcome, reason = "limited", str(exc)
    except InvalidFrame:
        outcome, reason = "unavailable", "invalid_frame"
    except Exception:
        # Never serialize SDK exception strings: URLs, credentials or raw log text may be present.
        outcome, reason = "unavailable", "docker_unavailable"
    finally:
        if client is not None:
            if instance is not None:
                try:
                    current = client.containers.get(DEFAULT_DEPLOYMENT.by_name["frontend"].container_name)
                    if not _owned(current) or current.id != instance:
                        signals, outcome, reason = [], "denied", "target_changed"
                except Exception:
                    signals, outcome, reason = [], "unavailable", "docker_unavailable"
            try:
                client.close()
            except Exception:
                pass
    registry = DEFAULT_DEPLOYMENT
    return LogObservation(environment_id=registry.environment_id, registry_revision=registry.revision,
                          registry_digest=registry.digest, instance_id=instance, window_start=start,
                          measured_at=end, collected_at=datetime.now(timezone.utc),
                          outcome=outcome, reason=reason, signals=signals, **stats)


def _failed_scan(window_seconds, end, reason):
    registry = DEFAULT_DEPLOYMENT
    return LogObservation(environment_id=registry.environment_id, registry_revision=registry.revision,
        registry_digest=registry.digest, instance_id=None, window_start=end - timedelta(seconds=window_seconds),
        measured_at=end, collected_at=datetime.now(timezone.utc),
        outcome="limited" if reason == "deadline" else "unavailable", reason=reason, signals=[])


def _terminate_scan(signum, _frame):
    raise SystemExit(128 + signum)


def collect_supervised_frontend_logs(*, window_seconds=60, now=None):
    """Bound the complete SDK scan, including startup, streaming and final identity checks."""
    if type(window_seconds) is not int or window_seconds not in (60, 300, 900):
        raise ValueError("Unsupported log collection window")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        raise ValueError("Log collection requires an aware clock")
    command = [sys.executable, "-m", "simulation.log_collector",
               "--scan-worker", "--window-seconds", str(window_seconds), "--scan-end", end.isoformat()]
    try:
        # The worker emits only the validated projection; its raw Docker stream never crosses this boundary.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=READ_SECONDS, check=False)
            if result.returncode:
                if hasattr(signal, "SIGALRM") and result.returncode == -signal.SIGALRM:
                    return _failed_scan(window_seconds, end, "deadline")
                return _failed_scan(window_seconds, end, "docker_unavailable")
            output.seek(0)
            payload = output.read(16385)
        if len(payload) > 16384:
            return _failed_scan(window_seconds, end, "docker_unavailable")
        record = LogObservation.model_validate_json(payload)
        registry = DEFAULT_DEPLOYMENT
        if (record.environment_id != registry.environment_id or record.registry_revision != registry.revision
                or record.registry_digest != registry.digest or record.measured_at != end
                or record.window_start != end - timedelta(seconds=window_seconds)
                or record.collected_at > datetime.now(timezone.utc)):
            return _failed_scan(window_seconds, end, "docker_unavailable")
        return record
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the worker; partial output cannot become evidence.
        return _failed_scan(window_seconds, end, "deadline")
    except (OSError, ValueError):
        return _failed_scan(window_seconds, end, "docker_unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-seconds", type=int, choices=(60, 300, 900), default=60)
    parser.add_argument("--read", action="store_true", help="Read retained scans only; do not contact Docker")
    parser.add_argument("--scan-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scan-end", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.scan_worker:
        if args.read or not args.scan_end:
            parser.error("Invalid worker arguments")
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.alarm(READ_SECONDS)
        import docker
        from core.config import settings
        record = collect_frontend_logs(lambda: docker.DockerClient(base_url=settings.docker_host, timeout=2),
            window_seconds=args.window_seconds, now=datetime.fromisoformat(args.scan_end))
        print(record.model_dump_json())
        return
    if args.scan_end:
        parser.error("A scan cutoff is internal to the collection worker")
    from core.config import settings

    path = settings.audit_dir / "evidence.sqlite"
    if args.read:
        from core.diagnostics import DiagnosticRequest

        result = LogReader(path).window(DiagnosticRequest(service_id="frontend", window_seconds=args.window_seconds))
    else:
        from core.evidence import EvidenceStore

        previous = signal.signal(signal.SIGTERM, _terminate_scan)
        try:
            record = collect_supervised_frontend_logs(window_seconds=args.window_seconds)
        finally:
            signal.signal(signal.SIGTERM, previous)
        EvidenceStore(path).append_log_observation(record)
        result = record.model_dump(mode="json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
