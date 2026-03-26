"""
Notification service — sends status updates back to Telegram.

Decoupled from the bot module so services can send messages without
importing the bot directly. The bot registers a callback at startup.
"""

from __future__ import annotations

from typing import Callable, Optional

from app.core.logger import get_logger
from app.storage.models import Job

log = get_logger(__name__)

# Type alias for the send function injected by the Telegram bot.
SendFn = Callable[[int, str], None]


class NotificationService:
    """Sends job lifecycle messages to Telegram."""

    def __init__(self, send_fn: Optional[SendFn] = None) -> None:
        # send_fn(chat_id, text) — injected by the bot at startup.
        self._send = send_fn

    def register_send_fn(self, fn: SendFn) -> None:
        self._send = fn

    def _send_message(self, job: Job, text: str) -> None:
        if not job.chat_id:
            log.debug("No chat_id for job %s — skipping notification", job.job_id)
            return
        if self._send is None:
            log.warning("NotificationService has no send_fn — cannot send message")
            return
        try:
            self._send(job.chat_id, text)
        except Exception as exc:
            log.error("Failed to send Telegram notification: %s", exc)

    def job_started(self, job: Job) -> None:
        self._send_message(
            job,
            f"⚙️ Job `{job.job_id}` started\n"
            f"📧 Creating account for `{job.email}` ...",
        )

    def waiting_for_otp(self, job: Job) -> None:
        self._send_message(
            job,
            f"⏳ Waiting for OTP email to `{job.email}` ...",
        )

    def otp_received(self, job: Job) -> None:
        self._send_message(
            job,
            f"✉️ OTP received — submitting to site ...",
        )

    def job_succeeded(self, job: Job) -> None:
        self._send_message(
            job,
            f"✅ Success! Account for `{job.email}` is verified and ready.",
        )

    def job_failed(self, job: Job, reason: str) -> None:
        self._send_message(
            job,
            f"❌ Job `{job.job_id}` failed\n"
            f"📧 Email: `{job.email}`\n"
            f"Reason: {reason}",
        )
