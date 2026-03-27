"""
Generic site automation using Playwright.

Navigates to any website, finds the registration form, fills it with
the provided email + fixed password + random fake info.
Optimized for speed — reduced timeouts, parallel detection.
"""

from __future__ import annotations

import asyncio
import random
from urllib.parse import urljoin

from app.core.logger import get_logger
from app.core.utils import (
    fake_first_name,
    fake_last_name,
    fake_username,
)

log = get_logger(__name__)

_SIGNUP_TEXTS = [
    "sign up", "signup", "register", "create account", "create an account",
    "get started", "join", "join now", "new account", "إنشاء حساب",
    "تسجيل", "انضم", "التسجيل",
]

_EMAIL_SELECTORS = [
    'input[type="email"]', 'input[name="email"]', 'input[id="email"]',
    'input[placeholder*="email" i]', 'input[autocomplete="email"]',
]

_PASSWORD_SELECTORS = [
    'input[type="password"]', 'input[name="password"]',
    'input[autocomplete="new-password"]', 'input[autocomplete="current-password"]',
]

_NAME_SELECTORS = {
    "first": [
        'input[name="first_name"]', 'input[name="firstName"]', 'input[name="fname"]',
        'input[placeholder*="first name" i]',
    ],
    "last": [
        'input[name="last_name"]', 'input[name="lastName"]', 'input[name="lname"]',
        'input[placeholder*="last name" i]',
    ],
    "full": [
        'input[name="name"]', 'input[name="full_name"]', 'input[name="fullName"]',
        'input[placeholder*="full name" i]', 'input[placeholder*="your name" i]',
    ],
    "username": [
        'input[name="username"]', 'input[id="username"]',
        'input[placeholder*="username" i]',
    ],
}

_SUBMIT_SELECTORS = ['button[type="submit"]', 'input[type="submit"]']

_OTP_SELECTORS = [
    'input[name="otp"]', 'input[name="code"]', 'input[name="verification_code"]',
    'input[placeholder*="code" i]', 'input[placeholder*="otp" i]',
]

_COMMON_SIGNUP_PATHS = [
    "/signup", "/register", "/join", "/sign-up", "/account/register",
]


class RegistrationResult:
    def __init__(self, success: bool, needs_otp: bool = False,
                 message: str = "", page_url: str = "") -> None:
        self.success = success
        self.needs_otp = needs_otp
        self.message = message
        self.page_url = page_url


class PlaywrightClient:
    def __init__(self, timeout: int = 20_000) -> None:
        self._timeout = timeout

    async def register(self, site_url: str, email: str, password: str) -> RegistrationResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return RegistrationResult(False, message="Playwright غير مثبّت")

        first = fake_first_name()
        last = fake_last_name()
        username = fake_username(email)

        async with async_playwright() as pw:
            import shutil
            chromium_path = shutil.which("chromium")
            launch_args = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-setuid-sandbox",
                         "--disable-dev-shm-usage",
                         "--disable-blink-features=AutomationControlled"],
            }
            if chromium_path:
                launch_args["executable_path"] = chromium_path
            browser = await pw.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            try:
                log.info("→ Opening %s", site_url)
                await page.goto(site_url, timeout=self._timeout, wait_until="domcontentloaded")

                found = await self._find_signup(page, site_url)
                if not found and not await self._has_form(page):
                    for path in _COMMON_SIGNUP_PATHS:
                        try:
                            await page.goto(urljoin(site_url, path), timeout=8000, wait_until="domcontentloaded")
                            if await self._has_form(page):
                                found = True
                                break
                        except Exception:
                            continue

                if not found and not await self._has_form(page):
                    return RegistrationResult(False, message="لم أجد نموذج تسجيل")

                log.info("→ Found form at %s", page.url)
                await self._fill(page, email, password, first, last, username)
                await asyncio.sleep(0.3)

                before_url = page.url
                await self._submit(page)
                await asyncio.sleep(2)

                otp = await self._detect_otp(page)
                if otp:
                    return RegistrationResult(True, needs_otp=True,
                                              message="تم الإرسال — بانتظار رمز التحقق",
                                              page_url=page.url)

                if page.url.rstrip("/") != before_url.rstrip("/"):
                    return RegistrationResult(True, message="✅ تم إنشاء الحساب بنجاح",
                                              page_url=page.url)

                return RegistrationResult(True, message="تم إرسال النموذج",
                                          page_url=page.url)

            except Exception as exc:
                log.error("Playwright error: %s", exc)
                return RegistrationResult(False, message=f"خطأ: {exc}")
            finally:
                await browser.close()

    async def _find_signup(self, page, base_url: str) -> bool:
        for text in _SIGNUP_TEXTS:
            try:
                loc = page.get_by_text(text, exact=False).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return True
            except Exception:
                continue
        return False

    async def _has_form(self, page) -> bool:
        for sel in _EMAIL_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    return True
            except Exception:
                continue
        return False

    async def _fill(self, page, email, password, first, last, username) -> None:
        for sel in _EMAIL_SELECTORS:
            try:
                f = page.locator(sel).first
                if await f.is_visible(timeout=1000):
                    await f.fill(email)
                    break
            except Exception:
                continue

        for sel in _PASSWORD_SELECTORS:
            try:
                fields = page.locator(sel)
                for i in range(await fields.count()):
                    f = fields.nth(i)
                    if await f.is_visible():
                        await f.fill(password)
            except Exception:
                continue

        for sel in _NAME_SELECTORS["first"]:
            try:
                f = page.locator(sel).first
                if await f.is_visible(timeout=500):
                    await f.fill(first)
                    break
            except Exception:
                continue

        for sel in _NAME_SELECTORS["last"]:
            try:
                f = page.locator(sel).first
                if await f.is_visible(timeout=500):
                    await f.fill(last)
                    break
            except Exception:
                continue

        for sel in _NAME_SELECTORS["full"]:
            try:
                f = page.locator(sel).first
                if await f.is_visible(timeout=500):
                    await f.fill(f"{first} {last}")
                    break
            except Exception:
                continue

        for sel in _NAME_SELECTORS["username"]:
            try:
                f = page.locator(sel).first
                if await f.is_visible(timeout=500):
                    await f.fill(username)
                    break
            except Exception:
                continue

    async def _submit(self, page) -> None:
        for sel in _SUBMIT_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    return
            except Exception:
                continue
        await page.keyboard.press("Enter")

    async def _detect_otp(self, page) -> bool:
        for sel in _OTP_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    return True
            except Exception:
                continue
        content = (await page.content()).lower()
        return any(kw in content for kw in [
            "verification code", "verify your email", "check your email",
            "enter the code", "تحقق من بريدك", "رمز التحقق",
        ])
