"""
Notification service — sends step-by-step updates back to Telegram.

All messages use plain text (no Markdown) to avoid silent failures
from special characters in URLs or Arabic text.
"""

from __future__ import annotations

from app.core.logger import get_logger
from app.storage.models import Job

log = get_logger(__name__)


class NotificationService:
    def step(self, job: Job, icon: str, message: str) -> None:
        if not job.chat_id:
            return
        text = f"{icon} Job {job.job_id}\n{message}"
        try:
            from app.bot.telegram_client import send_message
            send_message(job.chat_id, text)
        except Exception as exc:
            log.error("Notification failed for job %s: %s", job.job_id, exc)
