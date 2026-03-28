"""
AdaptiveThrottler — controls delay between bulk payment jobs.
Adapts delay based on real-time success/failure signals.
Prevents rate-limiting and account flagging from rapid sequential requests.
"""
from __future__ import annotations

import asyncio
import random
import threading
from typing import Callable, List, Optional

from app.core.logger import get_logger

log = get_logger(__name__)

_DEFAULT_DELAY = 35.0
_MIN_DELAY = 10.0
_MAX_DELAY = 180.0
_DECAY = 0.82
_GROWTH = 1.6
_PAUSE_THRESHOLD = 3


class AdaptiveThrottler:
    """
    Adapts inter-job delay based on success/failure feedback.
    - On success: reduce delay (min 10s)
    - On failure: increase delay exponentially (max 180s)
    - 3+ consecutive failures → pause and notify operator
    """

    def __init__(
        self,
        initial_delay: float = _DEFAULT_DELAY,
        min_delay: float = _MIN_DELAY,
        max_delay: float = _MAX_DELAY,
    ) -> None:
        self.delay = initial_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._consecutive_failures = 0
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self.delay = max(self.min_delay, self.delay * _DECAY)
            log.debug("Throttler: success → delay=%.1fs", self.delay)

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self.delay = min(self.max_delay, self.delay * _GROWTH)
            log.info(
                "Throttler: failure #%d → delay=%.1fs",
                self._consecutive_failures, self.delay
            )

    @property
    def should_pause(self) -> bool:
        with self._lock:
            return self._consecutive_failures >= _PAUSE_THRESHOLD

    def reset_failures(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    async def wait(self) -> None:
        """Wait current delay with ±20% jitter."""
        with self._lock:
            base = self.delay
        jitter = random.uniform(0.85, 1.20)
        actual = base * jitter
        log.debug("Throttler: waiting %.1fs before next job", actual)
        await asyncio.sleep(actual)

    @property
    def current_delay(self) -> float:
        with self._lock:
            return self.delay


bulk_throttler = AdaptiveThrottler()
