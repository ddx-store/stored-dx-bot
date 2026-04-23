"""Per-user sliding-window rate limiter (in-memory)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._events: dict[int, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, user_id: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        If allowed, the event is recorded. If not, retry_after tells the user
        how many whole seconds until they can retry.
        """
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            dq = self._events[user_id]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max_events:
                retry = max(1, int(self.window - (now - dq[0])))
                return False, retry
            dq.append(now)
            return True, 0

    def reset(self, user_id: int) -> None:
        with self._lock:
            self._events.pop(user_id, None)
