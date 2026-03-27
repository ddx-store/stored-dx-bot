"""
Registration service — orchestrates the full create-account → OTP → verify flow.

Uses Playwright to automate account creation on any website.
Each job runs in a background thread; asyncio.run() is used to
drive async Playwright calls inside that thread.
"""

from __future__ import annotations

import asyncio

from app.core.config import config
from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.gmail.otp_watcher import OtpTimeout, OtpWatcher
from app.jobs.job_manager import JobManager
from app.services.notification_service import NotificationService
from app.site.playwright_client import PlaywrightClient, RegistrationResult
from app.storage.models import Job, Result
from app.storage.repositories import ResultRepository

log = get_logger(__name__)


class RegistrationService:
    def __init__(
        self,
        job_manager: JobManager | None = None,
        notification: NotificationService | None = None,
        otp_watcher: OtpWatcher | None = None,
    ) -> None:
        self._jobs = job_manager or JobManager()
        self._notify = notification or NotificationService()
        self._watcher = otp_watcher or OtpWatcher()
        self._results = ResultRepository()
        self._browser = PlaywrightClient(timeout=30_000)

    # ------------------------------------------------------------------ #
    # Public entry point (called from background thread by Scheduler)
    # ------------------------------------------------------------------ #

    def run_job(self, job: Job, password: str) -> None:
        """
        Execute the full registration flow for *job*.
        Safe to call in a background thread.
        """
        log.info(
            "Starting registration flow: job=%s email=%s site=%s",
            job.job_id, job.email, job.site_url,
        )

        try:
            self._jobs.transition(job.job_id, JobStatus.CREATING_ACCOUNT)
            self._notify.job_started(job)

            # ── Step 1: Open the site and fill registration form ──────────
            if not job.site_url:
                raise RuntimeError(
                    "No site URL provided. Use: /create site.com email@example.com"
                )

            reg_result: RegistrationResult = asyncio.run(
                self._browser.register(
                    site_url=job.site_url,
                    email=job.email,
                    password=password,
                )
            )

            if not reg_result.success:
                raise RuntimeError(f"Registration failed: {reg_result.message}")

            log.info("Registration submitted for job=%s: %s", job.job_id, reg_result.message)

            # ── Step 2: If OTP is needed, wait for it via Gmail ───────────
            if reg_result.needs_otp or self._site_might_send_otp(job):
                self._step_wait_for_otp(job)
            else:
                # No OTP needed — mark complete
                self._finish(job, reg_result.message)

        except Exception as exc:
            log.exception("Unhandled exception in job %s: %s", job.job_id, exc)
            self._jobs.fail(job.job_id, str(exc))
            self._notify.job_failed(job, str(exc))

    # ------------------------------------------------------------------ #
    # Internal steps
    # ------------------------------------------------------------------ #

    def _site_might_send_otp(self, job: Job) -> bool:
        """
        Heuristic: if Gmail credentials are configured, we can
        attempt to wait for an OTP even if the browser didn't detect one.
        This handles sites that redirect before the OTP page is shown.
        """
        return bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)

    def _step_wait_for_otp(self, job: Job) -> None:
        self._jobs.transition(job.job_id, JobStatus.WAITING_FOR_OTP)
        self._notify.waiting_for_otp(job)

        try:
            otp_msg = self._watcher.wait_for_otp(job)
        except OtpTimeout as exc:
            # OTP timed out — still mark job done (form was submitted)
            log.warning("OTP timeout for job=%s — treating as complete: %s", job.job_id, exc)
            self._finish(job, "تم إرسال النموذج، لم يصل رمز التحقق (انتهى الوقت).")
            return

        # Determine OTP value
        if otp_msg.link_value:
            otp_value = otp_msg.link_value
            log.info("OTP is an activation link for job=%s", job.job_id)
        elif otp_msg.otp_value:
            otp_value = otp_msg.otp_value
            log.info("OTP code %s received for job=%s", otp_value, job.job_id)
        else:
            self._finish(job, "وصل إيميل لكن لم يُستخرج رمز أو رابط.")
            return

        self._notify.otp_received(job)
        log.info("OTP for job=%s: %s", job.job_id, otp_value)

        # If it's a link, open it directly
        if otp_msg.link_value and otp_msg.link_value.startswith("http"):
            try:
                import urllib.request
                urllib.request.urlopen(otp_value, timeout=15)
                log.info("Activation link opened for job=%s", job.job_id)
            except Exception as exc:
                log.warning("Could not open activation link: %s", exc)

        self._finish(job, f"✅ تم التحقق — الرمز: {otp_value}")

    def _finish(self, job: Job, detail: str) -> None:
        self._jobs.complete(job.job_id, detail)
        self._results.save(Result(job_id=job.job_id, success=True, detail=detail))
        self._notify.job_succeeded(job)
        log.info("Job %s completed: %s", job.job_id, detail)
