"""
Miscellaneous helpers used across modules.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional


def new_job_id() -> str:
    """Return a short unique job identifier."""
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


def ts_isoformat(dt: Optional[datetime]) -> Optional[str]:
    """Convert a datetime to an ISO-8601 string, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def is_valid_email(address: str) -> bool:
    """Very basic RFC 5322-ish check — good enough for input validation."""
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, address.strip()))


def truncate(text: str, max_length: int = 200, suffix: str = "…") -> str:
    """Truncate *text* to *max_length* characters, appending *suffix* if cut."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
