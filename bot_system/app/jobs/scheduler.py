from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, Optional, Set

from app.core.config import config
from app.core.logger import get_logger
from app.storage.models import CardInfo, Job, PaymentJob

log = get_logger(__name__)

_MAX_WORKERS = 5


class Scheduler:
    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reg-worker")
        self._futures: Dict[str, Future] = {}
        self._job_chat: Dict[str, int] = {}
        self._cancelled: Set[str] = set()
        self._lock = threading.Lock()

    def active_count_for_chat(self, chat_id: int) -> int:
        with self._lock:
            count = 0
            for job_id, cid in self._job_chat.items():
                if cid == chat_id and job_id in self._futures:
                    f = self._futures[job_id]
                    if not f.done():
                        count += 1
            return count

    def is_at_limit(self, chat_id: int) -> bool:
        return self.active_count_for_chat(chat_id) >= config.MAX_CONCURRENT_JOBS

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            f = self._futures.get(job_id)
            if f is None or f.done():
                return False
            self._cancelled.add(job_id)
            f.cancel()
            return True

    def get_active_jobs_for_chat(self, chat_id: int) -> list:
        with self._lock:
            result = []
            for job_id, cid in self._job_chat.items():
                if cid == chat_id and job_id in self._futures:
                    f = self._futures[job_id]
                    if not f.done():
                        result.append(job_id)
            return result

    def submit(self, job: Job, password: str) -> None:
        from app.services.registration_service import RegistrationService

        service = RegistrationService()

        future = self._pool.submit(service.run_job, job, password)
        with self._lock:
            self._futures[job.job_id] = future
            if job.chat_id:
                self._job_chat[job.job_id] = job.chat_id
        log.info("Job %s submitted to scheduler", job.job_id)

        def _on_done(f: Future) -> None:
            exc = f.exception()
            if exc:
                log.error("Background job %s raised an uncaught exception: %s", job.job_id, exc)
                try:
                    from app.services.notification_service import NotificationService
                    NotificationService().fail(job, f"خطأ غير متوقع: {exc}")
                except Exception as notify_exc:
                    log.error("Failed to notify user about crash: %s", notify_exc)
                _notify_admin(job.job_id, job.site_url, job.email, str(exc))
            with self._lock:
                self._futures.pop(job.job_id, None)
                self._job_chat.pop(job.job_id, None)
                self._cancelled.discard(job.job_id)

        future.add_done_callback(_on_done)

    def submit_payment(self, pjob: PaymentJob, card: CardInfo) -> None:
        from app.services.payment_service import PaymentService

        service = PaymentService()

        future = self._pool.submit(service.run_job, pjob, card)
        with self._lock:
            self._futures[pjob.job_id] = future
            if pjob.chat_id:
                self._job_chat[pjob.job_id] = pjob.chat_id
        log.info("Payment job %s submitted to scheduler", pjob.job_id)

        def _on_done(f: Future) -> None:
            exc = f.exception()
            if exc:
                log.error("Background payment job %s raised an uncaught exception: %s", pjob.job_id, exc)
                try:
                    from app.services.notification_service import NotificationService
                    NotificationService().fail(pjob, f"خطأ غير متوقع: {exc}")
                except Exception as notify_exc:
                    log.error("Failed to notify user about payment crash: %s", notify_exc)
                _notify_admin(pjob.job_id, pjob.site_url, pjob.email, str(exc))
            with self._lock:
                self._futures.pop(pjob.job_id, None)
                self._job_chat.pop(pjob.job_id, None)
                self._cancelled.discard(pjob.job_id)

        future.add_done_callback(_on_done)

    def is_running(self, job_id: str) -> bool:
        f = self._futures.get(job_id)
        return f is not None and not f.done()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)


scheduler = Scheduler()


def _notify_admin(job_id: str, site_url: str, email: str, error: str) -> None:
    if not config.ADMIN_CHAT_ID:
        return
    try:
        from app.bot.telegram_client import send_message
        text = (
            "⚠️ تنبيه أدمن ⚠️\n"
            "─────────────────\n"
            f"عملية فشلت: {job_id}\n"
            f"الموقع: {site_url}\n"
            f"الايميل: {email}\n"
            f"الخطأ: {error[:300]}\n"
        )
        send_message(config.ADMIN_CHAT_ID, text)
    except Exception as exc:
        log.error("Failed to notify admin: %s", exc)
