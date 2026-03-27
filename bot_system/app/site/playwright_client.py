"""
Generic site automation using Playwright + system Chromium.

Smart multi-phase approach:
1. Navigate to the site
2. Find the auth/login page
3. Switch to register mode (click register tab/link)
4. Fill ALL visible form fields intelligently
5. Submit and analyze the result
"""

from __future__ import annotations

import asyncio
import random
import shutil
from typing import Optional
from urllib.parse import urljoin

from app.core.logger import get_logger
from app.core.utils import fake_first_name, fake_last_name, fake_username

log = get_logger(__name__)


class RegistrationResult:
    def __init__(self, success: bool, needs_otp: bool = False,
                 message: str = "", page_url: str = "") -> None:
        self.success = success
        self.needs_otp = needs_otp
        self.message = message
        self.page_url = page_url


_LOGIN_LINK_TEXTS = [
    "تسجيل الدخول", "دخول", "login", "log in", "sign in",
    "حسابي", "my account",
]

_REGISTER_TAB_TEXTS = [
    "سجل الآن", "إنشاء حساب", "حساب جديد", "تسجيل",
    "سجل حساب", "أنشئ حساب", "إنشاء حساب جديد",
    "sign up", "signup", "register", "create account",
    "create an account", "get started", "join", "join now",
    "don't have an account", "ما عندك حساب",
]

_AUTH_PATHS = [
    "/auth", "/login", "/signin", "/sign-in",
    "/account/login", "/accounts/login", "/user/login",
]

_REGISTER_PATHS = [
    "/auth/register", "/register", "/signup", "/sign-up",
    "/account/register", "/accounts/register", "/join",
    "/auth/signup", "/user/register",
]

_OTP_KEYWORDS = [
    "verification code", "verify your email", "check your email",
    "enter the code", "confirm your email", "we sent you",
    "enter otp", "تحقق من بريدك", "رمز التحقق", "أدخل الرمز",
    "تأكيد البريد", "تم إرسال", "verification link",
]

_ERROR_KEYWORDS = [
    "already exists", "already registered", "email taken",
    "email already", "account exists", "مسجل مسبقاً",
    "حساب موجود", "البريد مستخدم", "already in use",
]

_SUCCESS_KEYWORDS = [
    "welcome", "account created", "registration complete",
    "successfully registered", "تم إنشاء", "مرحباً",
    "تم التسجيل", "حسابي",
]


class PlaywrightClient:
    def __init__(self, timeout: int = 20_000) -> None:
        self._timeout = timeout
        self._progress_callback = None

    async def register(self, site_url: str, email: str, password: str,
                       progress_callback=None) -> RegistrationResult:
        self._progress_callback = progress_callback
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return RegistrationResult(False, message="Playwright غير مثبّت")

        first = fake_first_name()
        last = fake_last_name()
        username = fake_username(email)
        phone = f"05{random.randint(10000000, 99999999)}"

        async with async_playwright() as pw:
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

            api_responses = []
            page.on("response", lambda r: api_responses.append(
                (r.status, r.url, r.request.method)
            ) if "/api/" in r.url or r.request.method == "POST" else None)

            try:
                result = await self._do_register(
                    page, site_url, email, password,
                    first, last, username, phone, api_responses,
                )
                return result
            except Exception as exc:
                log.error("Playwright error: %s", exc)
                return RegistrationResult(False, message=f"خطأ: {exc}")
            finally:
                await browser.close()

    async def _do_register(self, page, site_url, email, password,
                           first, last, username, phone, api_responses):
        """Main registration flow."""

        # Phase 1: Find the registration page
        self._log("البحث عن صفحة التسجيل...")
        reg_found = await self._navigate_to_register(page, site_url)
        if not reg_found:
            return RegistrationResult(False, message="لم أجد صفحة تسجيل على هذا الموقع")

        self._log(f"وجدت نموذج التسجيل: {page.url}")

        # Phase 2: Fill ALL visible form fields
        await self._smart_fill(page, email, password, first, last, username, phone)
        await asyncio.sleep(0.5)

        # Phase 3: Submit
        self._log("إرسال النموذج...")
        before_url = page.url
        api_responses.clear()
        submitted = await self._smart_submit(page)
        if not submitted:
            return RegistrationResult(False, message="لم أجد زر إرسال")

        await asyncio.sleep(4)

        # Phase 4: Analyze result
        return await self._analyze(page, before_url, api_responses)

    async def _navigate_to_register(self, page, site_url: str) -> bool:
        """Find and navigate to the registration form."""

        # Strategy 1: Try direct register paths
        for path in _REGISTER_PATHS:
            try:
                url = urljoin(site_url, path)
                await page.goto(url, timeout=self._timeout, wait_until="domcontentloaded")
                await asyncio.sleep(1)
                if await self._is_register_form(page):
                    return True
            except Exception:
                continue

        # Strategy 2: Go to auth paths and click register tab
        for path in _AUTH_PATHS:
            try:
                url = urljoin(site_url, path)
                await page.goto(url, timeout=self._timeout, wait_until="domcontentloaded")
                await asyncio.sleep(1)
                if await self._click_register_tab(page):
                    await asyncio.sleep(1.5)
                    if await self._is_register_form(page):
                        return True
            except Exception:
                continue

        # Strategy 3: Visit homepage and find auth/register links
        try:
            await page.goto(site_url, timeout=self._timeout, wait_until="domcontentloaded")
            await asyncio.sleep(1)

            if await self._click_auth_link(page):
                await asyncio.sleep(1.5)
                if await self._is_register_form(page):
                    return True
                if await self._click_register_tab(page):
                    await asyncio.sleep(1.5)
                    if await self._is_register_form(page):
                        return True
        except Exception:
            pass

        return False

    async def _click_auth_link(self, page) -> bool:
        """Click login/auth link on a page."""
        for text in _LOGIN_LINK_TEXTS:
            try:
                loc = page.get_by_text(text, exact=False).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return True
            except Exception:
                continue

        for sel in ['a[href*="auth"]', 'a[href*="login"]', 'a[href*="signin"]', 'a[href*="account"]']:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return True
            except Exception:
                continue
        return False

    async def _click_register_tab(self, page) -> bool:
        """Click register/signup tab on login page."""
        for text in _REGISTER_TAB_TEXTS:
            try:
                locs = page.get_by_text(text, exact=False)
                count = await locs.count()
                for i in range(count):
                    loc = locs.nth(i)
                    if await loc.is_visible(timeout=500):
                        tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                        if tag in ("a", "button", "span", "div", "p", "label"):
                            await loc.click()
                            return True
            except Exception:
                continue
        return False

    async def _is_register_form(self, page) -> bool:
        """Check if current page has a registration form (email + password + extra fields)."""
        has_email = False
        has_password = False
        visible_inputs = 0

        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                visible_inputs += 1
                inp_type = (await inp.get_attribute("type") or "text").lower()
                if inp_type == "email":
                    has_email = True
                elif inp_type == "password":
                    has_password = True
                else:
                    name = (await inp.get_attribute("name") or "").lower()
                    placeholder = (await inp.get_attribute("placeholder") or "").lower()
                    if "email" in name or "email" in placeholder:
                        has_email = True
            except Exception:
                continue

        if has_email and has_password and visible_inputs >= 3:
            return True
        if has_email and has_password:
            body = (await page.inner_text("body")).lower()
            if any(kw in body for kw in ["إنشاء حساب", "تسجيل", "register", "sign up", "create account"]):
                return True
        return False

    async def _smart_fill(self, page, email, password, first, last, username, phone):
        """Fill ALL visible form fields intelligently based on type/name/placeholder."""
        inputs = await page.query_selector_all("input")
        filled = []

        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue

                inp_type = (await inp.get_attribute("type") or "text").lower()
                name = (await inp.get_attribute("name") or "").lower()
                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                inp_id = (await inp.get_attribute("id") or "").lower()
                autocomplete = (await inp.get_attribute("autocomplete") or "").lower()

                if inp_type in ("hidden", "submit", "button", "checkbox", "radio", "file"):
                    continue

                hint = f"{inp_type} {name} {placeholder} {inp_id} {autocomplete}"

                if inp_type == "email" or "email" in hint:
                    await inp.fill(email)
                    filled.append(f"email={email}")

                elif inp_type == "password":
                    await inp.fill(password)
                    filled.append("password=***")

                elif inp_type == "tel" or any(k in hint for k in ["phone", "mobile", "جوال", "هاتف", "05"]):
                    await inp.fill(phone)
                    filled.append(f"phone={phone}")

                elif any(k in hint for k in ["first_name", "firstname", "fname", "الاسم الأول"]):
                    await inp.fill(first)
                    filled.append(f"first_name={first}")

                elif any(k in hint for k in ["last_name", "lastname", "lname", "اسم العائلة"]):
                    await inp.fill(last)
                    filled.append(f"last_name={last}")

                elif any(k in hint for k in ["full", "name", "الاسم", "اسمك"]):
                    await inp.fill(f"{first} {last}")
                    filled.append(f"name={first} {last}")

                elif any(k in hint for k in ["username", "user_name", "المستخدم"]):
                    await inp.fill(username)
                    filled.append(f"username={username}")

                elif any(k in hint for k in ["birth", "dob", "age", "تاريخ"]):
                    await inp.fill("1995-06-15")
                    filled.append("dob=1995-06-15")

                elif any(k in hint for k in ["address", "عنوان"]):
                    await inp.fill("123 Main Street")
                    filled.append("address=123 Main Street")

                elif any(k in hint for k in ["city", "مدينة"]):
                    await inp.fill("Riyadh")
                    filled.append("city=Riyadh")

                elif any(k in hint for k in ["zip", "postal", "رمز بريدي"]):
                    await inp.fill("12345")
                    filled.append("zip=12345")

            except Exception as exc:
                log.debug("Fill skip: %s", exc)

        # Handle checkboxes (terms, etc.)
        checkboxes = await page.query_selector_all('input[type="checkbox"]')
        for cb in checkboxes:
            try:
                if await cb.is_visible() and not await cb.is_checked():
                    await cb.check()
                    filled.append("checkbox=checked")
            except Exception:
                pass

        # Handle select dropdowns
        selects = await page.query_selector_all("select")
        for sel in selects:
            try:
                if await sel.is_visible():
                    options = await sel.query_selector_all("option")
                    if len(options) > 1:
                        val = await options[1].get_attribute("value")
                        if val:
                            await sel.select_option(val)
                            filled.append(f"select={val}")
            except Exception:
                pass

        log.info("→ Filled %d fields: %s", len(filled), ", ".join(filled))

    async def _smart_submit(self, page) -> bool:
        """Find and click the submit button."""
        submit_texts = [
            "إنشاء حساب", "تسجيل", "سجل", "أنشئ حساب",
            "register", "sign up", "create account", "submit",
            "get started", "join", "continue", "next",
        ]

        for text in submit_texts:
            try:
                btn = page.locator(f'button:has-text("{text}")').first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    return True
            except Exception:
                continue

        for sel in ['button[type="submit"]', 'input[type="submit"]']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    return True
            except Exception:
                continue

        try:
            await page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    async def _analyze(self, page, before_url: str, api_responses: list) -> RegistrationResult:
        """Analyze the result after form submission."""
        current_url = page.url

        # Check API responses for clear signals
        for status, url, method in api_responses:
            if method == "POST":
                url_lower = url.lower()
                if any(k in url_lower for k in ["register", "signup", "sign-up", "auth"]):
                    if 200 <= status < 300:
                        log.info("→ API registration success: %s %s", status, url)
                    elif status >= 400:
                        log.warning("→ API registration failed: %s %s", status, url)

        body = ""
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            pass

        # Check for OTP/verification
        if any(kw in body for kw in _OTP_KEYWORDS):
            return RegistrationResult(
                True, needs_otp=True,
                message="تم التسجيل — بانتظار رمز التحقق",
                page_url=current_url,
            )

        # Check for errors
        if any(kw in body for kw in _ERROR_KEYWORDS):
            return RegistrationResult(
                False, message="الحساب موجود مسبقاً بهذا الإيميل",
                page_url=current_url,
            )

        # Check for success indicators
        if any(kw in body for kw in _SUCCESS_KEYWORDS):
            return RegistrationResult(
                True, message="✅ تم إنشاء الحساب بنجاح",
                page_url=current_url,
            )

        # URL changed to dashboard/account = success
        if current_url.rstrip("/") != before_url.rstrip("/"):
            path = current_url.lower()
            if any(k in path for k in ["account", "dashboard", "profile", "home", "welcome"]):
                return RegistrationResult(
                    True, message="✅ تم إنشاء الحساب بنجاح",
                    page_url=current_url,
                )
            return RegistrationResult(
                True, message="تم إرسال النموذج (تم التحويل لصفحة أخرى)",
                page_url=current_url,
            )

        # Check POST responses
        for status, url, method in api_responses:
            if method == "POST" and 200 <= status < 300:
                return RegistrationResult(
                    True, message="تم إرسال النموذج",
                    page_url=current_url,
                )

        return RegistrationResult(
            True, message="تم إرسال النموذج — تحقق من النتيجة",
            page_url=current_url,
        )

    def _log(self, msg: str):
        log.info("→ %s", msg)
        if self._progress_callback:
            try:
                self._progress_callback(msg)
            except Exception:
                pass
