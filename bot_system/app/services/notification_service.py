"""
Notification service — sends status updates back to Telegram.

Uses the global send function from telegram_client so all instances
share the same connection to the bot.
"""

from __future__ import annotations

from app.core.logger import get_logger
from app.storage.models import Job

log = get_logger(__name__)


class NotificationService:
    """Sends job lifecycle messages to Telegram."""

    def _send_message(self, job: Job, text: str) -> None:
        if not job.chat_id:
            log.debug("No chat_id for job %s — skipping notification", job.job_id)
            return
        try:
            from app.bot.telegram_client import send_message
            send_message(job.chat_id, text)
        except Exception as exc:
            log.error("Failed to send Telegram notification: %s", exc)

    def job_started(self, job: Job) -> None:
        site_info = f"\n🌐 الموقع: `{job.site_url}`" if job.site_url else ""
        self._send_message(
            job,
            f"⚙️ بدأ العمل على الطلب `{job.job_id}`\n"
            f"📧 إنشاء حساب لـ `{job.email}` ...{site_info}",
        )

    def waiting_for_otp(self, job: Job) -> None:
        self._send_message(
            job,
            f"⏳ بانتظار رمز التحقق على `{job.email}` ...",
        )

    def otp_received(self, job: Job) -> None:
        self._send_message(
            job,
            f"✉️ تم استلام الرمز — يتم التحقق الآن ...",
        )

    def job_succeeded(self, job: Job) -> None:
        detail = f"\n📝 {job.final_result}" if hasattr(job, "final_result") and job.final_result else ""
        self._send_message(
            job,
            f"✅ تم بنجاح! حساب `{job.email}` جاهز.{detail}",
        )

    def job_failed(self, job: Job, reason: str) -> None:
        self._send_message(
            job,
            f"❌ فشل الطلب `{job.job_id}`\n"
            f"📧 الإيميل: `{job.email}`\n"
            f"السبب: {reason}",
        )
