"""Opt-in retained live diagnosis and consistency check; no probes, faults or repairs."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

from agent.shadow import Usage
from agent.shadow_context import CURRENT_EVIDENCE_LAYOUT
from agent.shadow_protocol import ProviderFailure
from eval.shadow_capture import _require_observe_only, capture_observations
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replay import _write_new, implementation_manifest, load_inputs

MAX_CALLS = 4
REFRESH_WAIT_SECONDS = 60
SOURCES = ("eval/shadow_live.py", "eval/shadow_capture.py", "graph/graph_client.py", "core/telemetry.py")


def source_manifest():
    root = Path(__file__).resolve().parents[1]
    return {"investigation": implementation_manifest(), "live_handoff_sha256": {
        name: sha256((root / name).read_bytes()).hexdigest() for name in SOURCES}}


def _configured_driver():
    from agent.shadow_provider import configured_groq_driver
    from langchain_core.globals import get_llm_cache

    driver = configured_groq_driver(structured=True)
    model = driver.model
    if (model.model_name != "openai/gpt-oss-120b" or model.max_retries != 0 or model.temperature != 0
            or model.extra_body != {"reasoning_effort": "low", "include_reasoning": False}
            or get_llm_cache() is not None):
        raise ValueError("The live shadow provider does not match the reviewed configuration")
    model.cache = False
    return driver


class RecordedLiveDriver:
    kind = "model"
    structured_decisions = True

    def __init__(self, driver, output, guard):
        if getattr(driver, "kind", None) != "model" or not driver.structured_decisions:
            raise ValueError("A structured model driver is required")
        self.driver, self.output, self.guard = driver, output, guard
        self.calls, self.replies, self.known_tokens, self.stopped = 0, 0, 0, None

    def _admit(self):
        if self.stopped or self.calls >= MAX_CALLS:
            raise ProviderFailure(self.stopped or "approved_dispatch_ceiling", {"live_dispatch_stopped": True})
        try:
            self.guard()
        except Exception:
            self.stopped = "live_safeguard_failed"
            raise ProviderFailure(self.stopped, {"live_dispatch_stopped": True}) from None

    def estimate_input_tokens(self, messages):
        self._admit()
        return self.driver.estimate_input_tokens(messages)

    def wait_for_admission(self, **kwargs):
        self._admit()
        return self.driver.wait_for_admission(**kwargs)

    def complete(self, messages, **kwargs):
        self._admit()  # Recheck after any provider rate-admission wait.
        path = self.output / f"call-{self.calls + 1:02}"
        _write_new(path.with_suffix(".json"), {"messages": messages, "limits": kwargs,
            "charged_before_sdk": True, "started_at": datetime.now(timezone.utc).isoformat()})
        self.calls += 1
        try:
            reply = self.driver.complete(messages, **kwargs)
        except BaseException as error:
            category = error.category if isinstance(error, ProviderFailure) else "interrupted_or_unavailable"
            self.stopped = "provider_rate_limited" if category == "provider_rate_limited" else "usage_unknown"
            _write_new(path.with_name(path.name + "-failed.json"), {"category": category,
                "diagnostics": error.diagnostics if isinstance(error, ProviderFailure) else None,
                "usage_complete": False, "returned_at": datetime.now(timezone.utc).isoformat()})
            raise
        _write_new(path.with_name(path.name + "-returned.json"), {"content": reply.content,
            "usage": reply.usage, "finish_reason": reply.finish_reason,
            "returned_at": datetime.now(timezone.utc).isoformat()})
        try:
            usage = Usage.model_validate(reply.usage)
        except (ValueError, TypeError):
            self.stopped = "usage_unknown"
        else:
            self.known_tokens += usage.total_tokens
            self.replies += 1
        return reply


def run_live_shadow(audit_dir: Path, output_dir: Path, *, approve_live_transfer=False,
                    driver_factory=None, status_reader=None, topology_loader=None,
                    clock=time.monotonic, sleep=time.sleep):
    """One capture, one model epoch, one later capture and one local recheck.

    A separately owned observer must keep recording transactions/components.
    The passive wait is cooperative; this function creates no live observer.
    """
    from core.telemetry import collector_status

    if approve_live_transfer is not True:
        raise ValueError("Explicit approval for actual observation transfer is required")
    audit_dir, output_dir = Path(audit_dir), Path(output_dir)
    status_reader = status_reader or (lambda: collector_status(audit_dir))
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    frozen_source = source_manifest()
    _write_new(output_dir / "started.json", {"kind": "live-shadow-handoff-started",
        "started_at": datetime.now(timezone.utc).isoformat(), "source": frozen_source,
        "approved_maximum_calls": MAX_CALLS, "evidence_layout": CURRENT_EVIDENCE_LAYOUT,
        "profile": "two-epoch-v1", "investigation_protocol": "bounded-coverage-v1",
        "actual_observation_transfer_approved": True, "new_application_probes": 0,
        "faults_injected": 0, "repairs_run": 0, "execution_authorized": False})
    result = {"kind": "live-shadow-handoff", "outcome": "failed", "reason": None,
        "real_model_calls": 0, "known_reported_tokens": 0, "usage_complete": True,
        "unused_allowance_closed": MAX_CALLS, "allowance_closed": True,
        "candidate_screened": False, "fresh_consistency_verified": False,
        "recovery_verified": False, "causal_attribution": False, "execution_authorized": False}
    controller, recorded = None, None

    def guard():
        if source_manifest() != frozen_source:
            raise ValueError("live_source_changed")
        _require_observe_only(status_reader())

    def capture(name):
        guard()
        value = capture_observations(audit_dir, output_dir / name, status_reader=status_reader,
                                     topology_loader=topology_loader)
        if value["outcome"] != "captured":
            return value, None
        inputs = load_inputs(output_dir / name / "inputs.json")
        if inputs.source_kind != "retained-observation" or inputs.schema_version != 1 or inputs.trace_records:
            raise ValueError("live_capture_profile_changed")
        return value, inputs

    try:
        initial, origin = capture("initial")
        if origin is None:
            result.update(outcome="not_eligible", reason=initial["reason"])
            return result
        guard()
        recorded = RecordedLiveDriver((driver_factory or _configured_driver)(), output_dir, guard)
        controller = ReplanningInvestigation(origin, profile="two-epoch-v1",
            evidence_layout=CURRENT_EVIDENCE_LAYOUT, investigation_protocol="bounded-coverage-v1", clock=clock)
        event = controller.run_epoch(origin, recorded, approve_external=True)
        _write_new(output_dir / "model-epoch.json", event)
        report = event.get("report") or {}
        result["candidate_screened"] = report.get("candidate_screened") is True
        result["reason"] = event["reason"]
        if not result["candidate_screened"] or not report.get("usage_complete") or recorded.stopped:
            return result
        _write_new(output_dir / "after-model.json", controller.snapshot())
        # Wait for a measurement made after the model returned, not just a recut old bundle.
        completed_at = datetime.now(timezone.utc)
        deadline = clock() + REFRESH_WAIT_SECONDS
        refreshed = False
        while clock() < deadline:
            guard()
            from core.diagnostics import DiagnosticRequest, TransactionReader

            rows = TransactionReader(audit_dir / "evidence.sqlite").window(
                DiagnosticRequest(service_id="frontend", window_seconds=60),
                now=datetime.now(timezone.utc).timestamp())["observations"]
            if rows and max(datetime.fromisoformat(row["measured_at"]) for row in rows) > completed_at:
                refreshed = True
                break
            sleep(min(1, max(0, deadline - clock())))
        if not refreshed:
            result["reason"] = "new_transaction_not_observed"
            return result
        latest, fresh = capture("refresh")
        if fresh is None:
            controller.invalidate_current()
            result["reason"] = latest["reason"]
            return result
        guard()
        candidate = controller.snapshot()["candidates"][-1]
        recheck = controller.recheck_candidate(fresh, candidate_revision=candidate["revision"])
        _write_new(output_dir / "candidate-recheck.json", recheck)
        guard()
        current = controller.snapshot()["current_consistency_check"]
        result["fresh_consistency_verified"] = recheck["outcome"] == "consistent" and current is not None
        result.update(outcome="consistent" if result["fresh_consistency_verified"] else "failed",
                      reason=recheck["reason"])
    except Exception:
        result["reason"] = "invalid_or_unavailable_live_handoff"
    except BaseException:
        result.update(outcome="interrupted", reason="live_handoff_interrupted")
        raise
    finally:
        if controller is not None:
            if not result["fresh_consistency_verified"]:
                controller.invalidate_current()
            _write_new(output_dir / "investigation.json", controller.snapshot())
        if recorded is not None:
            result.update(real_model_calls=recorded.calls, known_reported_tokens=recorded.known_tokens,
                usage_complete=recorded.calls == recorded.replies, unused_allowance_closed=MAX_CALLS - recorded.calls)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_new(output_dir / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private audit directory")
    parser.add_argument("--approve-live-transfer", action="store_true",
                        help="Approve sending sanitized actual observations and runtime IDs to configured Groq")
    args = parser.parse_args()
    if not args.approve_live_transfer:
        parser.error("Actual observation transfer needs explicit approval")
    from core.config import settings

    try:
        result = run_live_shadow(settings.audit_dir, args.output, approve_live_transfer=True)
    except FileExistsError:
        parser.error("Use a new directory; earlier runs cannot be overwritten")
    print(json.dumps(result, indent=2))
    return 0 if result["outcome"] in {"consistent", "not_eligible"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
