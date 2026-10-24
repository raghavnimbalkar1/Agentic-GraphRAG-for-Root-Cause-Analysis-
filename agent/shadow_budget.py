"""Process-local incident accounting shared across read-only evidence epochs."""

import math
import threading
import time


class SharedShadowBudget:
    """No reset/refund API. Not a durable ledger, billing cap or hard deadline."""

    def __init__(self, evidence_limits, shadow_limits, *, clock=time.monotonic):
        from agent.investigation import InvestigationLimits
        from agent.shadow import ShadowLimits

        self.evidence_limits = InvestigationLimits.model_validate(evidence_limits.model_dump())
        self.shadow_limits = ShadowLimits.model_validate(shadow_limits.model_dump())
        self.clock = clock
        self.started = clock()
        if type(self.started) not in (int, float) or not math.isfinite(self.started):
            raise ValueError("A finite monotonic clock is required")
        self._last = self._floor_at = self.started
        self._evidence_elapsed = 0.0
        self._accounted_floor = 0.0
        self._calls = self._bytes = self._decisions = self._admission = self._tokens = 0
        self._usage_complete, self._in_flight = True, False
        self._halt = None
        self._lock = threading.RLock()

    def check_evidence_limits(self, limits):
        if limits != self.evidence_limits:
            raise ValueError("Evidence epochs must use the same incident limits")

    def check_shadow_limits(self, limits):
        if limits != self.shadow_limits:
            raise ValueError("Decision epochs must use the same incident limits")

    def _elapsed(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < self._last:
            self._halt = "clock_invalid"
            return min(self.evidence_limits.max_seconds, self.shadow_limits.max_seconds)
        self._last = now
        return max(now - self.started, self._accounted_floor + now - self._floor_at)

    def remaining_seconds(self):
        with self._lock:
            remaining = min(self.evidence_limits.max_seconds, self.shadow_limits.max_seconds) - self._elapsed()
            if remaining <= 0 and self._halt is None:
                self._halt = "time_exhausted"
            return max(0.0, remaining)

    def advance_evidence_time(self, elapsed):
        with self._lock:
            accounted = self._elapsed()
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                    or elapsed < self._evidence_elapsed):
                raise ValueError("Evidence time must advance monotonically")
            self._evidence_elapsed, self._floor_at = elapsed, self._last
            self._accounted_floor = max(accounted, elapsed)
            return self.blocked_reason()

    def blocked_reason(self):
        with self._lock:
            self.remaining_seconds()
            return self._halt

    def halt(self, reason):
        with self._lock:
            if self._halt is None:
                self._halt = reason

    def remaining_calls(self):
        with self._lock:
            return max(0, self.evidence_limits.max_tool_calls - self._calls)

    def claim_tool(self):
        with self._lock:
            if reason := self.blocked_reason():
                return reason
            if not self.remaining_calls():
                return "budget_exhausted"
            self._calls += 1
            return None

    def charge_bytes(self, size):
        with self._lock:
            if type(size) is not int or size < 0:
                raise ValueError("Invalid context charge")
            if self._bytes + size > self.evidence_limits.max_total_bytes:
                return False
            self._bytes += size
            return True

    def remaining_decisions(self):
        with self._lock:
            return max(0, self.shadow_limits.max_decisions - self._decisions)

    def remaining_tokens(self):
        with self._lock:
            return max(0, self.shadow_limits.max_reported_tokens - self._tokens)

    def check_decision(self, admission_units):
        with self._lock:
            if reason := self.blocked_reason():
                return reason
            if self._in_flight:
                return "decision_in_flight"
            if not self.remaining_decisions():
                return "decision_budget_exhausted"
            if (type(admission_units) is not int or admission_units < 0
                    or self._admission + admission_units > self.shadow_limits.max_admission_units
                    or not self.remaining_tokens()):
                return "admission_budget_exhausted"
            return None

    def claim_decision(self, admission_units):
        with self._lock:
            if reason := self.check_decision(admission_units):
                return reason
            self._decisions += 1
            self._admission += admission_units
            self._in_flight = True
            return None

    def record_usage(self, usage):
        with self._lock:
            if not self._in_flight:
                raise ValueError("No charged decision awaits usage")
            if usage is None:
                self._in_flight = False
                self._usage_complete = False
                self.halt("usage_unknown")
            else:
                from agent.shadow import Usage

                try:
                    measured = Usage.model_validate(usage)
                except (ValueError, TypeError):
                    self._in_flight, self._usage_complete = False, False
                    self.halt("usage_unknown")
                    raise ValueError("Decision usage is invalid") from None
                self._in_flight = False
                self._tokens += measured.total_tokens
                if self._tokens > self.shadow_limits.max_reported_tokens:
                    self.halt("reported_token_limit")

    def abandon_in_flight(self):
        with self._lock:
            if self._in_flight:
                self.record_usage(None)

    def snapshot(self):
        with self._lock:
            remaining = self.remaining_seconds()
            return {"scope": "process-local-incident", "calls_used": self._calls,
                    "response_bytes": self._bytes, "decisions_used": self._decisions,
                    "admission_units": self._admission, "known_reported_tokens": self._tokens,
                    "usage_complete": self._usage_complete, "decision_in_flight": self._in_flight,
                    "remaining_seconds": remaining, "evidence_elapsed_seconds": self._evidence_elapsed,
                    "stop_reason": self._halt, "durable": False, "execution_authorized": False}
