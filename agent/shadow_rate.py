"""Process-local rate scheduling, distinct from incident budgets and billing caps."""

import threading
import time


class RollingTokenAdmission:
    def __init__(self, *, tokens=8000, window_seconds=61, minimum_interval=31,
                 clock=time.monotonic, sleep=time.sleep):
        if tokens <= 0 or window_seconds <= 0 or minimum_interval < 0:
            raise ValueError("Invalid local rate limits")
        self.tokens, self.window, self.interval = tokens, window_seconds, minimum_interval
        self.clock, self.sleep = clock, sleep
        self.rows, self.last, self.next_ticket = {}, None, 0
        self.lock = threading.Lock()

    def reserve(self, amount, *, remaining_seconds):
        if type(amount) is not int or not 0 < amount <= self.tokens or remaining_seconds <= 0:
            return None
        deadline = self.clock() + remaining_seconds
        while True:
            with self.lock:
                now = self.clock()
                self.rows = {key: row for key, row in self.rows.items() if now - row[0] < self.window}
                gap = max(0, self.last + self.interval - now) if self.last is not None else 0
                if sum(row[1] for row in self.rows.values()) + amount > self.tokens:
                    gap = max(gap, min(row[0] for row in self.rows.values()) + self.window - now)
                if not gap:
                    if now >= deadline:
                        return None
                    self.next_ticket += 1
                    self.rows[self.next_ticket] = (now, amount)
                    self.last = now
                    return self.next_ticket
                if now + gap >= deadline:
                    return None
            self.sleep(gap)

    def record_usage(self, ticket, total):
        """Replace only this rate-window estimate with known usage; no incident refund."""
        if type(total) is not int or total < 0:
            return
        with self.lock:
            if ticket in self.rows:
                stamp, _ = self.rows[ticket]
                self.rows[ticket] = (stamp, total)
