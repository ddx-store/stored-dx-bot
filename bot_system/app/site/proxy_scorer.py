"""
ProxyScorer — real-time proxy health scoring with circuit breaker.
Tracks per-proxy success rates, latency, and per-domain failure counts.
Auto-disables proxies that consistently fail on specific domains.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.logger import get_logger
from app.storage.models import Proxy

log = get_logger(__name__)

_FAILURE_THRESHOLD = 3
_BASE_RECOVERY_SECONDS = 300      # 5 دقائق للفشل المعتدل
_MAX_RECOVERY_SECONDS = 3600      # ساعة كاملة للفشل الشديد
_LATENCY_EWM_ALPHA = 0.2
_SUCCESS_WEIGHT = 0.70
_LATENCY_WEIGHT = 0.30
_MAX_LATENCY_MS = 8000


@dataclass
class _ProxyStats:
    proxy_id: int
    total_attempts: int = 0
    total_successes: int = 0
    avg_latency_ms: float = 1500.0
    site_failures: Dict[str, int] = field(default_factory=dict)
    circuit_open: bool = False
    circuit_open_at: float = 0.0
    consecutive_global_failures: int = 0


class ProxyScorer:
    """
    Thread-safe proxy scorer with per-domain circuit breakers.
    All state is in-memory (per process lifetime).
    """

    def __init__(self) -> None:
        self._stats: Dict[int, _ProxyStats] = {}
        self._lock = threading.Lock()

    def _get_stats(self, proxy_id: int) -> _ProxyStats:
        if proxy_id not in self._stats:
            self._stats[proxy_id] = _ProxyStats(proxy_id=proxy_id)
        return self._stats[proxy_id]

    def score(self, proxy_id: int) -> float:
        with self._lock:
            s = self._get_stats(proxy_id)
            success_rate = s.total_successes / max(s.total_attempts, 1)
            latency_score = max(0.0, 1.0 - s.avg_latency_ms / _MAX_LATENCY_MS)
            return _SUCCESS_WEIGHT * success_rate + _LATENCY_WEIGHT * latency_score

    def _recovery_seconds(self, failures: int) -> float:
        """مدة الاسترداد تتناسب مع شدة الفشل: كلما زاد الفشل، طالت المدة."""
        factor = min(failures / _FAILURE_THRESHOLD, 6.0)
        return min(_BASE_RECOVERY_SECONDS * factor, _MAX_RECOVERY_SECONDS)

    def is_available(self, proxy_id: int, domain: str = "") -> bool:
        with self._lock:
            s = self._get_stats(proxy_id)
            if s.circuit_open:
                required = self._recovery_seconds(s.consecutive_global_failures)
                if time.monotonic() - s.circuit_open_at >= required:
                    s.circuit_open = False
                    s.consecutive_global_failures = 0
                    log.info("Proxy %d circuit recovered (after %.0fs)", proxy_id, required)
                else:
                    return False
            if domain and s.site_failures.get(domain, 0) >= _FAILURE_THRESHOLD:
                return False
            return True

    def pick_best(self, proxies: List[Proxy], domain: str = "") -> Optional[Proxy]:
        if not proxies:
            return None
        available = [p for p in proxies if self.is_available(p.id, domain)]
        if not available:
            log.warning("All proxies circuit-open for domain=%s, using random fallback", domain)
            return proxies[0]
        return max(available, key=lambda p: self.score(p.id))

    def record_result(
        self,
        proxy_id: int,
        domain: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        with self._lock:
            s = self._get_stats(proxy_id)
            s.total_attempts += 1
            if success:
                s.total_successes += 1
                s.consecutive_global_failures = 0
                s.circuit_open = False
                if latency_ms > 0:
                    s.avg_latency_ms = (
                        (1 - _LATENCY_EWM_ALPHA) * s.avg_latency_ms
                        + _LATENCY_EWM_ALPHA * latency_ms
                    )
            else:
                s.consecutive_global_failures += 1
                s.site_failures[domain] = s.site_failures.get(domain, 0) + 1
                if s.consecutive_global_failures >= _FAILURE_THRESHOLD:
                    if not s.circuit_open:
                        s.circuit_open = True
                        s.circuit_open_at = time.monotonic()
                        recovery = self._recovery_seconds(s.consecutive_global_failures)
                        log.warning(
                            "Proxy %d circuit OPEN (failures=%d, recovery=%.0fs)",
                            proxy_id, s.consecutive_global_failures, recovery,
                        )
                if s.site_failures[domain] >= _FAILURE_THRESHOLD:
                    log.warning(
                        "Proxy %d blocked for domain=%s (failures=%d)",
                        proxy_id, domain, s.site_failures[domain],
                    )

    def reset_domain(self, proxy_id: int, domain: str) -> None:
        with self._lock:
            s = self._get_stats(proxy_id)
            s.site_failures.pop(domain, None)

    def summary(self) -> str:
        lines = []
        with self._lock:
            for pid, s in self._stats.items():
                sr = s.total_successes / max(s.total_attempts, 1) * 100
                lines.append(
                    f"proxy#{pid}: score={self.score(pid):.2f} "
                    f"sr={sr:.0f}% lat={s.avg_latency_ms:.0f}ms "
                    f"circuit={'OPEN' if s.circuit_open else 'OK'}"
                )
        return "\n".join(lines) if lines else "No proxy stats yet"


proxy_scorer = ProxyScorer()
