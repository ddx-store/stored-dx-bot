"""
Miscellaneous helpers used across modules.
"""

from __future__ import annotations

import random
import re
import string
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def ts_isoformat(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def is_valid_email(address: str) -> bool:
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, address.strip()))


def normalise_url(raw: str) -> str:
    """Ensure the URL has a scheme."""
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def truncate(text: str, max_length: int = 200, suffix: str = "…") -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


# ------------------------------------------------------------------ #
# Fake identity generator — used for registration forms
# ------------------------------------------------------------------ #

_FIRST_NAMES = [
    "James", "Oliver", "Liam", "Noah", "Ethan", "Lucas", "Mason", "Logan",
    "Emma", "Sophia", "Ava", "Isabella", "Mia", "Charlotte", "Amelia", "Harper",
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Parker",
]

_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Anderson", "Taylor", "Thomas", "Jackson",
    "White", "Harris", "Martin", "Thompson", "Young", "Walker", "Hall",
]


def fake_first_name() -> str:
    return random.choice(_FIRST_NAMES)


def fake_last_name() -> str:
    return random.choice(_LAST_NAMES)


def fake_full_name() -> Tuple[str, str]:
    return fake_first_name(), fake_last_name()


def fake_username(email: str) -> str:
    """Derive a username from an email address."""
    local = email.split("@")[0]
    local = re.sub(r"[^a-zA-Z0-9]", "", local)
    return local[:15] or "user" + str(random.randint(1000, 9999))


def fake_birth_year() -> str:
    return str(random.randint(1985, 2000))


def fake_birth_month() -> str:
    return str(random.randint(1, 12)).zfill(2)


def fake_birth_day() -> str:
    return str(random.randint(1, 28)).zfill(2)
