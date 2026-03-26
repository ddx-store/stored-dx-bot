"""
Job manager — creates, retrieves, and updates jobs.
Acts as the single point of truth for job state transitions.
"""

from __future__ import annotations

from typing import List, Optional

from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.core.utils import new_job_id, utcnow
from app.storage.db import get_connection
from app.storage.models import Job
from app.storage.repositories import AuditRepository, JobRepository

log = get_logger(__name__)


class JobManager:
    def __init__(self) -> None:
        self._conn = get_connection()
        self._jobs = JobRepository(self._conn)
        self._audit = AuditRepository(self._conn)

    def create_job(
        self,
        email: str,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Job:
        job = Job(
            job_id=new_job_id(),
            email=email,
            status=JobStatus.PENDING,
            created_at=utcnow(),
            updated_at=utcnow(),
            chat_id=chat_id,
            message_id=message_id,
        )
        self._jobs.create(job)
        self._audit.log("job_created", f"email={email}", job.job_id)
        log.info("Job created: job_id=%s email=%s", job.job_id, email)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_by_email(self, email: str) -> Optional[Job]:
        return self._jobs.get_by_email(email)

    def list_recent(self, limit: int = 10) -> List[Job]:
        return self._jobs.list_recent(limit)

    def transition(
        self,
        job_id: str,
        new_status: JobStatus,
        error_msg: Optional[str] = None,
        final_result: Optional[str] = None,
    ) -> None:
        self._jobs.update_status(job_id, new_status, error_msg, final_result)
        self._audit.log(
            "status_change",
            f"new_status={new_status.value} error={error_msg}",
            job_id,
        )
        log.info("Job %s → %s", job_id, new_status.value)

    def increment_otp_attempts(self, job_id: str) -> int:
        count = self._jobs.increment_otp_attempts(job_id)
        self._audit.log("otp_attempt", f"attempt={count}", job_id)
        return count

    def fail(self, job_id: str, reason: str) -> None:
        self.transition(job_id, JobStatus.FAILED, error_msg=reason)
        log.error("Job %s failed: %s", job_id, reason)

    def complete(self, job_id: str, detail: Optional[str] = None) -> None:
        self.transition(job_id, JobStatus.COMPLETED, final_result=detail)
        log.info("Job %s completed: %s", job_id, detail)
