"""
Generic site automation using Playwright + system Chromium.

Comprehensive multi-phase approach:
1. If user gave a URL with a path, go there directly and work with whatever form exists
2. Try common register/auth paths with register-tab clicking
3. Fall back to homepage link scanning
4. Fill ALL visible form fields intelligently
5. Handle multi-step forms (fill -> submit -> fill next step)
6. Submit and analyze the result (API responses + page content)

Stealth mode: masks headless browser fingerprints to bypass
Cloudflare, hCaptcha, and similar bot detection systems.

Global timeout: 50 seconds max for the entire registration.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
from urllib.parse import urljoin, urlparse

from app.core.logger import get_logger
from app.core.utils import fake_first_name, fake_last_name, fake_username

log = get_logger(__name__)

GLOBAL_TIMEOUT = 80

_NAV_TIMEOUT = 8_000
_SPA_WAIT = 2.0
_SHORT_WAIT = 0.8

_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    window.navigator.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };

    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'ar'] });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5].map(() => ({
            name: 'Chrome PDF Plugin',
            filename: 'internal-pdf-viewer',
            description: 'Portable Document Format',
        })),
    });

    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );

    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };
}
"""

_CF_WAIT_PHRASES = [
    "checking your browser",
    "verify you are human",
    "performing security",
    "just a moment",
    "please wait",
    "enable javascript",
    "checking if the site",
    "attention required",
    "one more step",
    "security check",
]


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

_OAUTH_INDICATORS = [
    "sign in with google", "sign in with microsoft", "sign in with apple",
    "continue with google", "continue with microsoft", "continue with apple",
    "login with google", "login with microsoft", "login with apple",
    "sign in with github", "continue with github",
    "sign in with facebook", "continue with facebook",
    "سجل دخول بجوجل", "تسجيل بجوجل",
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
            return RegistrationResult(False, message="Playwright غير مثبت")

        first = fake_first_name()
        last = fake_last_name()
        username = fake_username(email)
        phone = f"05{random.randint(10000000, 99999999)}"

        browser = None
        pw_instance = None
        try:
            pw_instance = await async_playwright().start()
            chromium_path = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
            launch_args = {
                "headless": True,
                "args": [
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-extensions",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                    "--start-maximized",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                ],
            }
            if chromium_path:
                launch_args["executable_path"] = chromium_path

            browser = await pw_instance.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
                color_scheme="light",
                java_script_enabled=True,
                bypass_csp=True,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                },
            )

            await context.add_init_script(_STEALTH_JS)
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
                message=f"انتهى الوقت ({GLOBAL_TIMEOUT}ث) -- الموقع بطيء أو محمي"
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
        log.info("-> %s", msg)
        if self._progress_callback:
            try:
                self._progress_callback(msg)
            except Exception:
                pass

    async def _wait_for_cf(self, page, max_wait: float = 12.0) -> bool:
        """Wait for Cloudflare/bot-check challenge to resolve.
        Returns True if page loaded successfully, False if still blocked.
        """
        elapsed = 0.0
        interval = 1.5
        while elapsed < max_wait:
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                body = ""

            is_challenge = any(phrase in body for phrase in _CF_WAIT_PHRASES)
            if not is_challenge:
                return True

            log.debug("Cloudflare challenge detected, waiting... (%.1fs)", elapsed)
            await asyncio.sleep(interval)
            elapsed += interval

        log.warning("Cloudflare challenge did not resolve after %.1fs", max_wait)
        return False

    async def _is_oauth_only(self, page) -> bool:
        """Check if the page only offers OAuth login (no email+password form)."""
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            return False

        oauth_count = sum(1 for ind in _OAUTH_INDICATORS if ind in body)

        has_email = bool(await page.query_selector(
            'input[type="email"], input[name*="email"], input[placeholder*="email" i]'
        ))
        has_password = bool(await page.query_selector('input[type="password"]'))

        if oauth_count >= 2 and not has_email and not has_password:
            return True

        return False

    async def _do_register(self, page, site_url, email, password,
                           first, last, username, phone, api_responses):
        self._report(f"البحث عن صفحة التسجيل في {site_url}")

        reg_found = await self._navigate_to_register(page, site_url)

        if not reg_found:
            if await self._is_oauth_only(page):
                return RegistrationResult(
                    False,
                    message="هذا الموقع يدعم التسجيل عبر Google/Microsoft/Apple فقط -- لا يوجد نموذج تسجيل عادي"
                )
            return RegistrationResult(
                False,
                message="لم أجد صفحة تسجيل على هذا الموقع"
            )

        current_url = page.url
        self._report(f"وجدت نموذج في: {current_url}")

        max_steps = 5
        last_result = None

        for step in range(1, max_steps + 1):
            tried_password_link = await self._try_continue_with_password(page)
            if tried_password_link:
                self._report("اختيار التسجيل بالباسوورد...")
                await asyncio.sleep(2)
                await self._wait_for_spa(page)

            filled_count = await self._smart_fill(
                page, email, password, first, last, username, phone
            )

            if filled_count == 0 and step == 1:
                return RegistrationResult(
                    False,
                    message="وجدت الصفحة لكن لم أجد حقول لملئها"
                )

            if filled_count == 0:
                break

            await asyncio.sleep(0.3)

            step_label = f"الخطوة {step}" if step > 1 else "إرسال النموذج"
            self._report(f"{step_label}...")
            before_url = page.url
            api_responses.clear()
            submitted = await self._smart_submit(page)
            if not submitted:
                if step == 1:
                    return RegistrationResult(False, message="لم أجد زر إرسال")
                break

            await asyncio.sleep(3)

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(1)

            step_result = await self._analyze(page, before_url, api_responses)
            last_result = step_result

            if not step_result.success:
                return step_result

            has_api_success = any(
                method == "POST" and 200 <= status < 300
                and any(k in url.lower() for k in ["register", "signup", "auth", "account", "join", "create", "password"])
                for status, url, method in api_responses
            )

            has_register_api = any(
                method == "POST" and 200 <= status < 300
                and any(k in url.lower() for k in ["register", "signup", "sign-up", "join", "create-account"])
                for status, url, method in api_responses
            )
            if has_register_api:
                return RegistrationResult(
                    True,
                    message="تم إنشاء الحساب بنجاح",
                    page_url=page.url
                )

            if step_result.needs_otp:
                can_use_pw = await self._try_continue_with_password(page)
                if can_use_pw:
                    self._report("اختيار التسجيل بالباسوورد بدل OTP...")
                    await asyncio.sleep(2)
                    await self._wait_for_spa(page)
                    continue
                return step_result

            if any(kw in step_result.message for kw in ["تم إنشاء الحساب", "بنجاح"]):
                return step_result

            new_inputs = await self._count_visible_inputs(page)
            if new_inputs == 0:
                if has_api_success:
                    step_result.message = "تم إنشاء الحساب بنجاح"
                return step_result

            self._report(f"مرحلة {step + 1} -- ملء حقول إضافية...")

        if last_result:
            return last_result

        return RegistrationResult(
            True,
            message="تم إرسال النموذج -- تحقق من النتيجة",
            page_url=page.url
        )

    async def _try_continue_with_password(self, page) -> bool:
        password_link_texts = [
            "continue with password", "use password",
            "sign in with password", "use a password",
            "log in with password", "enter password instead",
        ]
        for text in password_link_texts:
            try:
                link = page.get_by_text(text, exact=False).first
                if await link.is_visible(timeout=500):
                    await link.click()
                    return True
            except Exception:
                continue
        return False

    async def _navigate_to_register(self, page, site_url: str) -> bool:
        parsed = urlparse(site_url)
        has_path = parsed.path not in ("", "/")
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        if has_path:
            if await self._try_url_smart(page, site_url):
                return True

        homepage_loaded = await self._load_homepage(page, base_url)

        if homepage_loaded:
            if await self._has_fillable_form(page, require_register_context=True):
                return True

            register_link = await self._find_register_link(page)
            if register_link:
                try:
                    await register_link.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                    await self._wait_for_spa(page)
                    if await self._click_register_tab(page):
                        await asyncio.sleep(1)
                        if await self._has_fillable_form(page):
                            return True
                    elif await self._has_fillable_form(page, require_register_context=True):
                        return True
                except Exception:
                    pass
            else:
                signup_btn_texts = [
                    "sign up for free", "sign up", "signup", "register",
                    "create account", "get started", "إنشاء حساب",
                    "سجل الآن", "سجل", "تسجيل",
                ]
                for text in signup_btn_texts:
                    try:
                        btn = page.get_by_text(text, exact=False).first
                        if await btn.is_visible(timeout=400):
                            await btn.click()
                            await asyncio.sleep(2)
                            await self._wait_for_spa(page)
                            if await self._has_fillable_form(page):
                                return True
                            if await self._has_email_only_form(page):
                                return True
                            break
                    except Exception:
                        continue

        for path in _REGISTER_PATHS:
            url = urljoin(base_url + "/", path)
            if await self._try_url_smart(page, url):
                return True

        for path in _AUTH_PATHS:
            url = urljoin(base_url + "/", path)
            if await self._try_url_with_register_tab(page, url):
                return True

        if await self._try_homepage_form(page, base_url):
            return True

        if await self._quick_homepage_check(page, base_url):
            return True

        return False

    async def _load_homepage(self, page, base_url: str) -> bool:
        try:
            resp = await page.goto(base_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            status = resp.status if resp else 0
            if status == 403 or status == 503:
                self._report("حماية Cloudflare -- جاري المحاولة...")
                cf_ok = await self._wait_for_cf(page, max_wait=12)
                if not cf_ok:
                    return False
            elif status >= 400:
                return False
            await self._wait_for_spa(page)
            return True
        except Exception:
            return False

    async def _find_register_link(self, page):
        link_selectors = [
            'a[href*="register"]', 'a[href*="signup"]', 'a[href*="sign-up"]',
            'a[href*="join"]', 'a[href*="create-account"]',
            'a[href*="auth"]', 'a[href*="login"]', 'a[href*="account"]',
        ]
        for sel in link_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=400):
                    return loc
            except Exception:
                continue
        return None

    async def _quick_homepage_check(self, page, base_url: str) -> bool:
        try:
            resp = await page.goto(base_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            status = resp.status if resp else 0
            if status == 403 or status == 503:
                self._report("حماية Cloudflare -- جاري المحاولة...")
                cf_ok = await self._wait_for_cf(page, max_wait=12)
                if not cf_ok:
                    return False
            elif status >= 400:
                return False
            await self._wait_for_spa(page)

            if await self._has_fillable_form(page):
                return True
            if await self._has_email_only_form(page):
                return True

            signup_btn_texts = [
                "sign up for free", "sign up", "signup", "register",
                "create account", "get started", "إنشاء حساب",
                "سجل", "تسجيل", "سجل الآن",
            ]
            for text in signup_btn_texts:
                try:
                    btn = page.get_by_text(text, exact=False).first
                    if await btn.is_visible(timeout=400):
                        await btn.click()
                        await asyncio.sleep(2)
                        await self._wait_for_spa(page)
                        if await self._has_fillable_form(page):
                            return True
                        if await self._has_email_only_form(page):
                            return True
                        break
                except Exception:
                    continue

        except Exception:
            pass
        return False

    async def _try_url_smart(self, page, url: str) -> bool:
        try:
            resp = await page.goto(url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            status = resp.status if resp else 0

            if status == 403 or status == 503:
                cf_ok = await self._wait_for_cf(page, max_wait=6)
                if not cf_ok:
                    log.debug("CF block at %s (status=%s)", url, status)
                    return False

            elif status >= 400:
                log.debug("_try_url %s -> HTTP %s", url, status)
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
            status = resp.status if resp else 0

            if status == 403 or status == 503:
                cf_ok = await self._wait_for_cf(page, max_wait=5)
                if not cf_ok:
                    return False
            elif status >= 400:
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
            resp = await page.goto(base_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            status = resp.status if resp else 0
            if status == 403 or status == 503:
                cf_ok = await self._wait_for_cf(page, max_wait=12)
                if not cf_ok:
                    return False
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

    async def _try_homepage_form(self, page, base_url: str) -> bool:
        try:
            current = page.url
            if not current.startswith(base_url):
                resp = await page.goto(base_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
                status = resp.status if resp else 0
                if status == 403 or status == 503:
                    cf_ok = await self._wait_for_cf(page, max_wait=12)
                    if not cf_ok:
                        return False
                await self._wait_for_spa(page)
        except Exception:
            return False

        signup_btn_texts = [
            "sign up", "signup", "register", "create account",
            "get started", "sign up for free", "إنشاء حساب",
            "سجل", "تسجيل",
        ]
        for text in signup_btn_texts:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await asyncio.sleep(2)
                    await self._wait_for_spa(page)
                    break
            except Exception:
                continue

        if await self._has_fillable_form(page):
            return True
        if await self._has_email_only_form(page):
            return True

        return False

    async def _wait_for_spa(self, page):
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
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

    async def _has_email_only_form(self, page) -> bool:
        email_input = await page.query_selector(
            'input[type="email"], input[name="email"], '
            'input[placeholder*="email" i], input[placeholder*="mail" i], '
            'input[autocomplete="email"]'
        )
        if not email_input:
            return False
        try:
            if not await email_input.is_visible():
                return False
        except Exception:
            return False

        continue_btn = None
        for text in ["continue", "next", "التالي", "متابعة", "إرسال"]:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible(timeout=300):
                    continue_btn = btn
                    break
            except Exception:
                continue

        if not continue_btn:
            for sel in ['button[type="submit"]', 'input[type="submit"]']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=300):
                        continue_btn = btn
                        break
                except Exception:
                    continue

        if continue_btn:
            log.info("-> Found email-only form (multi-step signup)")
            return True

        return False

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

        log.info("-> Filled %d fields: %s", len(filled), ", ".join(filled))
        return len(filled)

    async def _fill_field(self, inp, value: str):
        try:
            await inp.click()
            await asyncio.sleep(random.uniform(0.05, 0.15))
        except Exception:
            pass
        await inp.fill(value)
        await asyncio.sleep(random.uniform(0.03, 0.1))

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
                        log.info("-> API success: %s %s", status, url[:100])
                    elif status == 409 or status == 422:
                        log.warning("-> API conflict/validation: %s %s", status, url[:100])
                        return RegistrationResult(
                            False,
                            message="الحساب موجود مسبقا أو البيانات غير صحيحة",
                            page_url=current_url
                        )
                    elif status >= 400:
                        log.warning("-> API failed: %s %s", status, url[:100])

        body = ""
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            pass

        if any(kw in body for kw in _ERROR_KEYWORDS):
            return RegistrationResult(
                False,
                message="الحساب موجود مسبقا بهذا الايميل",
                page_url=current_url
            )

        if any(kw in body for kw in _OTP_KEYWORDS):
            return RegistrationResult(
                True, needs_otp=True,
                message="تم التسجيل -- بانتظار رمز التحقق",
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

            still_has_inputs = False
            try:
                inputs = await page.query_selector_all("input")
                for inp in inputs:
                    try:
                        if await inp.is_visible():
                            t = (await inp.get_attribute("type") or "text").lower()
                            if t not in ("hidden", "submit", "button", "file", "image", "reset"):
                                still_has_inputs = True
                                break
                    except Exception:
                        pass
            except Exception:
                pass

            if still_has_inputs:
                return RegistrationResult(
                    True,
                    message="تم إرسال النموذج -- تحقق من النتيجة",
                    page_url=current_url
                )

            if any(k in path for k in [
                "account", "dashboard", "profile", "home",
                "welcome",
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
            message="تم إرسال النموذج -- تحقق من النتيجة",
            page_url=current_url
        )
