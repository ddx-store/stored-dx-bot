from __future__ import annotations

import asyncio
import traceback

from urllib.parse import urlparse

from app.core.enums import JobStatus
from app.core.logger import get_logger
from app.core.secure_logger import secure_logger
from app.services.notification_service import NotificationService
from app.site.payment_client import PaymentClient
from app.site.proxy_scorer import proxy_scorer
from app.storage.models import CardInfo, PaymentJob, Result, SavedAccount
from app.storage.repositories import ProxyRepository, ResultRepository, SavedAccountRepository

log = get_logger(__name__)

PAYMENT_JOB_TIMEOUT = 350


class PaymentService:
    def __init__(self) -> None:
        self._notify = NotificationService()
        self._results = ResultRepository()
        self._saved = SavedAccountRepository()
        self._proxies = ProxyRepository()

    def run_job(self, pjob: PaymentJob, card: CardInfo) -> None:
        log.info("START payment job=%s site=%s email=%s", pjob.job_id, pjob.site_url, pjob.email)

        job_proxy = _PaymentJobProxy(pjob)

        proxy_url: str | None = None
        active_proxy_id: int | None = None
        domain = urlparse(pjob.site_url).netloc.replace("www.", "")
        _start_time = __import__("time").monotonic()
        try:
            all_proxies = self._proxies.get_all_active()
            p = proxy_scorer.pick_best(all_proxies, domain) if all_proxies else self._proxies.get_random_active()
            if p:
                proxy_url = p.proxy_url
                active_proxy_id = p.id
                log.info("Payment job %s using proxy#%d: %s", pjob.job_id, p.id, p.label or proxy_url[:40])
        except Exception as pe:
            log.warning("Could not fetch proxy: %s", pe)

        try:
            from app.jobs.scheduler import scheduler
            if scheduler.is_cancelled(pjob.job_id):
                self._handle_cancel(pjob, job_proxy)
                return

            self._notify.step(job_proxy, "1\ufe0f\u20e3", "جاري فتح الموقع للدفع...", is_payment=True)

            def on_progress(msg: str):
                if scheduler.is_cancelled(pjob.job_id):
                    raise RuntimeError("__CANCELLED__")
                self._notify.step(job_proxy, "\U0001f504", msg, is_payment=True)

            client = PaymentClient(timeout=8_000)

            try:
                from app.site.browser_pool import browser_pool
                future = browser_pool.submit(
                    client.pay_with_pool(
                        browser_pool,
                        site_url=pjob.site_url,
                        email=pjob.email,
                        password=pjob.password,
                        card_number=card.number,
                        card_expiry_month=card.expiry_month,
                        card_expiry_year=card.expiry_year,
                        card_cvv=card.cvv,
                        card_holder=card.holder_name,
                        plan_name=pjob.plan_name,
                        billing_zip=card.billing_zip,
                        billing_country=card.billing_country,
                        proxy_url=proxy_url,
                        progress_callback=on_progress,
                        job_id=pjob.job_id,
                    )
                )
                result = future.result(timeout=PAYMENT_JOB_TIMEOUT)
            except Exception as pool_exc:
                log.warning("Browser pool error, falling back to standalone browser: %s", pool_exc)
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
                                billing_zip=card.billing_zip,
                                billing_country=card.billing_country,
                                proxy_url=proxy_url,
                                progress_callback=on_progress,
                                job_id=pjob.job_id,
                            ),
                            timeout=PAYMENT_JOB_TIMEOUT,
                        )
                    )
                finally:
                    loop.close()

            if scheduler.is_cancelled(pjob.job_id):
                self._handle_cancel(pjob, job_proxy)
                return

            log.info("Payment result: success=%s msg=%s", result.success, result.message)

            # Record proxy performance for future scoring
            if active_proxy_id is not None:
                latency_ms = (__import__("time").monotonic() - _start_time) * 1000
                proxy_scorer.record_result(
                    active_proxy_id, domain, result.success, latency_ms
                )
            secure_logger.log_payment(
                pjob.email, pjob.card_last4 or "????", domain,
                "SUCCESS" if result.success else "FAIL"
            )

            if not result.success:
                # Feed throttler for bulk jobs
                if getattr(pjob, "is_bulk", False):
                    try:
                        from app.core.throttler import bulk_throttler
                        bulk_throttler.record_failure()
                    except Exception:
                        pass
                raise RuntimeError(result.message)

            # Feed throttler on success for bulk jobs
            if getattr(pjob, "is_bulk", False):
                try:
                    from app.core.throttler import bulk_throttler
                    bulk_throttler.record_success()
                except Exception:
                    pass

            detail = result.message or "تم الدفع بنجاح"
            pjob.final_result = detail
            pjob.status = JobStatus.COMPLETED
            self._notify.complete(job_proxy, detail)

            try:
                self._saved.save(SavedAccount(
                    chat_id=pjob.chat_id or 0,
                    site_url=pjob.site_url,
                    email=pjob.email,
                    password=pjob.password,
                    job_type="payment",
                    plan_name=pjob.plan_name or "",
                    detail=detail,
                ))
            except Exception as save_exc:
                log.error("Failed to save payment account: %s", save_exc)

            log.info("Payment job %s DONE: %s", pjob.job_id, detail)

        except Exception as exc:
            if "__CANCELLED__" in str(exc):
                self._handle_cancel(pjob, job_proxy)
                return
            msg = str(exc)[:200]
            log.error("Payment job %s FAILED: %s\n%s", pjob.job_id, msg, traceback.format_exc())
            pjob.status = JobStatus.FAILED
            pjob.error_msg = msg
            try:
                self._notify.fail(job_proxy, msg)
            except Exception as notify_exc:
                log.error("CRITICAL: Could not notify user about payment failure: %s", notify_exc)

    def _handle_cancel(self, pjob: PaymentJob, proxy) -> None:
        log.info("Payment job %s CANCELLED by user", pjob.job_id)
        pjob.status = JobStatus.CANCELLED
        self._notify.fail(proxy, "تم إلغاء العملية بواسطة المستخدم")


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
