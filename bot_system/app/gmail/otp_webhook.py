"""
OTPWebhookServer — استقبال OTP فوري عبر HTTP بدلاً من IMAP Polling.
يعمل بجانب otp_watcher.py كـ optional fast path.

كيفية الاستخدام:
1. شغّل الخادم (يبدأ تلقائياً مع البوت إذا تم ضبط WEBHOOK_OTP_PORT)
2. أعدّ Gmail Filter لإعادة توجيه رسائل OTP إلى:
   POST http://YOUR_SERVER:PORT/otp
   الـ body: {"to": "user@gmail.com", "body": "Your code is 123456"}

3. أو استخدم Zapier/Make لإرسال الرسائل إلى هذا الـ endpoint

يُقلّل زمن انتظار OTP من 10-30 ثانية إلى أقل من ثانية.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Dict, Optional

from app.core.logger import get_logger

log = get_logger(__name__)


class OTPWebhookServer:
    """
    خادم HTTP خفيف يستقبل OTP عبر webhook ويُوقظ الـ jobs المنتظرة.
    """

    def __init__(self, port: int = 8765) -> None:
        self._port = port
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._started = False

    def start_in_background(self, loop: asyncio.AbstractEventLoop) -> None:
        """يبدأ الخادم في الـ event loop الموجود."""
        self._loop = loop
        asyncio.run_coroutine_threadsafe(self._start(), loop)

    async def _start(self) -> None:
        try:
            from aiohttp import web
        except ImportError:
            log.warning("OTPWebhook: aiohttp not available, skipping")
            return

        from aiohttp import web

        app = web.Application()
        app.router.add_post("/otp", self._handle_otp)
        app.router.add_get("/health", self._handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._port)
        await site.start()
        self._started = True
        log.info("OTPWebhook: listening on port %d", self._port)

    async def _handle_health(self, request) -> "web.Response":
        from aiohttp import web
        return web.json_response({"status": "ok", "pending": len(self._pending)})

    async def _handle_otp(self, request) -> "web.Response":
        from aiohttp import web
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        to_email = (data.get("to") or "").lower().strip()
        body = data.get("body") or data.get("text") or ""
        subject = data.get("subject") or ""

        if not to_email or not body:
            return web.Response(status=400, text="Missing 'to' or 'body'")

        code = self._extract_code(body + " " + subject)
        if not code:
            log.debug("OTPWebhook: no code found in payload for %s", to_email[:6])
            return web.Response(status=200, text="no_code")

        log.info("OTPWebhook: received OTP for %s (code length=%d)", to_email[:6], len(code))

        with self._lock:
            future = self._pending.get(to_email)

        if future and not future.done():
            self._loop.call_soon_threadsafe(future.set_result, code)
            return web.Response(status=200, text="delivered")

        log.debug("OTPWebhook: no pending job for %s", to_email[:6])
        return web.Response(status=200, text="no_pending")

    def _extract_code(self, text: str) -> Optional[str]:
        """استخراج كود OTP من نص الرسالة."""
        import re
        patterns = [
            r'\b(\d{6})\b',
            r'\b(\d{4})\b',
            r'code[:\s]+(\d{4,8})',
            r'(\d{4,8})\s+is your',
            r'verification code[:\s]+(\d{4,8})',
            r'OTP[:\s]+(\d{4,8})',
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    async def wait_for_otp(self, email: str, timeout: int = 90) -> Optional[str]:
        """
        ينتظر OTP لبريد محدد.
        يُستخدم كـ fast path قبل IMAP polling.
        """
        if not self._started:
            return None

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        with self._lock:
            self._pending[email.lower()] = future

        try:
            code = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            log.info("OTPWebhook: delivered code for %s in fast path", email[:6])
            return code
        except asyncio.TimeoutError:
            return None
        finally:
            with self._lock:
                self._pending.pop(email.lower(), None)
            if not future.done():
                future.cancel()

    def register_pending(self, email: str) -> None:
        """سجّل بريد كـ pending دون انتظار فوري."""
        if not self._started or not self._loop:
            return
        loop = self._loop
        future = loop.create_future()
        with self._lock:
            self._pending[email.lower()] = future

    def is_running(self) -> bool:
        return self._started


otp_webhook = OTPWebhookServer()
