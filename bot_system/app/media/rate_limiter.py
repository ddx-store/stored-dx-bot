"""
Per-user sliding-window rate limiter for media downloads.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Tuple


class RateLimiter:
    """Simple sliding-window rate limiter keyed by Telegram user ID."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[int, list[float]] = defaultdict(list)

    def check(self, user_id: int) -> Tuple[bool, int]:
        """
        Check whether *user_id* may make another request.

        Returns ``(allowed, seconds_until_reset)``.
        """
        now = time.time()
        cutoff = now - self.window
        self._requests[user_id] = [
            t for t in self._requests[user_id] if t > cutoff
        ]
        if len(self._requests[user_id]) >= self.max_requests:
            oldest = self._requests[user_id][0]
            wait = int(oldest + self.window - now) + 1
            return False, wait
        return True, 0

    def record(self, user_id: int) -> None:
        """Record a download request."""
        self._requests[user_id].append(time.time())

    def reset(self, user_id: int) -> None:
        """Clear history for a single user (admin action)."""
        self._requests[user_id].clear()
