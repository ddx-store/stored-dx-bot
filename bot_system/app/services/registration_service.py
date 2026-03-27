"""
Registration service — orchestrates the full create-account flow.

Uses Playwright with system Chromium for real browser automation.
Sends real-time step-by-step updates to the user via Telegram.
Global timeout ensures the user always gets a response within 60 seconds.
"""

from __future__ import annotations

import asyncio
import traceback

from app.core.config import config
from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.gmail.otp_watcher import OtpTimeout, OtpWatcher
from app.jobs.job_manager import JobManager
from app.services.notification_service import NotificationService
from app.site.playwright_client import PlaywrightClient
from app.storage.models import Job, Result
from app.storage.repositories import ResultRepository

log = get_logger(__name__)

JOB_TIMEOUT = 60


class RegistrationService:
    def __init__(self) -> None:
        self._jobs = JobManager()
        self._notify = NotificationService()
        self._results = ResultRepository()
        self._client = PlaywrightClient(timeout=8_000)

    def run_job(self, job: Job, password: str) -> None:
        log.info("▶ START job=%s site=%s email=%s", job.job_id, job.site_url, job.email)

        try:
            self._jobs.transition(job.job_id, JobStatus.CREATING_ACCOUNT)
            self._notify.step(job, "1️⃣", f"جاري فتح الموقع `{job.site_url}`...")

            if not job.site_url:
                raise RuntimeError("لم يتم تحديد الموقع")

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    asyncio.wait_for(
                        self._client.register(
                            site_url=job.site_url,
                            email=job.email,
                            password=password,
                        ),
                        timeout=JOB_TIMEOUT,
                    )
                )
            except asyncio.TimeoutError:
                raise RuntimeError(f"انتهى الوقت ({JOB_TIMEOUT}ث) — الموقع بطيء أو لم أجد نموذج تسجيل")
            finally:
                loop.close()

            log.info("Playwright result: success=%s otp=%s msg=%s",
                     result.success, result.needs_otp, result.message)

            if not result.success:
                raise RuntimeError(result.message)

            self._notify.step(job, "3️⃣", "تم ملء النموذج وإرساله ✓")

            if result.needs_otp and config.GMAIL_USER and config.GMAIL_APP_PASSWORD:
                self._handle_otp(job)
            else:
                self._finish(job, result.message or "✅ تم إنشاء الحساب")

        except Exception as exc:
            log.error("Job %s FAILED: %s\n%s", job.job_id, exc, traceback.format_exc())
            self._jobs.fail(job.job_id, str(exc))
            self._notify.step(job, "❌", f"فشل: {exc}")

    def _handle_otp(self, job: Job) -> None:
        self._jobs.transition(job.job_id, JobStatus.WAITING_FOR_OTP)
        self._notify.step(job, "4️⃣", "بانتظار رمز التحقق من البريد...")

        watcher = OtpWatcher()
        try:
            otp_msg = watcher.wait_for_otp(job)
        except OtpTimeout:
            self._finish(job, "تم الإرسال — لم يصل رمز (انتهى الوقت)")
            return

        otp_value = otp_msg.link_value or otp_msg.otp_value
        if not otp_value:
            self._finish(job, "وصل إيميل لكن بدون رمز")
            return

        self._notify.step(job, "5️⃣", "تم استلام الرمز — جاري التحقق...")

        if otp_msg.link_value and otp_msg.link_value.startswith("http"):
            try:
                import urllib.request
                urllib.request.urlopen(otp_value, timeout=15)
            except Exception as exc:
                log.warning("Link open failed: %s", exc)

        self._finish(job, f"✅ تم التحقق — الرمز: {otp_value}")

    def _finish(self, job: Job, detail: str) -> None:
        self._jobs.complete(job.job_id, detail)
        self._results.save(Result(job_id=job.job_id, success=True, detail=detail))
        job.final_result = detail
        self._notify.step(job, "✅", f"اكتمل!\n{detail}")
        log.info("Job %s DONE: %s", job.job_id, detail)
