"""
Unit tests for the OTP matcher.
"""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("SITE_API_BASE_URL", "https://example.com/api/v1")

from app.core.enums import JobStatus, OtpType
from app.gmail.matcher import match_otp_message
from app.storage.models import Job, OtpMessage
from app.core.utils import utcnow


def _job(email: str) -> Job:
    return Job(
        job_id="testjob1",
        email=email,
        status=JobStatus.WAITING_FOR_OTP,
        created_at=utcnow(),
    )


def _msg(recipient: str, received_offset_secs: int = 5, processed: bool = False) -> OtpMessage:
    return OtpMessage(
        gmail_message_id=f"msg-{id(recipient)}-{received_offset_secs}",
        recipient=recipient,
        sender="noreply@example.com",
        subject="Your OTP Code",
        received_at=utcnow() + timedelta(seconds=received_offset_secs),
        otp_value="123456",
        otp_type=OtpType.NUMERIC,
        processed=processed,
    )


def test_match_correct_email():
    job = _job("user@example.com")
    msg = _msg("user@example.com")
    result = match_otp_message(job, [msg])
    assert result is not None
    assert result.recipient == "user@example.com"


def test_no_match_wrong_email():
    job = _job("user@example.com")
    msg = _msg("other@example.com")
    result = match_otp_message(job, [msg])
    assert result is None


def test_no_match_already_processed():
    job = _job("user@example.com")
    msg = _msg("user@example.com", processed=True)
    result = match_otp_message(job, [msg])
    assert result is None


def test_picks_most_recent():
    job = _job("user@example.com")
    old_msg = _msg("user@example.com", received_offset_secs=1)
    old_msg.gmail_message_id = "old"
    new_msg = _msg("user@example.com", received_offset_secs=10)
    new_msg.gmail_message_id = "new"
    result = match_otp_message(job, [old_msg, new_msg])
    assert result is not None
    assert result.gmail_message_id == "new"


def test_empty_candidates():
    job = _job("user@example.com")
    result = match_otp_message(job, [])
    assert result is None
