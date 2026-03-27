from __future__ import annotations

import asyncio
import traceback

from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.services.notification_service import NotificationService
from app.site.payment_client import PaymentClient
from app.storage.models import CardInfo, PaymentJob, Result
from app.storage.repositories import ResultRepository

log = get_logger(__name__)

PAYMENT_JOB_TIMEOUT = 350


class PaymentService:
    def __init__(self) -> None:
        self._notify = NotificationService()
        self._results = ResultRepository()

    def run_job(self, pjob: PaymentJob, card: CardInfo) -> None:
        log.info("START payment job=%s site=%s email=%s", pjob.job_id, pjob.site_url, pjob.email)

        job_proxy = _PaymentJobProxy(pjob)

        try:
            self._notify.step(job_proxy, "1️⃣", "جاري فتح الموقع للدفع...", is_payment=True)

            def on_progress(msg: str):
                self._notify.step(job_proxy, "🔄", msg, is_payment=True)

            client = PaymentClient(timeout=8_000)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    asyncio.wait_for(
                        client.pay(
                            site_url=pjob.site_url,
                            email=pjob.email,
                            password=pjob.password,
                            card_number=card.number,
                            card_expiry_month=card.expiry_month,
                            card_expiry_year=card.expiry_year,
                            card_cvv=card.cvv,
                            card_holder=card.holder_name,
                            plan_name=pjob.plan_name,
                            progress_callback=on_progress,
                        ),
                        timeout=PAYMENT_JOB_TIMEOUT,
                    )
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"انتهى الوقت ({PAYMENT_JOB_TIMEOUT}ث) -- الموقع بطيء أو صفحة الدفع غير موجودة"
                )
            finally:
                loop.close()

            log.info("Payment result: success=%s msg=%s", result.success, result.message)

            if not result.success:
                raise RuntimeError(result.message)

            detail = result.message or "تم الدفع بنجاح"
            pjob.final_result = detail
            pjob.status = JobStatus.COMPLETED
            self._notify.complete(job_proxy, detail)
            log.info("Payment job %s DONE: %s", pjob.job_id, detail)

        except Exception as exc:
            msg = str(exc)[:200]
            log.error("Payment job %s FAILED: %s\n%s", pjob.job_id, msg, traceback.format_exc())
            pjob.status = JobStatus.FAILED
            pjob.error_msg = msg
            try:
                self._notify.fail(job_proxy, msg)
            except Exception as notify_exc:
                log.error("CRITICAL: Could not notify user about payment failure: %s", notify_exc)


class _PaymentJobProxy:
    def __init__(self, pjob: PaymentJob):
        self.job_id = pjob.job_id
        self.email = pjob.email
        self.site_url = pjob.site_url
        self.chat_id = pjob.chat_id
        self.message_id = pjob.message_id
        self.status = pjob.status
        self.final_result = pjob.final_result
        self.error_msg = pjob.error_msg
