"""
Persistent browser pool — keeps one Chromium process alive across all payment jobs.
Worker threads submit coroutines via submit() using run_coroutine_threadsafe.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import threading
from typing import Optional

from app.core.logger import get_logger

log = get_logger(__name__)

_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete navigator.__proto__.webdriver;
    window.navigator.chrome = {
        runtime: { onConnect: undefined, onMessage: undefined, id: undefined },
        loadTimes: function(){ return {}; },
        csi: function(){ return {}; },
        app: { isInstalled: false },
    };
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'ar'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    Object.defineProperty(document, 'hidden', { get: () => false });
    Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
}
"""

_LAUNCH_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions", "--disable-infobars",
    "--window-size=1920,1080",
]


class BrowserPool:
    """
    Manages a single persistent Chromium instance in a dedicated event loop thread.
    Worker threads interact via submit() which is thread-safe.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._browser = None
        self._pw = None
        self._ready = threading.Event()
        self._lock: Optional[asyncio.Lock] = None
        self._start()

    def _start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="browser-pool"
        )
        self._thread.start()
        ok = self._ready.wait(timeout=60)
        if not ok:
            log.error("BrowserPool: timed out waiting for browser to start")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self) -> None:
        self._lock = asyncio.Lock()
        await self._launch_browser()

    async def _launch_browser(self) -> None:
        try:
            from playwright.async_api import async_playwright
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
            self._pw = await async_playwright().start()
            chromium_path = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
            kwargs = {"headless": True, "args": _LAUNCH_ARGS}
            if chromium_path:
                kwargs["executable_path"] = chromium_path
            self._browser = await self._pw.chromium.launch(**kwargs)
            log.info("BrowserPool: Chromium launched (persistent)")
        except Exception as exc:
            log.error("BrowserPool: failed to launch browser: %s", exc)
            self._browser = None

    async def new_context(self, proxy_url: Optional[str] = None):
        """
        Create a new browser context (isolated session).
        Reopens the browser if it crashed.
        """
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                log.warning("BrowserPool: browser disconnected, relaunching...")
                await self._launch_browser()
            if self._browser is None:
                raise RuntimeError("BrowserPool: Chromium could not be started")

        ctx_args = dict(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/134.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            color_scheme="light",
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                "sec-ch-ua": '"Google Chrome";v="134", "Chromium";v="134"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        if proxy_url:
            from app.site.payment_client import _build_proxy_config
            ctx_args["proxy"] = _build_proxy_config(proxy_url)

        context = await self._browser.new_context(**ctx_args)
        await context.add_init_script(_STEALTH_JS)
        return context

    def submit(self, coro) -> "asyncio.Future":
        """
        Thread-safe: submit a coroutine to the pool's event loop.
        Returns a concurrent.futures.Future — call .result(timeout=N) to block.
        """
        if self._loop is None:
            raise RuntimeError("BrowserPool is not initialised")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def shutdown(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


browser_pool = BrowserPool()
