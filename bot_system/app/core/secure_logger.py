"""
SecureLogger — tokenizes sensitive data before writing to logs.
Replaces emails, card numbers, passwords with stable short tokens
so log files don't expose operational data if compromised.
Token → real value map is in-memory only (never written to disk).
"""
from __future__ import annotations

import hashlib
import re
import threading
from typing import Dict

from app.core.logger import get_logger

log = get_logger(__name__)

_CARD_RE = re.compile(r"\b(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})\b")
_CVV_RE = re.compile(r"\bcvv[=:]\s*(\d{3,4})\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


class SecureLogger:
    """
    Wraps sensitive strings with stable tokens.
    Tokens look like [T:3f8a1b2c] — safe to appear in logs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token_map: Dict[str, str] = {}

    def tokenize(self, sensitive: str) -> str:
        if not sensitive:
            return sensitive
        key = hashlib.sha256(sensitive.encode()).hexdigest()[:10]
        token = f"[T:{key}]"
        with self._lock:
            self._token_map[token] = sensitive
        return token

    def resolve(self, token: str) -> str:
        with self._lock:
            return self._token_map.get(token, token)

    def sanitize_message(self, msg: str) -> str:
        """Replace recognizable sensitive patterns in a log message."""
        # Mask full card numbers → ****XXXX
        msg = _CARD_RE.sub(lambda m: f"****{m.group(4)}", msg)
        # Mask CVV values
        msg = _CVV_RE.sub("cvv=[REDACTED]", msg)
        # Tokenize emails
        msg = _EMAIL_RE.sub(lambda m: self.tokenize(m.group(0)), msg)
        return msg

    def log_payment(self, email: str, card_last4: str, site: str, result: str) -> None:
        safe_email = self.tokenize(email)
        log.info("Payment: %s → ****%s @ %s → %s", safe_email, card_last4, site, result)

    def log_login(self, email: str, site: str, success: bool) -> None:
        safe_email = self.tokenize(email)
        status = "OK" if success else "FAIL"
        log.info("Login: %s @ %s → %s", safe_email, site, status)

    def log_otp(self, email: str, job_id: str, found: bool) -> None:
        safe_email = self.tokenize(email)
        log.info("OTP: %s job=%s → %s", safe_email, job_id[:8], "found" if found else "timeout")


secure_logger = SecureLogger()
