"""
OTP email matcher.

Given a job (email address + creation time), finds the right OTP email
from a list of candidates. Uses multiple signals to avoid mixing codes
between concurrent jobs:
  - recipient address match
  - time window (email must arrive after job was created)
  - known sender / subject pattern (optional, configured per deployment)
  - deduplication via the OtpMessageRepository
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.core.logger import get_logger
from app.gmail.parser import extract_otp
from app.storage.models import Job, OtpMessage

log = get_logger(__name__)

# Optional: restrict accepted senders — comma-separated partial strings.
# Example: "noreply@mysite.com,accounts@mysite.com"
_ALLOWED_SENDERS_RAW = os.environ.get("OTP_ALLOWED_SENDERS", "")
ALLOWED_SENDERS: list[str] = [
    s.strip().lower() for s in _ALLOWED_SENDERS_RAW.split(",") if s.strip()
]

# Optional: only accept emails whose subject matches this regex.
OTP_SUBJECT_PATTERN: str = os.environ.get("OTP_SUBJECT_PATTERN", "")

# How many seconds before job.created_at we still consider an email valid.
# Keep small to avoid picking up stale messages.
LOOKBACK_SECONDS: int = int(os.environ.get("OTP_LOOKBACK_SECONDS", "30"))


def _normalise_email(address: str) -> str:
    return address.strip().lower()


def _sender_allowed(sender: Optional[str]) -> bool:
    if not ALLOWED_SENDERS or not sender:
        return True
    sender_lower = sender.lower()
    return any(allowed in sender_lower for allowed in ALLOWED_SENDERS)


def _subject_matches(subject: Optional[str]) -> bool:
    if not OTP_SUBJECT_PATTERN or not subject:
        return True
    return bool(re.search(OTP_SUBJECT_PATTERN, subject, re.IGNORECASE))


def match_otp_message(
    job: Job,
    candidates: List[OtpMessage],
) -> Optional[OtpMessage]:
    """
    From *candidates* return the best-matching OTP message for *job*.

    Returns None if no suitable message is found.
    """
    target_email = _normalise_email(job.email)
    earliest = job.created_at - timedelta(seconds=LOOKBACK_SECONDS)

    scored: list[tuple[int, OtpMessage]] = []

    for msg in candidates:
        if msg.processed:
            continue

        # Must be addressed to the job's email.
        if not msg.recipient or _normalise_email(msg.recipient) != target_email:
            log.debug(
                "Skipping message %s — recipient %r != job email %r",
                msg.gmail_message_id, msg.recipient, target_email,
            )
            continue

        # Must arrive after the job was created (with small lookback tolerance).
        if msg.received_at:
            # Normalise to UTC.
            rcv = msg.received_at
            if rcv.tzinfo is None:
                rcv = rcv.replace(tzinfo=timezone.utc)
            if rcv < earliest.replace(tzinfo=timezone.utc):
                log.debug(
                    "Skipping message %s — received_at %s is before job window",
                    msg.gmail_message_id, rcv,
                )
                continue

        # Sender filter.
        if not _sender_allowed(msg.sender):
            log.debug(
                "Skipping message %s — sender %r not in allowed list",
                msg.gmail_message_id, msg.sender,
            )
            continue

        # Subject filter.
        if not _subject_matches(msg.subject):
            log.debug(
                "Skipping message %s — subject %r did not match pattern",
                msg.gmail_message_id, msg.subject,
            )
            continue

        # Assign a simple score (newer = higher).
        score = 0
        if msg.received_at:
            score = int(msg.received_at.timestamp())
        scored.append((score, msg))

    if not scored:
        return None

    # Pick the most recent qualifying message.
    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0][1]
    log.info(
        "Matched OTP message %s to job %s (email=%s)",
        best.gmail_message_id, job.job_id, job.email,
    )
    return best
