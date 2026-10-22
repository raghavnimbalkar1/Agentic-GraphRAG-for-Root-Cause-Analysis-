"""Timestamp validation with no runtime settings, credentials or service imports."""

import math
import time
from datetime import datetime


def age_seconds(timestamp, *, now: float | None = None) -> float | None:
    """Reject missing, naive, future and non-finite timestamps."""
    try:
        if isinstance(timestamp, str):
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            timestamp = parsed.timestamp()
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            return None
        age = (time.time() if now is None else now) - timestamp
        return age if math.isfinite(age) and age >= 0 else None
    except (ValueError, OverflowError, OSError):
        return None
