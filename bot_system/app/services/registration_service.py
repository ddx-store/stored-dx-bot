"""
Registration service — orchestrates the full create-account → OTP → verify flow.

This is the core business logic. It is called by the Telegram bot handler
and runs synchronously (one thread per job). For batch jobs, each job
gets its own thread via the scheduler.
"""

from __future__ import annotations

from app.core.config import config
from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.gmail.otp_watcher import OtpTimeout, OtpWatcher
from app.jobs.job_manager import JobManager
from app.services.notification_service import NotificationService
from app.site import get_site_integration
from app.site.base import DuplicateAccountError, SiteIntegrationError
from app.storage.models import Job
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
        self._site = get_site_integration()

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def run_job(self, job: Job, password: str) -> None:
        """
        Execute the full registration flow for *job*.

        This method is safe to call in a background thread.
        All exceptions are caught and translated into job failure.
        """
        log.info("Starting registration flow for job=%s email=%s", job.job_id, job.email)

        try:
            self._step_create_account(job, password)
            self._step_wait_for_otp(job)
            self._step_finalize(job)
        except Exception as exc:
            log.exception("Unhandled exception in job %s", job.job_id)
            self._jobs.fail(job.job_id, str(exc))
            self._notify.job_failed(job, str(exc))

    # ------------------------------------------------------------------ #
    # Internal steps
    # ------------------------------------------------------------------ #

    def _step_create_account(self, job: Job, password: str) -> None:
        self._jobs.transition(job.job_id, JobStatus.CREATING_ACCOUNT)
        self._notify.job_started(job)

        try:
            result = self._site.create_account(job.email, password)
            log.info(
                "create_account OK: job=%s msg=%s", job.job_id, result.message
            )
        except DuplicateAccountError as exc:
            raise SiteIntegrationError(
                f"Account already exists for {job.email}: {exc}"
            ) from exc
        except SiteIntegrationError:
            raise

        # Trigger OTP send (no-op on sites that send it automatically).
        try:
            self._site.request_otp(job.email)
        except SiteIntegrationError as exc:
            log.warning("request_otp failed (continuing anyway): %s", exc)

    def _step_wait_for_otp(self, job: Job) -> None:
        self._jobs.transition(job.job_id, JobStatus.WAITING_FOR_OTP)
        self._notify.waiting_for_otp(job)

        try:
            otp_msg = self._watcher.wait_for_otp(job)
        except OtpTimeout as exc:
            raise SiteIntegrationError(str(exc)) from exc

        # Determine what to submit: activation link or numeric code.
        if otp_msg.link_value:
            otp_value = otp_msg.link_value
            log.info("OTP is an activation link for job=%s", job.job_id)
        elif otp_msg.otp_value:
            otp_value = otp_msg.otp_value
            log.info(
                "OTP code %s received for job=%s", otp_value, job.job_id
            )
        else:
            raise SiteIntegrationError(
                f"OTP email received but no code/link could be extracted for job {job.job_id}"
            )

        self._notify.otp_received(job)
        self._jobs.transition(job.job_id, JobStatus.VERIFYING_OTP)

        attempts = 0
        last_error: str = ""

        while attempts < config.OTP_MAX_ATTEMPTS:
            attempts = self._jobs.increment_otp_attempts(job.job_id)
            try:
                result = self._site.submit_otp(job.email, otp_value)
                log.info(
                    "OTP verified for job=%s attempt=%d msg=%s",
                    job.job_id, attempts, result.message,
                )
                return  # success
            except SiteIntegrationError as exc:
                last_error = str(exc)
                log.warning(
                    "OTP attempt %d/%d failed for job=%s: %s",
                    attempts, config.OTP_MAX_ATTEMPTS, job.job_id, exc,
                )

        raise SiteIntegrationError(
            f"OTP verification failed after {attempts} attempts: {last_error}"
        )

    def _step_finalize(self, job: Job) -> None:
        try:
            result = self._site.finalize_account(job.email)
            log.info("finalize_account for job=%s: %s", job.job_id, result.message)
        except SiteIntegrationError as exc:
            log.warning("finalize_account error (non-fatal): %s", exc)

        self._jobs.complete(job.job_id, "Registration complete")
        from app.storage.models import Result
        self._results.save(Result(job_id=job.job_id, success=True, detail="Registration complete"))
        self._notify.job_succeeded(job)
