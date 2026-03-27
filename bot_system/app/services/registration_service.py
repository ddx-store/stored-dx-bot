from __future__ import annotations

import asyncio
import traceback
from concurrent.futures import ThreadPoolExecutor

from app.core.config import config
from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.gmail.otp_watcher import OtpTimeout, OtpWatcher
from app.jobs.job_manager import JobManager
from app.services.notification_service import NotificationService
from app.site.playwright_client import PlaywrightClient
from app.storage.models import Job, Result, SavedAccount
from app.storage.repositories import ResultRepository, SavedAccountRepository

log = get_logger(__name__)

JOB_TIMEOUT = 350


class RegistrationService:
    def __init__(self) -> None:
        self._jobs = JobManager()
        self._notify = NotificationService()
        self._results = ResultRepository()
        self._saved = SavedAccountRepository()

    def run_job(self, job: Job, password: str) -> None:
        log.info("START job=%s site=%s email=%s", job.job_id, job.site_url, job.email)

        try:
            from app.jobs.scheduler import scheduler
            if scheduler.is_cancelled(job.job_id):
                self._handle_cancel(job)
                return

            self._jobs.transition(job.job_id, JobStatus.CREATING_ACCOUNT)
            self._notify.step(job, "1\ufe0f\u20e3", "جاري فتح الموقع...")

            if not job.site_url:
                raise RuntimeError("لم يتم تحديد الموقع")

            def on_progress(msg: str):
                if scheduler.is_cancelled(job.job_id):
                    raise RuntimeError("__CANCELLED__")
                self._notify.step(job, "\U0001f504", msg)

            has_gmail = bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)

            otp_provider = None
            if has_gmail:
                otp_provider = self._make_otp_provider(job, on_progress)

            client = PlaywrightClient(timeout=8_000)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    asyncio.wait_for(
                        client.register(
                            site_url=job.site_url,
                            email=job.email,
                            password=password,
                            progress_callback=on_progress,
                            otp_provider=otp_provider,
                        ),
                        timeout=JOB_TIMEOUT,
                    )
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"انتهى الوقت ({JOB_TIMEOUT}ث) -- الموقع بطيء أو لم أجد نموذج تسجيل"
                )
            except RuntimeError as e:
                if "__CANCELLED__" in str(e):
                    self._handle_cancel(job)
                    return
                raise
            finally:
                loop.close()

            if scheduler.is_cancelled(job.job_id):
                self._handle_cancel(job)
                return

            log.info("Playwright result: success=%s otp=%s msg=%s",
                     result.success, result.needs_otp, result.message)

            if not result.success:
                raise RuntimeError(result.message)

            if result.needs_otp and not has_gmail:
                detail = "تم التسجيل -- يحتاج تحقق يدوي (Gmail غير مربوط)"
            else:
                detail = result.message or "تم إنشاء الحساب بنجاح"

            self._jobs.complete(job.job_id, detail)
            self._results.save(Result(job_id=job.job_id, success=True, detail=detail))
            job.final_result = detail
            self._notify.complete(job, detail)

            try:
                self._saved.save(SavedAccount(
                    chat_id=job.chat_id or 0,
                    site_url=job.site_url,
                    email=job.email,
                    password=password,
                    job_type="registration",
                    detail=detail,
                ))
            except Exception as save_exc:
                log.error("Failed to save account: %s", save_exc)

            log.info("Job %s DONE: %s", job.job_id, detail)

        except Exception as exc:
            msg = str(exc)[:200]
            log.error("Job %s FAILED: %s\n%s", job.job_id, msg, traceback.format_exc())
            try:
                self._jobs.fail(job.job_id, msg)
            except Exception:
                log.error("Failed to update job status for %s", job.job_id)
            try:
                self._notify.fail(job, msg)
            except Exception as notify_exc:
                log.error("CRITICAL: Could not notify user: %s", notify_exc)

    def _handle_cancel(self, job: Job) -> None:
        log.info("Job %s CANCELLED by user", job.job_id)
        try:
            self._jobs.transition(job.job_id, JobStatus.CANCELLED)
        except Exception:
            pass
        self._notify.fail(job, "تم إلغاء العملية بواسطة المستخدم")

    def _make_otp_provider(self, job: Job, on_progress):
        notify = self._notify
        jobs = self._jobs

        async def provider():
            jobs.transition(job.job_id, JobStatus.WAITING_FOR_OTP)
            notify.step(job, "4\ufe0f\u20e3", "بانتظار رمز التحقق من البريد...")

            loop = asyncio.get_event_loop()
            watcher = OtpWatcher()

            try:
                otp_msg = await loop.run_in_executor(
                    None, watcher.wait_for_otp, job
                )
            except OtpTimeout:
                log.warning("OTP timeout for job %s", job.job_id)
                return None

            otp_code = otp_msg.otp_value
            otp_link = otp_msg.link_value

            if not otp_code and not otp_link:
                log.warning("Email received but no OTP/link for job %s", job.job_id)
                return None

            notify.step(job, "5\ufe0f\u20e3", "تم استلام الرمز -- جاري التحقق...")
            return {"code": otp_code, "link": otp_link}

        return provider
