"""
OTP extraction from email body text.

Supports:
- Numeric OTP codes (4–8 digits)
- Activation / verification links
- Configurable patterns via EXTRA_PATTERNS env var (comma-separated regexes)

Pattern priority (first match wins):
1. Link patterns
2. Numeric OTP patterns
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from app.core.enums import OtpType
from app.core.logger import get_logger

log = get_logger(__name__)

# ------------------------------------------------------------------ #
# Built-in patterns
# ------------------------------------------------------------------ #

# Activation / verification links — adjust the domain(s) to match your site.
_LINK_PATTERNS = [
    # Generic: any URL that contains "verify", "confirm", or "activate"
    r"https?://[^\s\"'<>]+(?:verif|confirm|activat)[^\s\"'<>]*",
    # Token-based: ?token=... or ?code=...
    r"https?://[^\s\"'<>]+[?&](?:token|code|key)=[A-Za-z0-9\-_]+[^\s\"'<>]*",
]

# Numeric OTP patterns (ordered most-specific first).
_NUMERIC_PATTERNS = [
    # "Your OTP is: 123456"
    r"(?:OTP|one.time.(?:password|code)|verification.code)[^\d]{0,30}(\d{4,8})",
    # "Code: 123456"
    r"(?:code|pin)[^\d]{0,10}(\d{4,8})",
    # "123456 is your code"
    r"(\d{4,8})\s+is\s+your\s+(?:code|OTP|one.time|verification)",
    # Last resort: any standalone 4-8 digit sequence.
    r"\b(\d{4,8})\b",
]

# Load any user-supplied extra patterns from env (comma-separated regex strings).
_EXTRA_RAW = os.environ.get("EXTRA_OTP_PATTERNS", "")
_EXTRA_PATTERNS: list[str] = [p.strip() for p in _EXTRA_RAW.split(",") if p.strip()]


def extract_otp(body: str) -> Tuple[Optional[str], OtpType, Optional[str]]:
    """
    Parse *body* and return ``(otp_code, otp_type, link_or_none)``.

    Returns:
        otp_code  — the extracted code string, or None
        otp_type  — OtpType enum value
        link      — the full URL for link-type OTPs, or None
    """
    if not body:
        return None, OtpType.UNKNOWN, None

    # 1. Try link patterns first (more actionable than bare codes).
    all_link_patterns = _LINK_PATTERNS + _EXTRA_PATTERNS
    for pattern in all_link_patterns:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            link = m.group(0)
            log.debug("OTP link matched by pattern %r: %s", pattern, link[:80])
            return None, OtpType.LINK, link

    # 2. Numeric OTP patterns.
    for pattern in _NUMERIC_PATTERNS:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            code = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            log.debug("Numeric OTP matched by pattern %r: %s", pattern, code)
            return code, OtpType.NUMERIC, None

    log.debug("No OTP found in email body (length=%d)", len(body))
    return None, OtpType.UNKNOWN, None
