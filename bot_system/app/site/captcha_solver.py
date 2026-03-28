"""
CaptchaSolver — automatic CAPTCHA detection and solving.
Detects: hCaptcha, reCaptcha v2/v3, Cloudflare Turnstile.
Solvers: CapMonster, 2Captcha, AntiCaptcha (priority order, fallback chain).
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

from app.core.logger import get_logger

log = get_logger(__name__)

_CAPMONSTER_URL = "https://api.capmonster.cloud"
_2CAPTCHA_URL = "https://2captcha.com/in.php"
_ANTICAPTCHA_URL = "https://api.anti-captcha.com"

_POLL_INTERVAL = 5
_MAX_POLLS = 24  # 2 min max


class CaptchaSolver:

    def __init__(self) -> None:
        self._capmonster_key = os.environ.get("CAPMONSTER_API_KEY", "")
        self._2captcha_key = os.environ.get("TWOCAPTCHA_API_KEY", "")
        self._anticaptcha_key = os.environ.get("ANTICAPTCHA_API_KEY", "")

    @property
    def _any_solver_configured(self) -> bool:
        return bool(self._capmonster_key or self._2captcha_key or self._anticaptcha_key)

    async def solve_if_present(self, page) -> bool:
        """
        Detect and solve CAPTCHA on page if present.
        Returns True if no CAPTCHA found OR CAPTCHA solved.
        Returns False if CAPTCHA detected but solving failed.
        """
        captcha_type, site_key = await self._detect_captcha(page)
        if not captcha_type:
            return True

        log.info("CAPTCHA detected: type=%s sitekey=%s", captcha_type, site_key[:20] if site_key else "?")

        if not self._any_solver_configured:
            log.warning("CAPTCHA detected but no solver API key configured")
            return False

        page_url = page.url
        token = await self._solve(captcha_type, site_key or "", page_url)
        if not token:
            log.warning("CAPTCHA solving failed for type=%s", captcha_type)
            return False

        success = await self._inject_token(page, captcha_type, token)
        if success:
            log.info("CAPTCHA token injected successfully")
        return success

    async def _detect_captcha(self, page) -> tuple[Optional[str], Optional[str]]:
        """Returns (captcha_type, site_key) or (None, None)."""
        for frame in page.frames:
            url = frame.url or ""
            if "hcaptcha.com/captcha" in url:
                site_key = await self._extract_hcaptcha_key(page)
                return "hcaptcha", site_key
            if "recaptcha/api2" in url or "recaptcha.net" in url:
                site_key = await self._extract_recaptcha_key(page)
                return "recaptcha_v2", site_key
            if "challenges.cloudflare.com" in url:
                site_key = await self._extract_turnstile_key(page)
                return "turnstile", site_key

        # Body text detection fallback
        try:
            body = (await page.inner_text("body"))[:1000].lower()
            if "hcaptcha" in body or "h-captcha" in body:
                site_key = await self._extract_hcaptcha_key(page)
                return "hcaptcha", site_key
            if "recaptcha" in body:
                site_key = await self._extract_recaptcha_key(page)
                return "recaptcha_v2", site_key
        except Exception:
            pass

        return None, None

    async def _extract_hcaptcha_key(self, page) -> Optional[str]:
        try:
            key = await page.evaluate(
                "() => {"
                "  const el = document.querySelector('[data-sitekey]');"
                "  return el ? el.getAttribute('data-sitekey') : null;"
                "}"
            )
            return key
        except Exception:
            return None

    async def _extract_recaptcha_key(self, page) -> Optional[str]:
        try:
            key = await page.evaluate(
                "() => {"
                "  const el = document.querySelector('.g-recaptcha, [data-sitekey]');"
                "  if (el) return el.getAttribute('data-sitekey');"
                "  const match = document.documentElement.innerHTML.match(/['\"]sitekey['\"]:?\\s*['\"]([^'\"]+)/);"
                "  return match ? match[1] : null;"
                "}"
            )
            return key
        except Exception:
            return None

    async def _extract_turnstile_key(self, page) -> Optional[str]:
        try:
            key = await page.evaluate(
                "() => {"
                "  const el = document.querySelector('.cf-turnstile, [data-sitekey]');"
                "  return el ? el.getAttribute('data-sitekey') : null;"
                "}"
            )
            return key
        except Exception:
            return None

    async def _solve(self, captcha_type: str, site_key: str, page_url: str) -> Optional[str]:
        """Try solvers in priority order."""
        solvers = []
        if self._capmonster_key:
            solvers.append(("capmonster", self._solve_capmonster))
        if self._2captcha_key:
            solvers.append(("2captcha", self._solve_2captcha))
        if self._anticaptcha_key:
            solvers.append(("anticaptcha", self._solve_anticaptcha))

        for name, solver_fn in solvers:
            try:
                log.info("Trying CAPTCHA solver: %s", name)
                token = await solver_fn(captcha_type, site_key, page_url)
                if token:
                    return token
            except Exception as exc:
                log.warning("Solver %s failed: %s", name, exc)
        return None

    async def _solve_capmonster(self, captcha_type: str, site_key: str, page_url: str) -> Optional[str]:
        from app.site.tls_client import TLSClient
        task_map = {
            "hcaptcha": "HCaptchaTaskProxyless",
            "recaptcha_v2": "NoCaptchaTaskProxyless",
            "turnstile": "TurnstileTaskProxyless",
        }
        task_type = task_map.get(captcha_type, "HCaptchaTaskProxyless")
        payload = {
            "clientKey": self._capmonster_key,
            "task": {"type": task_type, "websiteURL": page_url, "websiteKey": site_key},
        }
        async with TLSClient() as client:
            r = await client.post(f"{_CAPMONSTER_URL}/createTask", json=payload, timeout=15)
            data = await r.json()
            task_id = data.get("taskId")
            if not task_id:
                return None
            for _ in range(_MAX_POLLS):
                await asyncio.sleep(_POLL_INTERVAL)
                r2 = await client.post(
                    f"{_CAPMONSTER_URL}/getTaskResult",
                    json={"clientKey": self._capmonster_key, "taskId": task_id},
                    timeout=10,
                )
                result = await r2.json()
                if result.get("status") == "ready":
                    return result.get("solution", {}).get("gRecaptchaResponse") or result.get("solution", {}).get("token")
        return None

    async def _solve_2captcha(self, captcha_type: str, site_key: str, page_url: str) -> Optional[str]:
        from app.site.tls_client import TLSClient
        method_map = {"hcaptcha": "hcaptcha", "recaptcha_v2": "userrecaptcha", "turnstile": "turnstile"}
        method = method_map.get(captcha_type, "hcaptcha")
        params = {"key": self._2captcha_key, "method": method, "sitekey": site_key, "pageurl": page_url, "json": 1}
        async with TLSClient() as client:
            r = await client.get(_2CAPTCHA_URL, params=params, timeout=15)
            data = await r.json()
            if data.get("status") != 1:
                return None
            task_id = data.get("request")
            for _ in range(_MAX_POLLS):
                await asyncio.sleep(_POLL_INTERVAL)
                r2 = await client.get(
                    "https://2captcha.com/res.php",
                    params={"key": self._2captcha_key, "action": "get", "id": task_id, "json": 1},
                    timeout=10,
                )
                result = await r2.json()
                if result.get("status") == 1:
                    return result.get("request")
        return None

    async def _solve_anticaptcha(self, captcha_type: str, site_key: str, page_url: str) -> Optional[str]:
        from app.site.tls_client import TLSClient
        task_map = {
            "hcaptcha": "HCaptchaTaskProxyless",
            "recaptcha_v2": "NoCaptchaTaskProxyless",
            "turnstile": "TurnstileTaskProxyless",
        }
        task_type = task_map.get(captcha_type, "HCaptchaTaskProxyless")
        payload = {
            "clientKey": self._anticaptcha_key,
            "task": {"type": task_type, "websiteURL": page_url, "websiteKey": site_key},
        }
        async with TLSClient() as client:
            r = await client.post(f"{_ANTICAPTCHA_URL}/createTask", json=payload, timeout=15)
            data = await r.json()
            task_id = data.get("taskId")
            if not task_id:
                return None
            for _ in range(_MAX_POLLS):
                await asyncio.sleep(_POLL_INTERVAL)
                r2 = await client.post(
                    f"{_ANTICAPTCHA_URL}/getTaskResult",
                    json={"clientKey": self._anticaptcha_key, "taskId": task_id},
                    timeout=10,
                )
                result = await r2.json()
                if result.get("status") == "ready":
                    return result.get("solution", {}).get("gRecaptchaResponse") or result.get("solution", {}).get("token")
        return None

    async def _inject_token(self, page, captcha_type: str, token: str) -> bool:
        try:
            if captcha_type in ("hcaptcha",):
                await page.evaluate(f"""
                    (() => {{
                        const selectors = [
                            '[name="h-captcha-response"]',
                            '[name="g-recaptcha-response"]',
                            'textarea[name*="captcha"]',
                        ];
                        selectors.forEach(sel => {{
                            const el = document.querySelector(sel);
                            if (el) el.value = '{token}';
                        }});
                        if (window.hcaptcha) {{
                            try {{ window.hcaptcha.setResponse('{token}'); }} catch(e) {{}}
                        }}
                        if (typeof onCaptchaFinished !== 'undefined') onCaptchaFinished('{token}');
                    }})();
                """)
            elif captcha_type == "recaptcha_v2":
                await page.evaluate(f"""
                    (() => {{
                        const el = document.querySelector('#g-recaptcha-response, [name="g-recaptcha-response"]');
                        if (el) el.value = '{token}';
                        if (window.___grecaptcha_cfg) {{
                            try {{
                                const ids = Object.keys(window.___grecaptcha_cfg.clients);
                                if (ids.length) window.___grecaptcha_cfg.clients[ids[0]].l.l.callback('{token}');
                            }} catch(e) {{}}
                        }}
                    }})();
                """)
            elif captcha_type == "turnstile":
                await page.evaluate(f"""
                    (() => {{
                        const el = document.querySelector('[name="cf-turnstile-response"]');
                        if (el) el.value = '{token}';
                        if (window.turnstile) {{
                            try {{ window.turnstile.reset(); }} catch(e) {{}}
                        }}
                    }})();
                """)
            await asyncio.sleep(0.5)
            return True
        except Exception as exc:
            log.error("Token injection failed: %s", exc)
            return False


captcha_solver = CaptchaSolver()
