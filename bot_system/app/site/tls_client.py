"""
TLSClient — HTTP wrapper يُقلّد TLS fingerprint متصفح Chrome الحقيقي.
يستخدم curl_cffi إذا كانت متاحة (JA3/JA4 Chrome 134)،
ويرجع إلى aiohttp كـ fallback تلقائي بدون أي خطأ.

يُحل محل aiohttp في: captcha_solver.py و result_validator.py
مما يمنع Cloudflare/F5 من كشف أن الطلب من Python.
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from app.core.logger import get_logger

log = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tls-client")

try:
    import curl_cffi.requests as _cffi_requests
    _CFFI_AVAILABLE = True
    log.info("TLSClient: curl_cffi available — Chrome TLS fingerprint active")
except ImportError:
    _CFFI_AVAILABLE = False
    log.info("TLSClient: curl_cffi not available — using aiohttp fallback")


class TLSResponse:
    """Unified response object matching both curl_cffi and aiohttp."""
    def __init__(self, status: int, text: str, json_data: Any = None):
        self.status = status
        self._text = text
        self._json = json_data

    async def text(self) -> str:
        return self._text

    async def json(self, **_) -> Any:
        import json
        if self._json is not None:
            return self._json
        return json.loads(self._text)


class TLSClient:
    """
    Drop-in replacement for aiohttp با Chrome TLS fingerprint.
    استخدام:
        async with TLSClient() as client:
            resp = await client.get(url, params=..., headers=...)
            data = await resp.json()
    """

    CHROME_VERSION = "chrome134"

    def __init__(self, cookies: Optional[Dict] = None, headers: Optional[Dict] = None):
        self._cookies = cookies or {}
        self._headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url: str, params: Optional[Dict] = None,
                  headers: Optional[Dict] = None, timeout: int = 15) -> TLSResponse:
        merged_headers = {**self._headers, **(headers or {})}
        if _CFFI_AVAILABLE:
            return await self._cffi_request("GET", url, params=params,
                                             headers=merged_headers, timeout=timeout)
        return await self._aiohttp_request("GET", url, params=params,
                                           headers=merged_headers, timeout=timeout)

    async def post(self, url: str, json: Optional[Dict] = None,
                   params: Optional[Dict] = None, headers: Optional[Dict] = None,
                   timeout: int = 15) -> TLSResponse:
        merged_headers = {**self._headers, **(headers or {})}
        if _CFFI_AVAILABLE:
            return await self._cffi_request("POST", url, json=json, params=params,
                                             headers=merged_headers, timeout=timeout)
        return await self._aiohttp_request("POST", url, json=json, params=params,
                                           headers=merged_headers, timeout=timeout)

    async def _cffi_request(self, method: str, url: str, **kwargs) -> TLSResponse:
        """curl_cffi في thread منفصل (لأنها sync)."""
        timeout = kwargs.pop("timeout", 15)
        json_body = kwargs.pop("json", None)
        params = kwargs.pop("params", None)
        headers = kwargs.pop("headers", {})

        def _sync():
            session = _cffi_requests.Session(impersonate=self.CHROME_VERSION)
            if self._cookies:
                session.cookies.update(self._cookies)
            resp = session.request(
                method, url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            return resp.status_code, resp.text

        loop = asyncio.get_event_loop()
        status, text = await loop.run_in_executor(_executor, _sync)
        return TLSResponse(status=status, text=text)

    async def _aiohttp_request(self, method: str, url: str, **kwargs) -> TLSResponse:
        """aiohttp fallback."""
        import aiohttp
        timeout_val = kwargs.pop("timeout", 15)
        json_body = kwargs.pop("json", None)
        params = kwargs.pop("params", None)
        headers = kwargs.pop("headers", {})

        async with aiohttp.ClientSession(
            cookies=self._cookies, headers=headers
        ) as session:
            async with session.request(
                method, url,
                json=json_body,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout_val),
                allow_redirects=True,
            ) as resp:
                text = await resp.text()
                return TLSResponse(status=resp.status, text=text)
