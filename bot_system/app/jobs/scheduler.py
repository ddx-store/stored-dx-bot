"""
Scheduler — runs registration jobs in background threads.

Each job gets its own thread so the Telegram bot handler returns
immediately without blocking. ThreadPoolExecutor caps concurrency.
If a job thread crashes unexpectedly, the user gets a notification.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict

from app.core.logger import get_logger
from app.storage.models import Job

log = get_logger(__name__)

_MAX_WORKERS = 5


class Scheduler:
    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reg-worker")
        self._futures: Dict[str, Future] = {}

    def submit(self, job: Job, password: str) -> None:
        from app.services.registration_service import RegistrationService

        service = RegistrationService()

        future = self._pool.submit(service.run_job, job, password)
        self._futures[job.job_id] = future
        log.info("Job %s submitted to scheduler", job.job_id)

        def _on_done(f: Future) -> None:
            exc = f.exception()
            if exc:
                log.error("Background job %s raised an uncaught exception: %s", job.job_id, exc)
                try:
                    from app.services.notification_service import NotificationService
                    NotificationService().step(job, "❌", f"خطأ غير متوقع: {exc}")
                except Exception as notify_exc:
                    log.error("Failed to notify user about crash: %s", notify_exc)
            self._futures.pop(job.job_id, None)

        future.add_done_callback(_on_done)

    def is_running(self, job_id: str) -> bool:
        f = self._futures.get(job_id)
        return f is not None and not f.done()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)


scheduler = Scheduler()
