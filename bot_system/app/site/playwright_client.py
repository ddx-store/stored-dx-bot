"""
Generic site automation using Playwright.

Navigates to any website, finds the registration form, fills it with
the provided email + fixed password + random fake info, then handles
OTP verification if required.
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

# Common patterns for locating signup pages
_SIGNUP_TEXTS = [
    "sign up", "signup", "register", "create account", "create an account",
    "get started", "join", "join now", "new account", "إنشاء حساب",
    "تسجيل", "انضم", "التسجيل",
]

_EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[id="email"]',
    'input[placeholder*="email" i]',
    'input[autocomplete="email"]',
]

_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[id="password"]',
    'input[autocomplete="new-password"]',
    'input[autocomplete="current-password"]',
]

_FIRST_NAME_SELECTORS = [
    'input[name="first_name"]',
    'input[name="firstName"]',
    'input[name="fname"]',
    'input[id="first_name"]',
    'input[id="firstName"]',
    'input[placeholder*="first name" i]',
    'input[placeholder*="الاسم الأول" i]',
]

_LAST_NAME_SELECTORS = [
    'input[name="last_name"]',
    'input[name="lastName"]',
    'input[name="lname"]',
    'input[id="last_name"]',
    'input[id="lastName"]',
    'input[placeholder*="last name" i]',
    'input[placeholder*="اسم العائلة" i]',
]

_FULL_NAME_SELECTORS = [
    'input[name="name"]',
    'input[name="full_name"]',
    'input[name="fullName"]',
    'input[id="name"]',
    'input[placeholder*="full name" i]',
    'input[placeholder*="your name" i]',
    'input[placeholder*="الاسم" i]',
]

_USERNAME_SELECTORS = [
    'input[name="username"]',
    'input[name="user_name"]',
    'input[id="username"]',
    'input[placeholder*="username" i]',
    'input[placeholder*="user name" i]',
]

_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
]

_OTP_SELECTORS = [
    'input[name="otp"]',
    'input[name="code"]',
    'input[name="verification_code"]',
    'input[name="verificationCode"]',
    'input[id="otp"]',
    'input[id="code"]',
    'input[placeholder*="code" i]',
    'input[placeholder*="otp" i]',
    'input[placeholder*="verification" i]',
    'input[placeholder*="الرمز" i]',
]

_COMMON_SIGNUP_PATHS = [
    "/signup", "/register", "/join", "/account/register",
    "/accounts/register", "/user/register", "/sign-up", "/en/register",
]


class RegistrationResult:
    def __init__(
        self,
        success: bool,
        needs_otp: bool = False,
        message: str = "",
        page_url: str = "",
    ) -> None:
        self.success = success
        self.needs_otp = needs_otp
        self.message = message
        self.page_url = page_url

    def __repr__(self) -> str:
        return (
            f"RegistrationResult(success={self.success}, "
            f"needs_otp={self.needs_otp}, message={self.message!r})"
        )


class PlaywrightClient:
    """
    Browser-based registration automation for any website.
    Works headlessly — no display required.
    """

    def __init__(self, timeout: int = 30_000) -> None:
        self._timeout = timeout  # milliseconds

    # ---------------------------------------------------------------- #
    # Public API
    # ---------------------------------------------------------------- #

    async def register(
        self,
        site_url: str,
        email: str,
        password: str,
    ) -> RegistrationResult:
        """
        Open the site, find the signup form, fill it, and submit.
        Returns RegistrationResult indicating success / OTP-needed / failure.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("Playwright not installed — run: playwright install chromium")
            return RegistrationResult(
                success=False,
                message="Playwright غير مثبّت على السيرفر.",
            )

        first = fake_first_name()
        last = fake_last_name()
        username = fake_username(email)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            try:
                log.info("[Playwright] Navigating to %s", site_url)
                await page.goto(
                    site_url,
                    timeout=self._timeout,
                    wait_until="domcontentloaded",
                )

                # Try to find signup from the main page
                found = await self._find_and_navigate_signup(page, site_url)
                if not found:
                    # Try common signup URL paths
                    for path in _COMMON_SIGNUP_PATHS:
                        try:
                            target = urljoin(site_url, path)
                            log.info("[Playwright] Trying path: %s", target)
                            await page.goto(target, timeout=8_000, wait_until="domcontentloaded")
                            if await self._has_registration_form(page):
                                found = True
                                break
                        except Exception:
                            continue

                if not found and not await self._has_registration_form(page):
                    return RegistrationResult(
                        success=False,
                        message="لم أجد نموذج تسجيل على هذا الموقع.",
                        page_url=page.url,
                    )

                log.info("[Playwright] Found registration form at %s", page.url)

                # Fill the form
                await self._fill_form(page, email, password, first, last, username)
                await asyncio.sleep(random.uniform(0.5, 1.2))

                # Submit
                initial_url = page.url
                await self._submit_form(page)
                await asyncio.sleep(3)

                current_url = page.url

                # Check if OTP step appeared
                needs_otp = await self._detect_otp_page(page)
                if needs_otp:
                    log.info("[Playwright] OTP step detected after registration.")
                    return RegistrationResult(
                        success=True,
                        needs_otp=True,
                        message="تم إرسال طلب التسجيل، انتظر رمز التحقق على البريد.",
                        page_url=current_url,
                    )

                # URL changed = likely success redirect
                if current_url.rstrip("/") != initial_url.rstrip("/"):
                    return RegistrationResult(
                        success=True,
                        needs_otp=False,
                        message=f"✅ تم إنشاء الحساب بنجاح.",
                        page_url=current_url,
                    )

                # Default: form was submitted
                return RegistrationResult(
                    success=True,
                    needs_otp=False,
                    message=f"تم إرسال النموذج. تحقق من بريدك للتأكيد.",
                    page_url=current_url,
                )

            except Exception as exc:
                log.exception("[Playwright] Error during registration: %s", exc)
                return RegistrationResult(
                    success=False,
                    message=f"خطأ في المتصفح: {exc}",
                    page_url=getattr(page, "url", ""),
                )
            finally:
                await browser.close()

    # ---------------------------------------------------------------- #
    # Internals
    # ---------------------------------------------------------------- #

    async def _find_and_navigate_signup(self, page, base_url: str) -> bool:
        for text in _SIGNUP_TEXTS:
            try:
                locator = page.get_by_text(text, exact=False).first
                if await locator.is_visible(timeout=1_500):
                    log.info("[Playwright] Clicking signup link: %r", text)
                    await locator.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=8_000)
                    return True
            except Exception:
                continue
        return False

    async def _has_registration_form(self, page) -> bool:
        for sel in _EMAIL_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1_500):
                    return True
            except Exception:
                continue
        return False

    async def _fill_form(
        self, page, email: str, password: str,
        first: str, last: str, username: str
    ) -> None:
        # Email
        for sel in _EMAIL_SELECTORS:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=1_500):
                    await field.fill(email)
                    log.info("[Playwright] Filled email: %s", sel)
                    break
            except Exception:
                continue

        # Password (fill all password fields — some sites have confirm password)
        for sel in _PASSWORD_SELECTORS:
            try:
                fields = page.locator(sel)
                count = await fields.count()
                for i in range(count):
                    field = fields.nth(i)
                    if await field.is_visible():
                        await field.fill(password)
            except Exception:
                continue

        # First name
        for sel in _FIRST_NAME_SELECTORS:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=1_000):
                    await field.fill(first)
                    break
            except Exception:
                continue

        # Last name
        for sel in _LAST_NAME_SELECTORS:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=1_000):
                    await field.fill(last)
                    break
            except Exception:
                continue

        # Full name fallback
        for sel in _FULL_NAME_SELECTORS:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=1_000):
                    await field.fill(f"{first} {last}")
                    break
            except Exception:
                continue

        # Username
        for sel in _USERNAME_SELECTORS:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=1_000):
                    await field.fill(username)
                    break
            except Exception:
                continue

    async def _submit_form(self, page) -> None:
        for sel in _SUBMIT_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2_000):
                    await btn.click()
                    log.info("[Playwright] Clicked submit: %s", sel)
                    return
            except Exception:
                continue
        await page.keyboard.press("Enter")

    async def _detect_otp_page(self, page) -> bool:
        for sel in _OTP_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1_500):
                    return True
            except Exception:
                continue
        content = (await page.content()).lower()
        indicators = [
            "verification code", "verify your email", "check your email",
            "enter the code", "confirm your email", "تحقق من بريدك",
            "رمز التحقق", "تأكيد البريد", "we sent you", "تم إرسال",
        ]
        return any(ind in content for ind in indicators)
