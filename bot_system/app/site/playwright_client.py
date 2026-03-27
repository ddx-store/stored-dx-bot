"""
Generic site automation using Playwright + system Chromium.

Comprehensive multi-phase approach:
1. If user gave a URL with a path, go there directly and work with whatever form exists
2. Try common register/auth paths with register-tab clicking
3. Fall back to homepage link scanning
4. Fill ALL visible form fields intelligently
5. Handle multi-step forms (fill → submit → fill next step)
6. Submit and analyze the result (API responses + page content)

Global timeout: 50 seconds max for the entire registration.
"""

from __future__ import annotations

import asyncio
import random
import shutil
from urllib.parse import urljoin, urlparse

from app.core.logger import get_logger
from app.core.utils import fake_first_name, fake_last_name, fake_username

log = get_logger(__name__)

GLOBAL_TIMEOUT = 50

_NAV_TIMEOUT = 8_000
_SPA_WAIT = 2.0
_SHORT_WAIT = 0.8


class RegistrationResult:
    def __init__(self, success: bool, needs_otp: bool = False,
                 message: str = "", page_url: str = "") -> None:
        self.success = success
        self.needs_otp = needs_otp
        self.message = message
        self.page_url = page_url


_REGISTER_TAB_TEXTS = [
    "سجل الآن", "إنشاء حساب", "حساب جديد",
    "سجل حساب", "أنشئ حساب", "إنشاء حساب جديد",
    "sign up", "signup", "register", "create account",
    "create an account", "get started", "join now",
    "don't have an account", "ما عندك حساب",
    "create your account", "new account",
]

_AUTH_PATHS = ["/auth", "/login", "/signin", "/account/login"]

_REGISTER_PATHS = [
    "/auth/register", "/register", "/signup", "/sign-up",
    "/join", "/auth/signup", "/account/register", "/create-account",
]

_OTP_KEYWORDS = [
    "verification code", "verify your email", "check your email",
    "enter the code", "confirm your email", "we sent you",
    "enter otp", "تحقق من بريدك", "رمز التحقق", "أدخل الرمز",
    "تأكيد البريد", "تم إرسال", "verification link",
    "verify your account", "confirm your account",
    "we've sent", "check your inbox",
]

_ERROR_KEYWORDS = [
    "already exists", "already registered", "email taken",
    "email already", "account exists", "مسجل مسبقاً",
    "حساب موجود", "البريد مستخدم", "already in use",
    "email is taken", "duplicate", "already have an account",
]

_SUCCESS_KEYWORDS = [
    "welcome", "account created", "registration complete",
    "successfully registered", "تم إنشاء", "مرحباً",
    "تم التسجيل", "حسابي", "thank you for registering",
    "registration successful", "you're all set",
    "account has been created", "successfully created",
]


class PlaywrightClient:
    def __init__(self, timeout: int = 8_000) -> None:
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

        browser = None
        pw_instance = None
        try:
            pw_instance = await async_playwright().start()
            chromium_path = shutil.which("chromium")
            launch_args = {
                "headless": True,
                "args": [
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-extensions",
                ],
            }
            if chromium_path:
                launch_args["executable_path"] = chromium_path

            browser = await pw_instance.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await context.new_page()

            api_responses = []
            page.on("response", lambda r: api_responses.append(
                (r.status, r.url, r.request.method)
            ) if r.request.method == "POST" else None)

            result = await asyncio.wait_for(
                self._do_register(page, site_url, email, password,
                                  first, last, username, phone, api_responses),
                timeout=GLOBAL_TIMEOUT,
            )
            return result
        except asyncio.TimeoutError:
            log.error("Global timeout (%ds) reached for %s", GLOBAL_TIMEOUT, site_url)
            return RegistrationResult(
                False,
                message=f"انتهى الوقت ({GLOBAL_TIMEOUT}ث) — الموقع بطيء أو محمي"
            )
        except Exception as exc:
            log.error("Playwright error: %s", exc)
            return RegistrationResult(False, message=f"خطأ: {exc}")
        finally:
            try:
                if browser:
                    await asyncio.wait_for(browser.close(), timeout=5)
            except Exception:
                log.warning("Browser close timed out")
            try:
                if pw_instance:
                    await asyncio.wait_for(pw_instance.stop(), timeout=5)
            except Exception:
                log.warning("Playwright stop timed out")

    def _report(self, msg: str):
        log.info("→ %s", msg)
        if self._progress_callback:
            try:
                self._progress_callback(msg)
            except Exception:
                pass

    async def _do_register(self, page, site_url, email, password,
                           first, last, username, phone, api_responses):
        self._report(f"البحث عن صفحة التسجيل في {site_url}")

        reg_found = await self._navigate_to_register(page, site_url)

        if not reg_found:
            return RegistrationResult(
                False,
                message="لم أجد صفحة تسجيل على هذا الموقع"
            )

        current_url = page.url
        self._report(f"وجدت نموذج في: {current_url}")

        filled_count = await self._smart_fill(
            page, email, password, first, last, username, phone
        )

        if filled_count == 0:
            return RegistrationResult(
                False,
                message="وجدت الصفحة لكن لم أجد حقول لملئها"
            )

        await asyncio.sleep(0.3)

        self._report("إرسال النموذج...")
        before_url = page.url
        api_responses.clear()
        submitted = await self._smart_submit(page)
        if not submitted:
            return RegistrationResult(False, message="لم أجد زر إرسال")

        await asyncio.sleep(2.5)

        step1_result = await self._analyze(page, before_url, api_responses)

        if not step1_result.success:
            return step1_result

        has_api_success = any(
            method == "POST" and 200 <= status < 300
            and any(k in url.lower() for k in ["register", "signup", "auth", "account", "join"])
            for status, url, method in api_responses
        )
        if has_api_success:
            if step1_result.message == "تم إرسال النموذج — تحقق من النتيجة":
                step1_result.message = "تم إنشاء الحساب بنجاح"
            return step1_result

        current_path = urlparse(page.url).path.lower()
        is_still_auth = any(k in current_path for k in [
            "auth", "login", "register", "signup", "sign-up",
        ])
        if not is_still_auth:
            return step1_result

        new_inputs = await self._count_visible_inputs(page)
        if new_inputs >= 1:
            self._report("مرحلة ثانية — ملء حقول إضافية...")
            filled2 = await self._smart_fill(
                page, email, password, first, last, username, phone
            )
            if filled2 > 0:
                await asyncio.sleep(0.3)
                api_responses.clear()
                before_url2 = page.url
                await self._smart_submit(page)
                await asyncio.sleep(2)
                step2_result = await self._analyze(page, before_url2, api_responses)
                if step2_result.message and step2_result.message != step1_result.message:
                    return step2_result

        return step1_result

    async def _navigate_to_register(self, page, site_url: str) -> bool:
        parsed = urlparse(site_url)
        has_path = parsed.path not in ("", "/")
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        if has_path:
            if await self._try_url_smart(page, site_url):
                return True

        for path in _REGISTER_PATHS:
            url = urljoin(base_url + "/", path)
            if await self._try_url_smart(page, url):
                return True

        for path in _AUTH_PATHS:
            url = urljoin(base_url + "/", path)
            if await self._try_url_with_register_tab(page, url):
                return True

        if await self._try_homepage_links(page, base_url):
            return True

        return False

    async def _try_url_smart(self, page, url: str) -> bool:
        try:
            resp = await page.goto(url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                log.debug("_try_url %s → HTTP %s", url, resp.status)
                return False
            await self._wait_for_spa(page)
            if await self._has_fillable_form(page):
                return True
            if await self._click_register_tab(page):
                await asyncio.sleep(1)
                return await self._has_fillable_form(page)
        except Exception as exc:
            log.debug("_try_url %s failed: %s", url, exc)
        return False

    async def _try_url_with_register_tab(self, page, url: str) -> bool:
        try:
            resp = await page.goto(url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                return False
            await self._wait_for_spa(page)
            if await self._click_register_tab(page):
                await asyncio.sleep(1)
                if await self._has_fillable_form(page):
                    return True
            if await self._has_fillable_form(page, require_register_context=True):
                return True
        except Exception as exc:
            log.debug("_try_url_with_tab %s failed: %s", url, exc)
        return False

    async def _try_homepage_links(self, page, base_url: str) -> bool:
        try:
            await page.goto(base_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            await self._wait_for_spa(page)
        except Exception:
            return False

        link_selectors = [
            'a[href*="register"]', 'a[href*="signup"]', 'a[href*="sign-up"]',
            'a[href*="join"]', 'a[href*="create-account"]',
            'a[href*="auth"]', 'a[href*="login"]', 'a[href*="account"]',
        ]

        for sel in link_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                    await self._wait_for_spa(page)
                    if await self._has_fillable_form(page):
                        return True
                    if await self._click_register_tab(page):
                        await asyncio.sleep(1)
                        if await self._has_fillable_form(page):
                            return True
                    break
            except Exception:
                continue
        return False

    async def _wait_for_spa(self, page):
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            pass
        await asyncio.sleep(0.3)

    async def _click_register_tab(self, page) -> bool:
        for text in _REGISTER_TAB_TEXTS:
            try:
                locs = page.get_by_text(text, exact=False)
                count = await locs.count()
                for i in range(min(count, 3)):
                    loc = locs.nth(i)
                    if await loc.is_visible(timeout=300):
                        tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                        if tag in ("a", "button", "span", "div", "p", "label", "li"):
                            await loc.click()
                            return True
            except Exception:
                continue
        return False

    async def _count_visible_inputs(self, page) -> int:
        count = 0
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                inp_type = (await inp.get_attribute("type") or "text").lower()
                if inp_type in ("hidden", "submit", "button", "file", "image", "reset"):
                    continue
                count += 1
            except Exception:
                continue
        return count

    async def _has_fillable_form(self, page, require_register_context=False) -> bool:
        has_email = False
        has_password = False
        visible_inputs = 0

        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                inp_type = (await inp.get_attribute("type") or "text").lower()
                if inp_type in ("hidden", "submit", "button", "file", "image", "reset"):
                    continue
                visible_inputs += 1

                if inp_type == "email":
                    has_email = True
                elif inp_type == "password":
                    has_password = True
                else:
                    name = (await inp.get_attribute("name") or "").lower()
                    placeholder = (await inp.get_attribute("placeholder") or "").lower()
                    autocomplete = (await inp.get_attribute("autocomplete") or "").lower()
                    hint = f"{name} {placeholder} {autocomplete}"
                    if "email" in hint or "mail" in hint:
                        has_email = True
                    elif "pass" in hint:
                        has_password = True
            except Exception:
                continue

        if has_email and has_password and visible_inputs >= 2:
            return True

        if has_email and has_password:
            return True

        if has_email and visible_inputs >= 3:
            return True

        if visible_inputs >= 2 and has_email:
            try:
                body = (await page.inner_text("body")).lower()
                register_kws = [
                    "إنشاء حساب", "حساب جديد", "تسجيل حساب",
                    "register", "sign up", "create account",
                    "create your account", "join",
                ]
                if any(kw in body for kw in register_kws):
                    return True
            except Exception:
                pass

        if require_register_context:
            return False

        if visible_inputs >= 3 and (has_email or has_password):
            return True

        return False

    async def _smart_fill(self, page, email, password, first, last, username, phone) -> int:
        inputs = await page.query_selector_all("input")
        filled = []
        filled_types = set()

        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue

                inp_type = (await inp.get_attribute("type") or "text").lower()
                name = (await inp.get_attribute("name") or "").lower()
                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                inp_id = (await inp.get_attribute("id") or "").lower()
                autocomplete = (await inp.get_attribute("autocomplete") or "").lower()
                aria_label = (await inp.get_attribute("aria-label") or "").lower()

                if inp_type in ("hidden", "submit", "button", "file", "image", "reset"):
                    continue

                hint = f"{inp_type} {name} {placeholder} {inp_id} {autocomplete} {aria_label}"

                current_val = await inp.input_value()
                if current_val and len(current_val) > 2:
                    continue

                if inp_type == "checkbox" or inp_type == "radio":
                    continue

                if inp_type == "email" or "email" in hint or "mail" in hint:
                    if "email" not in filled_types:
                        await self._fill_field(inp, email)
                        filled.append(f"email={email}")
                        filled_types.add("email")
                elif inp_type == "password" or "pass" in hint:
                    await self._fill_field(inp, password)
                    filled.append("password=***")
                    filled_types.add("password")
                elif inp_type == "tel" or any(k in hint for k in [
                    "phone", "mobile", "جوال", "هاتف", "رقم", "05", "tel"
                ]):
                    await self._fill_field(inp, phone)
                    filled.append(f"phone={phone}")
                    filled_types.add("phone")
                elif any(k in hint for k in [
                    "first_name", "firstname", "fname", "first-name",
                    "الاسم الأول", "given-name", "givenname"
                ]):
                    await self._fill_field(inp, first)
                    filled.append(f"first_name={first}")
                    filled_types.add("first_name")
                elif any(k in hint for k in [
                    "last_name", "lastname", "lname", "last-name",
                    "اسم العائلة", "family-name", "familyname", "surname"
                ]):
                    await self._fill_field(inp, last)
                    filled.append(f"last_name={last}")
                    filled_types.add("last_name")
                elif any(k in hint for k in [
                    "full", "name", "الاسم", "اسمك", "your name",
                    "display", "displayname"
                ]):
                    await self._fill_field(inp, f"{first} {last}")
                    filled.append(f"name={first} {last}")
                    filled_types.add("name")
                elif any(k in hint for k in [
                    "username", "user_name", "user-name",
                    "المستخدم", "اسم المستخدم", "nickname"
                ]):
                    await self._fill_field(inp, username)
                    filled.append(f"username={username}")
                    filled_types.add("username")
                elif any(k in hint for k in ["birth", "dob", "age", "تاريخ"]):
                    await self._fill_field(inp, "1995-06-15")
                    filled.append("dob=1995-06-15")
                elif any(k in hint for k in ["address", "عنوان"]):
                    await self._fill_field(inp, "123 Main Street")
                    filled.append("address=123 Main Street")
                elif any(k in hint for k in ["city", "مدينة"]):
                    await self._fill_field(inp, "Riyadh")
                    filled.append("city=Riyadh")
                elif any(k in hint for k in ["zip", "postal", "رمز بريدي"]):
                    await self._fill_field(inp, "12345")
                    filled.append("zip=12345")
                elif any(k in hint for k in ["country", "بلد"]):
                    await self._fill_field(inp, "Saudi Arabia")
                    filled.append("country=SA")
            except Exception as exc:
                log.debug("Fill skip: %s", exc)

        checkboxes = await page.query_selector_all('input[type="checkbox"]')
        for cb in checkboxes:
            try:
                if await cb.is_visible() and not await cb.is_checked():
                    await cb.check()
                    filled.append("checkbox=checked")
            except Exception:
                pass

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
        return len(filled)

    async def _fill_field(self, inp, value: str):
        try:
            await inp.click()
            await asyncio.sleep(0.05)
        except Exception:
            pass
        await inp.fill(value)

    async def _smart_submit(self, page) -> bool:
        submit_texts = [
            "إنشاء حساب", "تسجيل", "سجل", "أنشئ حساب",
            "register", "sign up", "create account", "submit",
            "get started", "join", "continue", "next", "إرسال",
            "التالي", "متابعة", "إنشاء", "create", "start",
        ]

        for text in submit_texts:
            try:
                btn = page.locator(f'button:has-text("{text}")').first
                if await btn.is_visible(timeout=300):
                    await btn.click()
                    return True
            except Exception:
                continue

        for sel_str in [
            'button[type="submit"]',
            'input[type="submit"]',
            'button.submit', 'button.btn-primary',
            'button.btn-register', 'button.signup-btn',
        ]:
            try:
                btn = page.locator(sel_str).first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    return True
            except Exception:
                continue

        try:
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                if await btn.is_visible():
                    text = (await btn.inner_text()).strip().lower()
                    if text and len(text) < 30 and text not in (
                        "x", "close", "cancel", "إلغاء", "إغلاق"
                    ):
                        await btn.click()
                        return True
        except Exception:
            pass

        try:
            await page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    async def _analyze(self, page, before_url: str, api_responses: list) -> RegistrationResult:
        current_url = page.url

        for status, url, method in api_responses:
            if method == "POST":
                url_lower = url.lower()
                if any(k in url_lower for k in [
                    "register", "signup", "sign-up", "auth",
                    "account", "join", "create",
                ]):
                    if 200 <= status < 300:
                        log.info("→ API success: %s %s", status, url[:100])
                    elif status == 409 or status == 422:
                        log.warning("→ API conflict/validation: %s %s", status, url[:100])
                        return RegistrationResult(
                            False,
                            message="الحساب موجود مسبقاً أو البيانات غير صحيحة",
                            page_url=current_url
                        )
                    elif status >= 400:
                        log.warning("→ API failed: %s %s", status, url[:100])

        body = ""
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            pass

        if any(kw in body for kw in _ERROR_KEYWORDS):
            return RegistrationResult(
                False,
                message="الحساب موجود مسبقاً بهذا الإيميل",
                page_url=current_url
            )

        if any(kw in body for kw in _OTP_KEYWORDS):
            return RegistrationResult(
                True, needs_otp=True,
                message="تم التسجيل — بانتظار رمز التحقق",
                page_url=current_url
            )

        if any(kw in body for kw in _SUCCESS_KEYWORDS):
            return RegistrationResult(
                True,
                message="تم إنشاء الحساب بنجاح",
                page_url=current_url
            )

        if current_url.rstrip("/") != before_url.rstrip("/"):
            path = current_url.lower()
            if any(k in path for k in [
                "account", "dashboard", "profile", "home",
                "welcome", "verify", "confirm",
            ]):
                return RegistrationResult(
                    True,
                    message="تم إنشاء الحساب بنجاح",
                    page_url=current_url
                )
            return RegistrationResult(
                True,
                message="تم إرسال النموذج (تم التحويل لصفحة أخرى)",
                page_url=current_url
            )

        for status, url, method in api_responses:
            if method == "POST" and 200 <= status < 300:
                return RegistrationResult(
                    True,
                    message="تم إرسال النموذج بنجاح",
                    page_url=current_url
                )

        return RegistrationResult(
            True,
            message="تم إرسال النموذج — تحقق من النتيجة",
            page_url=current_url
        )
