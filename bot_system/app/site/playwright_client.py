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

from app.core.fingerprint import fingerprint_engine
from app.site.dom_adapter import dom_adapter

from app.core.logger import get_logger
from app.core.utils import fake_first_name, fake_last_name, fake_username

log = get_logger(__name__)

GLOBAL_TIMEOUT = 300

_NAV_TIMEOUT = 15_000
_SPA_WAIT = 1.0
_SHORT_WAIT = 0.5

_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    delete navigator.__proto__.webdriver;

    window.navigator.chrome = {
        runtime: { onConnect: undefined, onMessage: undefined, id: undefined },
        loadTimes: function(){ return {}; },
        csi: function(){ return {}; },
        app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
    };

    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'ar'] });
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            plugins.refresh = () => {};
            return plugins;
        },
    });
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => {
            const types = [
                { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            ];
            types.refresh = () => {};
            return types;
        },
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
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

    Object.defineProperty(navigator, 'connection', {
        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }),
    });

    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };

    if (typeof WebGL2RenderingContext !== 'undefined') {
        const getParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParam2.call(this, parameter);
        };
    }

    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        if (this.width === 0 && this.height === 0) return origToDataURL.apply(this, arguments);
        return origToDataURL.apply(this, arguments);
    };

    window.Notification = window.Notification || { permission: 'default' };

    Object.defineProperty(document, 'hidden', { get: () => false });
    Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
}
"""

_CF_WAIT_PHRASES = [
    "checking your browser",
    "verify you are human",
    "performing security",
    "just a moment",
    "enable javascript",
    "checking if the site",
    "attention required",
    "one more step",
    "security check",
    "ddos protection",
    "ray id",
]

# Arkose Labs / FunCaptcha indicators — مختلف تماماً عن Cloudflare
_ARKOSE_PHRASES = [
    "arkoselabs",
    "funcaptcha",
    "verify your identity",
    "complete the challenge",
    "confirm you're human",
]


class RegistrationResult:
    def __init__(self, success: bool, needs_otp: bool = False,
                 message: str = "", page_url: str = "",
                 account_confirmed: bool = False) -> None:
        self.success = success
        self.needs_otp = needs_otp
        self.message = message
        self.page_url = page_url
        self.account_confirmed = account_confirmed  # تم التحقق من الحساب فعلاً


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

_DIRECT_AUTH_URLS = {
    # ChatGPT — نبدأ من الصفحة الرئيسية ونضغط Sign up (أكثر موثوقية من direct auth)
    "canva.com": "https://www.canva.com/signup",
}

# مواقع تُعالج ChatGPT بتدفق خاص عبر الصفحة الرئيسية
_CHATGPT_HOSTS = {"chatgpt.com", "chat.openai.com"}

# Keywords that appear when signup succeeded (post-auth redirect)
_SIGNUP_SUCCESS_URLS = [
    "chatgpt.com/?",          # ChatGPT main page after signup
    "chatgpt.com/c/",          # Chat session
    "chatgpt.com/gpts",
    "chat.openai.com",
    "/onboarding",
    "/welcome",
    "/dashboard",
    "/home",
]

# ChatGPT-specific error phrases on the signup page
_CHATGPT_ERROR_PHRASES = [
    "email already in use",
    "account already exists",
    "email is already registered",
    "this email is already",
    "already have an account",
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
    "you're set", "you are set", "account is ready",
    "setup complete", "profile complete",
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
                       progress_callback=None,
                       otp_provider=None) -> RegistrationResult:
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
        xvfb_proc = None
        try:
            import subprocess as _sp
            xvfb_display = f":{random.randint(10, 99)}"
            try:
                xvfb_proc = _sp.Popen(
                    ["Xvfb", xvfb_display, "-screen", "0", "1920x1080x24",
                     "-nolisten", "tcp", "-ac"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                )
                await asyncio.sleep(0.5)
                os.environ["DISPLAY"] = xvfb_display
                _use_headed = True
                log.info("Xvfb started on display %s — using headed mode", xvfb_display)
            except Exception as xvfb_err:
                _use_headed = False
                log.warning("Xvfb unavailable (%s) — falling back to headless", xvfb_err)

            pw_instance = await async_playwright().start()
            chromium_path = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
            chrome_args = [
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--lang=en-US",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-component-update",
            ]
            if not _use_headed:
                chrome_args.append("--disable-gpu")

            launch_args = {
                "headless": not _use_headed,
                "args": chrome_args,
            }
            if chromium_path:
                launch_args["executable_path"] = chromium_path

            browser = await pw_instance.chromium.launch(**launch_args)
            fp = fingerprint_engine.generate(proxy_country="US")
            cv = fp.chrome_version
            context = await browser.new_context(
                user_agent=fp.user_agent,
                viewport=fp.viewport,
                locale="en-US",
                timezone_id=fp.timezone_id,
                color_scheme="light",
                java_script_enabled=True,
                bypass_csp=True,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "sec-ch-ua": f'"Google Chrome";v="{cv}", "Chromium";v="{cv}"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                },
            )

            await context.add_init_script(fp.build_init_script())

            try:
                from playwright_stealth import stealth_async
            except ImportError:
                stealth_async = None

            page = await context.new_page()
            if stealth_async:
                await stealth_async(page)
                log.info("playwright-stealth applied")

            api_responses = []

            def _on_response(r):
                try:
                    if r.request.method == "POST":
                        api_responses.append((r.status, r.url, r.request.method))
                except Exception:
                    pass

            page.on("response", _on_response)

            result = await asyncio.wait_for(
                self._do_register(page, site_url, email, password,
                                  first, last, username, phone, api_responses),
                timeout=GLOBAL_TIMEOUT,
            )

            if result.needs_otp and otp_provider:
                max_otp_attempts = 3
                for otp_attempt in range(1, max_otp_attempts + 1):
                    attempt_label = f"(المحاولة {otp_attempt}/{max_otp_attempts})" if otp_attempt > 1 else ""
                    self._report(f"بانتظار رمز التحقق من البريد... {attempt_label}".strip())
                    try:
                        otp_data = await otp_provider()
                        if otp_data:
                            otp_code = otp_data.get("code")
                            otp_link = otp_data.get("link")
                            otp_done = False

                            if otp_link and otp_link.startswith("http"):
                                self._report("فتح رابط التحقق...")
                                verify_result = await self._open_verification_link(
                                    page, context, otp_link
                                )
                                if verify_result and verify_result.success:
                                    otp_done = True

                            if not otp_done and otp_code:
                                self._report(f"إدخال رمز التحقق {otp_code}...")
                                verify_result = await self._fill_otp_code(
                                    page, otp_code
                                )
                                if verify_result:
                                    otp_done = True

                            if not otp_done:
                                return RegistrationResult(
                                    True,
                                    message=f"تم التسجيل -- الرمز: {otp_code or otp_link}",
                                    page_url=page.url,
                                )

                            self._report("تم التحقق -- جاري إكمال إنشاء الملف الشخصي...")
                            profile_result = await self._continue_profile_setup(
                                page, email, password, first, last, username, phone,
                                api_responses
                            )
                            return profile_result

                        else:
                            if otp_attempt < max_otp_attempts:
                                self._report(f"لم يصل الرمز -- جاري إعادة الإرسال... (المحاولة {otp_attempt + 1})")
                                resent = await self._click_resend_otp(page)
                                if resent:
                                    log.info("Resend OTP clicked, attempt %d", otp_attempt + 1)
                                    await asyncio.sleep(2)
                                    continue
                                else:
                                    log.info("No resend button found, giving up")
                                    return RegistrationResult(
                                        True, needs_otp=True,
                                        message="تم التسجيل -- لم يصل رمز التحقق",
                                        page_url=result.page_url,
                                    )
                            else:
                                return RegistrationResult(
                                    True, needs_otp=True,
                                    message="تم التسجيل -- لم يصل رمز التحقق بعد عدة محاولات",
                                    page_url=result.page_url,
                                )
                    except Exception as exc:
                        log.warning("OTP flow error (attempt %d): %s", otp_attempt, exc)
                        if otp_attempt < max_otp_attempts:
                            resent = await self._click_resend_otp(page)
                            if resent:
                                continue
                        return RegistrationResult(
                            True, needs_otp=True,
                            message=f"تم التسجيل -- خطأ في التحقق: {exc}",
                            page_url=result.page_url,
                        )

            if not result.needs_otp and result.success:
                more_inputs = await self._wait_for_inputs(page, max_wait=5)
                if more_inputs > 0:
                    self._report("جاري إكمال إنشاء الملف الشخصي...")
                    profile_result = await self._continue_profile_setup(
                        page, email, password, first, last, username, phone,
                        api_responses
                    )
                    return profile_result

            # تأكيد الحساب: تحقق من صحة الإنشاء الفعلي
            if result.success and not result.needs_otp:
                self._report("التحقق من تأكيد الحساب...")
                confirmed = await self._post_registration_verify(page, site_url, email)
                if confirmed:
                    result.account_confirmed = True
                    result.message = result.message.replace("تم إنشاء الحساب بنجاح", "✅ تم إنشاء الحساب وتأكيده")
                    if "✅" not in result.message:
                        result.message = "✅ " + result.message
                    log.info("Account confirmed for %s at %s", email[:6], site_url)

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
            if xvfb_proc:
                try:
                    xvfb_proc.terminate()
                    xvfb_proc.wait(timeout=3)
                except Exception:
                    try:
                        xvfb_proc.kill()
                    except Exception:
                        pass

    def _report(self, msg: str):
        log.info("-> %s", msg)
        if self._progress_callback:
            try:
                self._progress_callback(msg)
            except Exception:
                pass

    # عناوين تُعدّ آمنة من CF (Auth0 وغيرها تُظهر "just a moment" بشكل طبيعي)
    _CF_EXEMPT_HOSTS = {"auth.openai.com", "auth0.com", "auth.canva.com"}

    async def _wait_for_cf(self, page, max_wait: float = 12.0) -> bool:
        """Wait for Cloudflare/bot-check challenge to resolve.
        Returns True if page loaded successfully, False if still blocked.
        """
        # على نطاقات Auth آمنة، نتجاهل بعض العبارات الخاطئة
        try:
            from urllib.parse import urlparse as _up
            _host = _up(page.url).netloc.lstrip("www.").lower()
        except Exception:
            _host = ""
        _is_exempt = _host in self._CF_EXEMPT_HOSTS

        # عبارات CF الحقيقية فقط (لا تُستخدم مع نطاقات Auth المعفاة من CF)
        _STRICT_CF = [
            "checking your browser", "ray id", "ddos protection",
            "verify you are human", "attention required",
        ]
        _phrases_to_use = _STRICT_CF if _is_exempt else _CF_WAIT_PHRASES

        elapsed = 0.0
        interval = 1.5
        while elapsed < max_wait:
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                body = ""

            is_challenge = any(phrase in body for phrase in _phrases_to_use)
            if not is_challenge:
                return True

            log.debug("CF challenge detected (%s), waiting... (%.1fs)", _host, elapsed)
            await asyncio.sleep(interval)
            elapsed += interval

        log.warning("CF challenge did not resolve after %.1fs on %s", max_wait, _host)
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
                await asyncio.sleep(1)
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
                # قد يكون الحقول مملوءة مسبقاً (مثل auth.openai.com بعد إدخال الإيميل)
                # تحقق إذا كانت هناك أزرار Continue/Next مرئية — إذا نعم، اضغطها
                visible_inputs = await self._count_visible_inputs(page)
                if visible_inputs == 0:
                    # لا يوجد أي حقول — انتهى التسجيل
                    break
                # يوجد حقول مملوءة مسبقاً — حاول الضغط على Continue
                log.info("-> Step %d: 0 new fields filled but %d inputs visible "
                         "(pre-filled) — trying to submit", step, visible_inputs)
                before_url_prefill = page.url
                submitted_prefill = await self._smart_submit(page)
                if submitted_prefill:
                    await asyncio.sleep(2)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    if page.url.rstrip("/") != before_url_prefill.rstrip("/"):
                        # URL تغيّر — تابع الحلقة من جديد
                        await self._wait_for_spa(page)
                        log.info("-> Prefill-submit: URL changed to %s", page.url[:80])
                        continue
                # لا تغيّر في URL أو لم نجد زر → توقف
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

            await asyncio.sleep(1.5)

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

            if page.url.rstrip("/") != before_url.rstrip("/"):
                new_url = page.url.lower()
                log.info("-> URL changed: %s → %s", before_url[:60], page.url[:80])

                if "/auth/error" in new_url or "/api/auth/error" in new_url:
                    log.warning("-> Auth error page detected: %s — sentinel may have blocked", page.url[:80])
                    return RegistrationResult(
                        False,
                        message="فشل التحقق من الحماية — جرب بروكسي مختلف أو حاول لاحقاً",
                        page_url=page.url,
                    )

                _AUTH_HOSTS = ["auth.openai.com", "auth0.com", "accounts.google.com",
                               "login.microsoftonline.com"]
                if any(h in new_url for h in _AUTH_HOSTS):
                    log.info("-> Auth redirect detected: %s — waiting for networkidle + form...", page.url[:80])
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=8000)
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    await self._simulate_human(page, duration=2.0)
                    await self._log_page_state(page, "post-auth-redirect")

                cf_ok = await self._wait_for_cf(page, max_wait=20)
                if not cf_ok:
                    try:
                        body_cf = (await page.inner_text("body")).lower()
                    except Exception:
                        body_cf = ""

                    # Arkose Labs قبل Cloudflare — يحتاج معالجة مختلفة
                    if await self._detect_arkose(page) or any(p in body_cf for p in _ARKOSE_PHRASES):
                        arkose_resolved = await self._handle_arkose(page)
                        if not arkose_resolved:
                            return RegistrationResult(
                                False,
                                message="يحتاج حل Arkose captcha — فعّل CapMonster/2Captcha أو جرب لاحقاً",
                                page_url=page.url,
                            )
                    elif any(p in body_cf for p in _CF_WAIT_PHRASES):
                        return RegistrationResult(
                            False,
                            message="Cloudflare تحجب الصفحة -- جرب بروكسي مختلف أو حاول لاحقاً",
                            page_url=page.url,
                        )
                await self._wait_for_spa(page)

            step_result = await self._analyze(page, before_url, api_responses)
            last_result = step_result
            if not step_result.success:
                return step_result

            has_api_success = any(
                method == "POST" and 200 <= status < 300
                and any(k in urlparse(url).path.lower() for k in [
                    "register", "signup", "sign-up", "auth", "account",
                    "join", "create", "password",
                ])
                for status, url, method in api_responses
            )

            _REG_API_KW = ["register", "signup", "sign-up", "join", "create-account"]
            has_register_api = False
            for status, url, method in api_responses:
                if method == "POST" and 200 <= status < 300:
                    url_path = urlparse(url).path.lower()
                    if any(k in url_path for k in _REG_API_KW):
                        log.info("-> register API match: %s %s", status, url[:120])
                        has_register_api = True
                        break

            if step_result.needs_otp:
                can_use_pw = await self._try_continue_with_password(page)
                if can_use_pw:
                    self._report("اختيار التسجيل بالباسوورد بدل OTP...")
                    await asyncio.sleep(2)
                    await self._wait_for_spa(page)
                    continue
                return step_result

            # انتظار أطول لصفحات auth التي تحتاج وقتاً لتحميل النموذج التالي
            _AUTH_NEXT = ["auth.openai.com", "auth0.com", "accounts.google.com",
                          "/auth/", "/signin", "/login", "/sso"]
            _auth_redirect = any(d in page.url.lower() for d in _AUTH_NEXT)
            _input_wait = 18.0 if _auth_redirect else 8.0
            new_inputs = await self._wait_for_inputs(page, max_wait=_input_wait)

            if has_register_api and new_inputs == 0:
                return RegistrationResult(
                    True,
                    message="تم إنشاء الحساب بنجاح",
                    page_url=page.url
                )

            if any(kw in step_result.message for kw in ["تم إنشاء الحساب", "بنجاح"]):
                if new_inputs == 0:
                    return step_result

            if new_inputs == 0:
                if has_api_success:
                    step_result.message = "تم إنشاء الحساب بنجاح"
                return step_result

            if has_register_api:
                log.info("-> register API succeeded but %d more inputs found -- continuing", new_inputs)

            self._report(f"مرحلة {step + 1} -- ملء حقول إضافية...")

        if last_result:
            return last_result

        return RegistrationResult(
            True,
            message="تم إرسال النموذج -- تحقق من النتيجة",
            page_url=page.url
        )

    async def _continue_profile_setup(
        self, page, email, password, first, last, username, phone, api_responses
    ) -> RegistrationResult:
        max_profile_steps = 5
        last_result = None

        for step in range(1, max_profile_steps + 1):
            await asyncio.sleep(1)
            await self._wait_for_spa(page)

            log.info("-> Profile step %d: URL=%s", step, page.url[:120])
            await self._dump_page_elements(page)

            body = ""
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                pass

            if any(kw in body for kw in _SUCCESS_KEYWORDS):
                ok_clicked = await self._click_confirm_dialog(page)
                if ok_clicked:
                    self._report("تم تأكيد إنشاء الحساب")
                    await asyncio.sleep(1)
                return RegistrationResult(
                    True,
                    message="تم إنشاء الحساب وإكمال الملف الشخصي بنجاح",
                    page_url=page.url,
                )

            new_inputs = await self._wait_for_inputs(page, max_wait=4)
            if new_inputs == 0:
                skip_btn = await self._find_skip_button(page)
                if skip_btn:
                    try:
                        await skip_btn.click()
                        self._report(f"تخطي الخطوة {step}...")
                        await asyncio.sleep(2)
                        await self._wait_for_spa(page)
                        continue
                    except Exception:
                        pass
                if last_result:
                    return last_result
                return RegistrationResult(
                    True,
                    message="تم التحقق وإنشاء الحساب بنجاح",
                    page_url=page.url,
                )

            seg_filled = await self._try_fill_segmented_birthday(page)
            date_filled = await self._try_fill_date_picker(page) if seg_filled == 0 else 0

            filled_count = await self._smart_fill(
                page, email, password, first, last, username, phone
            )
            filled_count += date_filled + seg_filled

            if filled_count == 0:
                body_fill = await self._try_fill_by_page_context(page, email, first, last, username, phone)
                filled_count += body_fill

            if filled_count == 0:
                ok_clicked = await self._click_confirm_dialog(page)
                if ok_clicked:
                    self._report("تم تأكيد إنشاء الحساب")
                    await asyncio.sleep(1)
                    body2 = ""
                    try:
                        body2 = (await page.inner_text("body")).lower()
                    except Exception:
                        pass
                    if any(kw in body2 for kw in _SUCCESS_KEYWORDS):
                        return RegistrationResult(
                            True,
                            message="تم إنشاء الحساب وإكمال الملف الشخصي بنجاح",
                            page_url=page.url,
                        )
                    continue

                continue_btn = await self._find_continue_button(page)
                if continue_btn:
                    self._report(f"إكمال الملف الشخصي -- الخطوة {step} (متابعة)...")
                    before_url = page.url
                    before_body = ""
                    try:
                        before_body = (await page.inner_text("body"))[:200]
                    except Exception:
                        pass
                    try:
                        await continue_btn.click()
                        await asyncio.sleep(1.5)
                        await self._wait_for_spa(page)
                        url_changed = page.url.rstrip("/") != before_url.rstrip("/")
                        dom_changed = False
                        try:
                            after_body = (await page.inner_text("body"))[:200]
                            dom_changed = after_body != before_body
                        except Exception:
                            pass
                        if url_changed or dom_changed:
                            continue
                    except Exception:
                        pass

                skip_btn = await self._find_skip_button(page)
                if skip_btn:
                    try:
                        await skip_btn.click()
                        self._report(f"تخطي الخطوة {step}...")
                        await asyncio.sleep(2)
                        await self._wait_for_spa(page)
                        continue
                    except Exception:
                        pass
                if last_result:
                    return last_result
                return RegistrationResult(
                    True,
                    message="تم التحقق وإنشاء الحساب بنجاح",
                    page_url=page.url,
                )

            self._report(f"إكمال الملف الشخصي -- الخطوة {step}...")
            before_url = page.url
            api_responses.clear()
            submitted = await self._smart_submit(page)
            if not submitted:
                skip_btn = await self._find_skip_button(page)
                if skip_btn:
                    try:
                        await skip_btn.click()
                        self._report(f"تخطي...")
                        await asyncio.sleep(1)
                        continue
                    except Exception:
                        pass
                break

            await asyncio.sleep(1.5)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

            step_result = await self._analyze(page, before_url, api_responses)
            last_result = step_result
            if not step_result.success:
                return step_result

        if last_result:
            last_result.message = "تم إنشاء الحساب وإكمال الملف الشخصي بنجاح"
            return last_result
        return RegistrationResult(
            True,
            message="تم إنشاء الحساب وإكمال الملف الشخصي بنجاح",
            page_url=page.url,
        )

    async def _try_fill_by_page_context(self, page, email, first, last, username, phone) -> int:
        filled = 0
        try:
            body = ""
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                return 0

            page_url = page.url.lower()

            inputs = await page.query_selector_all("input")
            visible_text_inputs = []
            for inp in inputs:
                try:
                    if not await inp.is_visible():
                        continue
                    inp_type = (await inp.get_attribute("type") or "text").lower()
                    if inp_type in ("hidden", "submit", "button", "file",
                                    "image", "reset", "checkbox", "radio"):
                        continue
                    current_val = await inp.input_value()
                    if current_val and len(current_val) > 2:
                        continue
                    visible_text_inputs.append((inp, inp_type))
                except Exception:
                    continue

            is_birthday_page = any(kw in body for kw in [
                "birthday", "date of birth", "birth date", "how old",
                "your age", "when were you born", "تاريخ الميلاد",
                "تاريخ ميلادك", "عمرك",
            ]) or any(kw in page_url for kw in [
                "about-you", "about_you", "birthday", "dob", "age",
            ])

            is_name_page = any(kw in body for kw in [
                "your name", "full name", "first name", "last name",
                "display name", "what should we call you",
                "اسمك", "الاسم الكامل",
            ])

            is_profile_page = any(kw in body for kw in [
                "tell us about", "about yourself", "complete your profile",
                "set up your", "personalize", "customize",
                "أكمل ملفك", "عن نفسك",
            ]) or "profile" in page_url or "onboard" in page_url

            if is_birthday_page and visible_text_inputs:
                for inp, inp_type in visible_text_inputs:
                    try:
                        if inp_type == "date":
                            await self._fill_field(inp, "1995-06-15")
                        else:
                            await self._fill_field(inp, "06/15/1995")
                            try:
                                await inp.evaluate(
                                    '(el) => { '
                                    'el.dispatchEvent(new Event("input", {bubbles:true})); '
                                    'el.dispatchEvent(new Event("change", {bubbles:true})); }'
                                )
                            except Exception:
                                pass
                        filled += 1
                        log.info("-> Context fill: birthday=06/15/1995 (page context match)")
                        break
                    except Exception:
                        continue

            if is_name_page and visible_text_inputs:
                first_filled = False
                last_filled = False
                for inp, inp_type in visible_text_inputs:
                    try:
                        hint = ""
                        for attr in ["name", "id", "placeholder", "aria-label", "autocomplete"]:
                            val = await inp.get_attribute(attr)
                            if val:
                                hint += f" {val.lower()}"
                        if not first_filled and any(k in hint for k in ["first", "given", "fname"]):
                            await self._fill_field(inp, first)
                            filled += 1
                            first_filled = True
                            log.info("-> Context fill: first=%s (hint match)", first)
                        elif not last_filled and any(k in hint for k in ["last", "family", "lname", "surname"]):
                            await self._fill_field(inp, last)
                            filled += 1
                            last_filled = True
                            log.info("-> Context fill: last=%s (hint match)", last)
                        elif any(k in hint for k in ["display", "full", "name"]) and not first_filled:
                            await self._fill_field(inp, f"{first} {last}")
                            filled += 1
                            first_filled = True
                            log.info("-> Context fill: full name=%s %s", first, last)
                    except Exception:
                        continue
                if filled == 0 and len(visible_text_inputs) <= 2:
                    for idx, (inp, inp_type) in enumerate(visible_text_inputs):
                        try:
                            if idx == 0:
                                await self._fill_field(inp, first)
                                filled += 1
                                log.info("-> Context fill: first=%s (positional)", first)
                            elif idx == 1:
                                await self._fill_field(inp, last)
                                filled += 1
                                log.info("-> Context fill: last=%s (positional)", last)
                        except Exception:
                            continue

            if filled == 0 and is_profile_page and visible_text_inputs:
                for inp, inp_type in visible_text_inputs:
                    try:
                        parent_text = ""
                        try:
                            parent_text = await inp.evaluate(
                                "(el) => { const p = el.closest('label, fieldset') || el.parentElement; "
                                "return p ? p.innerText.toLowerCase() : ''; }"
                            )
                        except Exception:
                            pass
                        hint = ""
                        for attr in ["name", "id", "placeholder", "aria-label"]:
                            val = await inp.get_attribute(attr)
                            if val:
                                hint += f" {val.lower()}"
                        combined = f"{parent_text} {hint}"

                        if any(k in combined for k in ["birth", "dob", "age", "birthday", "تاريخ"]):
                            await self._fill_field(inp, "06/15/1995")
                            filled += 1
                            log.info("-> Context fill (parent): birthday")
                        elif any(k in combined for k in ["name", "اسم"]):
                            await self._fill_field(inp, f"{first} {last}")
                            filled += 1
                            log.info("-> Context fill (parent): name")
                        elif any(k in combined for k in ["phone", "هاتف", "mobile"]):
                            await self._fill_field(inp, phone)
                            filled += 1
                            log.info("-> Context fill (parent): phone")
                    except Exception:
                        continue

            checkboxes = await page.query_selector_all('input[type="checkbox"]')
            for cb in checkboxes:
                try:
                    if await cb.is_visible() and not await cb.is_checked():
                        await cb.check()
                        filled += 1
                        log.info("-> Context fill: checkbox checked")
                except Exception:
                    pass

        except Exception as exc:
            log.debug("Context fill error: %s", exc)

        return filled

    async def _dump_page_elements(self, page):
        try:
            body_text = ""
            try:
                body_text = (await page.inner_text("body"))[:300]
            except Exception:
                pass
            log.info("-> Page body preview: %s", body_text.replace("\n", " | ")[:300])

            inputs = await page.query_selector_all("input")
            for inp in inputs:
                try:
                    visible = await inp.is_visible()
                    if not visible:
                        continue
                    inp_type = (await inp.get_attribute("type") or "text").lower()
                    if inp_type in ("hidden", "submit", "button"):
                        continue
                    name = await inp.get_attribute("name") or ""
                    inp_id = await inp.get_attribute("id") or ""
                    placeholder = await inp.get_attribute("placeholder") or ""
                    autocomplete = await inp.get_attribute("autocomplete") or ""
                    aria = await inp.get_attribute("aria-label") or ""
                    val = await inp.input_value()
                    log.info("-> INPUT: type=%s name=%s id=%s ph=%s auto=%s aria=%s val=%s",
                             inp_type, name, inp_id, placeholder[:30], autocomplete, aria[:30], val[:20])
                except Exception:
                    continue

            selects = await page.query_selector_all("select")
            for sel in selects:
                try:
                    if await sel.is_visible():
                        name = await sel.get_attribute("name") or ""
                        sel_id = await sel.get_attribute("id") or ""
                        aria = await sel.get_attribute("aria-label") or ""
                        opts = await sel.query_selector_all("option")
                        log.info("-> SELECT: name=%s id=%s aria=%s options=%d", name, sel_id, aria[:30], len(opts))
                except Exception:
                    continue

            buttons = await page.query_selector_all("button")
            for btn in buttons:
                try:
                    if await btn.is_visible():
                        text = (await btn.inner_text())[:50]
                        btn_type = await btn.get_attribute("type") or ""
                        log.info("-> BUTTON: type=%s text=%s", btn_type, text.replace("\n", " "))
                except Exception:
                    continue

            spinbuttons = await page.query_selector_all('[role="spinbutton"]')
            for sb in spinbuttons:
                try:
                    if await sb.is_visible():
                        aria = await sb.get_attribute("aria-label") or ""
                        text = (await sb.inner_text())[:20]
                        log.info("-> SPINBUTTON: aria=%s text=%s", aria, text)
                except Exception:
                    continue

            num_inputs = await page.query_selector_all('input[inputmode="numeric"]')
            for ni in num_inputs:
                try:
                    if await ni.is_visible():
                        name = await ni.get_attribute("name") or ""
                        maxlen = await ni.get_attribute("maxlength") or ""
                        val = await ni.input_value()
                        log.info("-> NUMERIC_INPUT: name=%s maxlen=%s val=%s", name, maxlen, val)
                except Exception:
                    continue
        except Exception as exc:
            log.debug("Dump page error: %s", exc)

    async def _try_fill_segmented_birthday(self, page) -> int:
        filled = 0
        try:
            spinbuttons = await page.query_selector_all('[role="spinbutton"]')
            visible_spins = []
            for sb in spinbuttons:
                try:
                    if await sb.is_visible():
                        visible_spins.append(sb)
                except Exception:
                    continue

            if len(visible_spins) >= 3:
                vals = ["06", "15", "1995"]
                for i, sb in enumerate(visible_spins[:3]):
                    try:
                        await sb.click()
                        await asyncio.sleep(0.15)
                        await sb.press("Control+a")
                        await asyncio.sleep(0.05)
                        await sb.type(vals[i], delay=80)
                        await asyncio.sleep(0.1)
                        await sb.press("Tab")
                        filled += 1
                    except Exception:
                        try:
                            await sb.evaluate(f"""(el) => {{
                                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value'
                                );
                                if (nativeInputValueSetter && nativeInputValueSetter.set) {{
                                    nativeInputValueSetter.set.call(el, '{vals[i]}');
                                }} else {{
                                    el.textContent = '{vals[i]}';
                                }}
                                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}""")
                            filled += 1
                        except Exception:
                            pass
                if filled > 0:
                    log.info("-> Filled %d segmented birthday spinbuttons", filled)
                return filled

            num_inputs = await page.query_selector_all('input[inputmode="numeric"]')
            visible_nums = []
            for ni in num_inputs:
                try:
                    if await ni.is_visible():
                        val = await ni.input_value()
                        visible_nums.append((ni, val))
                except Exception:
                    continue

            if len(visible_nums) >= 3:
                vals = ["06", "15", "1995"]
                for i, (ni, current_val) in enumerate(visible_nums[:3]):
                    try:
                        await ni.click()
                        await asyncio.sleep(0.1)
                        await ni.fill("")
                        await ni.type(vals[i], delay=50)
                        filled += 1
                    except Exception:
                        pass
                if filled > 0:
                    log.info("-> Filled %d segmented birthday numeric inputs", filled)
                return filled

            body = ""
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                pass
            if any(kw in body for kw in ["birthday", "date of birth", "confirm your age"]):
                small_inputs = await page.query_selector_all(
                    'input[maxlength="2"], input[maxlength="4"], '
                    'input[size="2"], input[size="4"]'
                )
                visible_small = []
                for si in small_inputs:
                    try:
                        if await si.is_visible():
                            visible_small.append(si)
                    except Exception:
                        continue
                if len(visible_small) >= 3:
                    vals = ["06", "15", "1995"]
                    for i, si in enumerate(visible_small[:3]):
                        try:
                            await si.click()
                            await asyncio.sleep(0.1)
                            await si.fill(vals[i])
                            filled += 1
                        except Exception:
                            pass
                    if filled > 0:
                        log.info("-> Filled %d segmented birthday small inputs", filled)

        except Exception as exc:
            log.debug("Segmented birthday error: %s", exc)
        return filled

    async def _try_fill_date_picker(self, page) -> int:
        filled = 0
        try:
            selects = await page.query_selector_all("select")
            visible_selects = []
            for sel in selects:
                try:
                    if await sel.is_visible():
                        visible_selects.append(sel)
                except Exception:
                    continue

            if len(visible_selects) >= 3:
                for sel in visible_selects:
                    sel_name = (await sel.get_attribute("name") or "").lower()
                    sel_id = (await sel.get_attribute("id") or "").lower()
                    sel_aria = (await sel.get_attribute("aria-label") or "").lower()
                    sel_hint = f"{sel_name} {sel_id} {sel_aria}"
                    options = await sel.query_selector_all("option")
                    option_count = len(options)

                    try:
                        if any(k in sel_hint for k in ["month", "شهر"]):
                            await sel.select_option(index=min(6, option_count - 1))
                            filled += 1
                        elif any(k in sel_hint for k in ["day", "يوم"]):
                            await sel.select_option(index=min(15, option_count - 1))
                            filled += 1
                        elif any(k in sel_hint for k in ["year", "سنة"]):
                            for opt in options:
                                val = await opt.get_attribute("value")
                                text = await opt.inner_text()
                                if val and ("1995" in str(val) or "1995" in text):
                                    await sel.select_option(val)
                                    filled += 1
                                    break
                            else:
                                mid = max(1, option_count // 3)
                                val = await options[mid].get_attribute("value")
                                if val:
                                    await sel.select_option(val)
                                    filled += 1
                    except Exception:
                        continue

            if filled == 0:
                date_input = page.locator(
                    'input[type="date"], '
                    'input[name*="birth" i], input[name*="dob" i], '
                    'input[name*="birthday" i], input[id*="birth" i], '
                    'input[id*="dob" i], input[placeholder*="birth" i], '
                    'input[placeholder*="MM/DD" i], input[placeholder*="DD/MM" i], '
                    'input[placeholder*="YYYY" i]'
                ).first
                try:
                    if await date_input.is_visible(timeout=500):
                        await date_input.click()
                        await asyncio.sleep(0.2)
                        await date_input.fill("06/15/1995")
                        await asyncio.sleep(0.2)
                        try:
                            await date_input.evaluate(
                                '(el) => { el.value = "1995-06-15"; '
                                'el.dispatchEvent(new Event("input", {bubbles:true})); '
                                'el.dispatchEvent(new Event("change", {bubbles:true})); }'
                            )
                        except Exception:
                            pass
                        filled += 1
                        log.info("-> Filled date picker: 1995-06-15")
                except Exception:
                    pass

        except Exception as exc:
            log.debug("Date picker fill error: %s", exc)

        if filled > 0:
            log.info("-> Date picker: filled %d components", filled)
        return filled

    async def _find_continue_button(self, page):
        continue_texts = [
            "continue", "next", "submit", "done", "finish",
            "complete", "save", "agree", "accept", "confirm",
            "متابعة", "التالي", "إرسال", "حفظ", "إنهاء", "موافق",
            "get started", "let's go", "start", "proceed",
        ]
        for text in continue_texts:
            try:
                btn = page.locator(
                    f'button:has-text("{text}"), '
                    f'[role="button"]:has-text("{text}")'
                ).first
                if await btn.is_visible(timeout=500):
                    return btn
            except Exception:
                continue

        for sel in ['button[type="submit"]', 'input[type="submit"]']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=500):
                    return btn
            except Exception:
                continue

        return None

    async def _click_confirm_dialog(self, page) -> bool:
        confirm_texts = [
            "ok", "okay", "got it", "continue", "done", "agree",
            "accept", "confirm", "let's go", "start", "finish",
            "تم", "موافق", "حسناً", "متابعة",
        ]
        for text in confirm_texts:
            try:
                btn = page.locator(
                    f'button:has-text("{text}"), [role="button"]:has-text("{text}")'
                ).first
                if await btn.is_visible(timeout=300):
                    btn_text = (await btn.inner_text()).strip()
                    if len(btn_text) < 30:
                        log.info("-> Clicking confirm dialog: '%s'", btn_text)
                        await btn.click()
                        await asyncio.sleep(1)
                        return True
            except Exception:
                continue
        return False

    async def _click_resend_otp(self, page) -> bool:
        resend_texts = [
            "resend", "resend code", "resend email", "send again",
            "didn't receive", "didn't get", "try again",
            "send new code", "request new code", "new code",
            "إعادة إرسال", "إعادة الإرسال", "أرسل مرة أخرى",
            "ارسل مرة اخرى", "لم يصل", "رمز جديد",
        ]
        for text in resend_texts:
            try:
                btn = page.locator(
                    f'button:has-text("{text}"), a:has-text("{text}"), '
                    f'[role="button"]:has-text("{text}"), '
                    f'span:has-text("{text}"), div[role="link"]:has-text("{text}")'
                ).first
                if await btn.is_visible(timeout=500):
                    btn_text = (await btn.inner_text()).strip()
                    if len(btn_text) < 60:
                        log.info("-> Clicking resend OTP: '%s'", btn_text)
                        await btn.click()
                        await asyncio.sleep(2)
                        return True
            except Exception:
                continue

        try:
            links = page.locator('a, button, [role="button"], span[tabindex]')
            count = await links.count()
            for i in range(count):
                el = links.nth(i)
                try:
                    if not await el.is_visible(timeout=200):
                        continue
                    el_text = (await el.inner_text()).strip().lower()
                    if any(kw in el_text for kw in ["resend", "send again", "new code", "إعادة"]):
                        log.info("-> Found resend via scan: '%s'", el_text[:40])
                        await el.click()
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        return False

    async def _find_skip_button(self, page):
        skip_texts = [
            "skip", "تخطي", "not now", "later", "maybe later",
            "skip for now", "i'll do this later", "no thanks",
            "remind me later", "dismiss",
        ]
        for text in skip_texts:
            try:
                btn = page.locator(
                    f'button:has-text("{text}"), a:has-text("{text}"), '
                    f'[role="button"]:has-text("{text}")'
                ).first
                if await btn.is_visible(timeout=500):
                    return btn
            except Exception:
                continue
        return None

    async def _fill_otp_code(self, page, code: str) -> RegistrationResult | None:
        await asyncio.sleep(0.5)
        await self._wait_for_spa(page)

        otp_selectors = [
            'input[name*="code" i]', 'input[name*="otp" i]',
            'input[name*="token" i]', 'input[name*="verification" i]',
            'input[name*="verify" i]', 'input[autocomplete="one-time-code"]',
            'input[placeholder*="code" i]', 'input[placeholder*="رمز" i]',
            'input[aria-label*="code" i]', 'input[aria-label*="verification" i]',
        ]

        otp_input = None
        for sel in otp_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    otp_input = loc
                    break
            except Exception:
                continue

        if not otp_input:
            digit_inputs = page.locator('input[maxlength="1"]')
            count = await digit_inputs.count()
            if count >= 4:
                for i in range(min(count, len(code))):
                    try:
                        d = digit_inputs.nth(i)
                        if await d.is_visible(timeout=300):
                            await d.fill(code[i])
                            await asyncio.sleep(0.1)
                    except Exception:
                        pass
                await asyncio.sleep(0.5)
                self._report("تم إدخال رمز التحقق")
                await asyncio.sleep(1.5)
                await self._wait_for_spa(page)
                body = ""
                try:
                    body = (await page.inner_text("body")).lower()
                except Exception:
                    pass
                if any(kw in body for kw in _SUCCESS_KEYWORDS):
                    return RegistrationResult(
                        True,
                        message="تم التحقق وإنشاء الحساب بنجاح",
                        page_url=page.url,
                    )
                return RegistrationResult(
                    True,
                    message=f"تم إدخال الرمز {code}",
                    page_url=page.url,
                )

        if not otp_input:
            inputs = await page.query_selector_all("input")
            for inp in inputs:
                try:
                    if not await inp.is_visible():
                        continue
                    inp_type = (await inp.get_attribute("type") or "text").lower()
                    if inp_type in ("hidden", "submit", "button", "file",
                                    "image", "reset", "email", "password"):
                        continue
                    otp_input = inp
                    break
                except Exception:
                    continue

        if not otp_input:
            log.warning("No OTP input found on page %s", page.url[:80])
            return None

        try:
            try:
                await otp_input.fill(code)
            except Exception:
                await otp_input.click(force=True)
                await asyncio.sleep(0.2)
                await otp_input.fill(code)
            await asyncio.sleep(0.3)
        except Exception as exc:
            try:
                await otp_input.focus()
                await page.keyboard.type(code, delay=50)
                await asyncio.sleep(0.3)
            except Exception:
                log.warning("Failed to fill OTP: %s", exc)
                return None

        self._report("تم إدخال رمز التحقق -- جاري الإرسال...")
        submitted = await self._smart_submit(page)
        if not submitted:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass

        await asyncio.sleep(1.5)
        await self._wait_for_spa(page)

        body = ""
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            pass

        if any(kw in body for kw in _ERROR_KEYWORDS):
            return RegistrationResult(
                False,
                message="رمز التحقق غير صحيح",
                page_url=page.url,
            )

        if any(kw in body for kw in _SUCCESS_KEYWORDS):
            return RegistrationResult(
                True,
                message="تم التحقق وإنشاء الحساب بنجاح",
                page_url=page.url,
            )

        return RegistrationResult(
            True,
            message=f"تم إدخال الرمز {code} -- تحقق من الحساب",
            page_url=page.url,
        )

    async def _open_verification_link(self, page, context, link: str) -> RegistrationResult | None:
        try:
            parsed_link = urlparse(link)
            host = parsed_link.hostname or ""
            if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", ""):
                log.warning("Blocked private verification link: %s", link[:80])
                return None
            if host.endswith(".local") or host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
                log.warning("Blocked internal verification link: %s", link[:80])
                return None

            new_page = await context.new_page()
            resp = await new_page.goto(link, timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            await self._wait_for_spa(new_page)

            body = ""
            try:
                body = (await new_page.inner_text("body")).lower()
            except Exception:
                pass

            if any(kw in body for kw in _SUCCESS_KEYWORDS):
                self._report("تم التحقق عبر الرابط بنجاح")
                return RegistrationResult(
                    True,
                    message="تم التحقق وإنشاء الحساب بنجاح",
                    page_url=new_page.url,
                )

            if any(kw in body for kw in _ERROR_KEYWORDS):
                return RegistrationResult(
                    False,
                    message="رابط التحقق غير صالح أو منتهي",
                    page_url=new_page.url,
                )

            status = resp.status if resp else 0
            if 200 <= status < 400:
                self._report("تم فتح رابط التحقق")
                return RegistrationResult(
                    True,
                    message="تم فتح رابط التحقق -- تحقق من الحساب",
                    page_url=new_page.url,
                )

            return None
        except Exception as exc:
            log.warning("Verification link error: %s", exc)
            return None

    async def _count_frame_inputs(self, page) -> int:
        """عد الحقول المرئية في جميع الإطارات (main + iframes)."""
        total = 0
        for frame in page.frames:
            try:
                inputs = await frame.query_selector_all("input")
                for inp in inputs:
                    try:
                        if await inp.is_visible():
                            t = (await inp.get_attribute("type") or "text").lower()
                            if t not in ("hidden", "submit", "button", "file", "image", "reset"):
                                total += 1
                    except Exception:
                        pass
            except Exception:
                pass
        return total

    async def _wait_for_inputs(self, page, max_wait: float = 5.0) -> int:
        elapsed = 0.0
        interval = 1.0
        is_auth_page = any(h in page.url.lower() for h in [
            "auth.openai.com", "auth0.com", "accounts.google.com"
        ])
        while elapsed < max_wait:
            count = await self._count_visible_inputs(page)
            if count > 0:
                return count
            if is_auth_page:
                frame_count = await self._count_frame_inputs(page)
                if frame_count > 0:
                    log.info("Found %d inputs in frames on auth page", frame_count)
                    return frame_count
            await asyncio.sleep(interval)
            elapsed += interval

            if is_auth_page and elapsed == 3.0:
                try:
                    await page.wait_for_selector(
                        'input[type="email"], input[type="password"], input[name="email"], input[name="password"]',
                        state="visible",
                        timeout=int((max_wait - elapsed) * 1000),
                    )
                    return await self._count_visible_inputs(page) or 1
                except Exception:
                    pass
                break
        return 0

    async def _try_continue_with_password(self, page) -> bool:
        password_link_texts = [
            "continue with password", "use password",
            "sign in with password", "use a password",
            "log in with password", "enter password instead",
            "continue with email", "sign up with email",
            "register with email", "use email instead",
            "log in with email", "sign in with email",
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

    async def _try_continue_with_email_link(self, page) -> bool:
        email_link_texts = [
            "continue with email", "sign up with email",
            "register with email", "use email instead",
            "log in with email", "sign in with email",
            "email address", "use email",
        ]
        for text in email_link_texts:
            try:
                link = page.get_by_text(text, exact=False).first
                if await link.is_visible(timeout=400):
                    tag = await link.evaluate("el => el.tagName.toLowerCase()")
                    if tag in ("a", "button", "span", "div", "p", "label"):
                        await link.click()
                        log.info("-> Clicked '%s' link to reveal email form", text)
                        return True
            except Exception:
                continue

        for sel in [
            'button:has-text("email")', 'a:has-text("email")',
            '[data-testid*="email"]', '[aria-label*="email" i]',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click()
                    log.info("-> Clicked email button via selector: %s", sel)
                    return True
            except Exception:
                continue

        return False

    async def _navigate_to_register(self, page, site_url: str) -> bool:
        parsed = urlparse(site_url)
        has_path = parsed.path not in ("", "/")
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        host = parsed.netloc.lower().lstrip("www.")

        # ---- تدفق خاص لـ ChatGPT ----
        if host in _CHATGPT_HOSTS:
            return await self._navigate_chatgpt_signup(page)

        direct_auth = _DIRECT_AUTH_URLS.get(host)
        if direct_auth:
            log.info("-> Using direct auth URL for %s", host)
            self._report(f"فتح صفحة التسجيل مباشرة...")
            if await self._try_url_smart(page, direct_auth):
                return True
            log.warning("Direct auth URL failed for %s, falling back", host)

        _LOGIN_PATHS = ["/auth/login", "/login", "/signin", "/sign-in", "/auth/signin"]
        is_login_url = has_path and any(parsed.path.rstrip("/").lower().endswith(lp.rstrip("/")) for lp in _LOGIN_PATHS)

        if has_path and not is_login_url:
            if await self._try_url_smart(page, site_url):
                return True

        homepage_loaded = await self._load_homepage(page, base_url)

        if homepage_loaded:
            if await self._has_fillable_form(page, require_register_context=True):
                return True

            signup_btn_texts = [
                "sign up for free", "sign up", "signup", "register",
                "create account", "get started", "إنشاء حساب",
                "سجل الآن", "سجل", "تسجيل",
            ]
            btn_clicked = False
            for text in signup_btn_texts:
                try:
                    btn = page.get_by_text(text, exact=False).first
                    if await btn.is_visible(timeout=400):
                        before_url = page.url
                        await btn.click()
                        await asyncio.sleep(1.5)
                        await self._wait_for_spa(page)
                        if page.url.rstrip("/") != before_url.rstrip("/"):
                            await self._wait_for_cf(page, max_wait=15)
                        if await self._has_fillable_form(page):
                            return True
                        if await self._has_email_only_form(page):
                            return True
                        clicked_email = await self._try_continue_with_email_link(page)
                        if clicked_email:
                            await asyncio.sleep(1.5)
                            await self._wait_for_spa(page)
                            if await self._has_fillable_form(page):
                                return True
                            if await self._has_email_only_form(page):
                                return True
                        btn_clicked = True
                        break
                except Exception:
                    continue

            if btn_clicked and not await self._has_fillable_form(page):
                log.info("-> Button click didn't navigate, trying JS click fallback...")
                try:
                    before_url = page.url
                    for sel in ['[data-testid*="signup"]', '[data-testid*="sign-up"]',
                                '[data-testid*="register"]']:
                        js_result = await page.evaluate(f"""() => {{
                            const btn = document.querySelector('{sel}');
                            if (!btn) return null;
                            btn.click();
                            return btn.tagName;
                        }}""")
                        if js_result:
                            await asyncio.sleep(2)
                            if page.url.rstrip("/") != before_url.rstrip("/"):
                                await self._wait_for_cf(page, max_wait=15)
                                if await self._has_fillable_form(page):
                                    return True
                                if await self._has_email_only_form(page):
                                    return True
                except Exception:
                    pass

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

        if is_login_url:
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
                cf_ok = await self._wait_for_cf(page, max_wait=20)
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
                        await asyncio.sleep(1.5)
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
                cf_ok = await self._wait_for_cf(page, max_wait=15)
                if not cf_ok:
                    log.debug("CF block at %s (status=%s)", url, status)
                    return False

            elif status >= 400:
                log.debug("_try_url %s -> HTTP %s", url, status)
                return False

            await self._wait_for_spa(page)
            await asyncio.sleep(1)
            if await self._has_fillable_form(page):
                return True
            if await self._has_email_only_form(page):
                log.info("-> Found email-only form at %s", page.url[:80])
                return True

            clicked_email = await self._try_continue_with_email_link(page)
            if clicked_email:
                await asyncio.sleep(1.5)
                await self._wait_for_spa(page)
                if await self._has_fillable_form(page):
                    return True
                if await self._has_email_only_form(page):
                    return True

            if await self._click_register_tab(page):
                await asyncio.sleep(1)
                if await self._has_fillable_form(page):
                    return True
                if await self._has_email_only_form(page):
                    return True
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

        selects = await page.query_selector_all("select")
        for sel in selects:
            try:
                if await sel.is_visible():
                    count += 1
            except Exception:
                continue

        return count

    async def _has_email_only_form(self, page) -> bool:
        # رفض صفحات platform.openai.com/login (API platform وليس ChatGPT)
        current_url = page.url.lower()
        if "platform.openai.com/login" in current_url:
            log.debug("_has_email_only_form: rejecting platform.openai.com/login")
            return False

        email_input = await page.query_selector(
            'input[type="email"], input[name="email"], '
            'input[placeholder*="email" i], input[placeholder*="mail" i], '
            'input[autocomplete="email"], '
            'input[autocomplete="username"], input[name="username"]'
        )
        if not email_input:
            return False
        try:
            if not await email_input.is_visible():
                return False
        except Exception:
            return False

        continue_btn = None
        for text in [
            "continue", "next", "التالي", "متابعة", "إرسال",
            "sign up", "signup", "create account", "get started",
            "register", "إنشاء حساب", "سجل", "تسجيل", "ابدأ",
        ]:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible(timeout=300):
                    continue_btn = btn
                    break
            except Exception:
                continue

        if not continue_btn:
            for sel in ['button[type="submit"]', 'input[type="submit"]',
                        'button[data-testid*="signup"]', 'button[data-testid*="email"]']:
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
                    elif name == "username" or autocomplete == "username":
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
                if current_val and len(current_val) >= 2:
                    continue

                if inp_type == "checkbox" or inp_type == "radio":
                    continue

                if inp_type == "number" and any(k in hint for k in ["age", "عمر"]):
                    if "age" not in filled_types:
                        await self._fill_field(inp, "25")
                        filled.append("age=25")
                        filled_types.add("age")
                        filled_types.add("dob")
                elif inp_type == "date" or any(k in hint for k in [
                    "birth", "dob", "تاريخ", "birthday",
                    "date_of_birth", "date-of-birth", "dateofbirth",
                    "bday", "born",
                ]):
                    if "dob" not in filled_types:
                        if inp_type == "number":
                            await self._fill_field(inp, "25")
                            filled.append("age=25")
                        else:
                            await self._fill_field(inp, "1995-06-15")
                            filled.append("dob=1995-06-15")
                        filled_types.add("dob")
                elif inp_type == "email" or "email" in hint or "mail" in hint:
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
                    "username", "user_name", "user-name",
                    "المستخدم", "اسم المستخدم", "nickname"
                ]):
                    if "email" not in filled_types and (
                        autocomplete == "username" or name == "username"
                    ):
                        await self._fill_field(inp, email)
                        filled.append(f"username(email)={email}")
                        filled_types.add("email")
                        filled_types.add("username")
                    elif "username" not in filled_types:
                        await self._fill_field(inp, username)
                        filled.append(f"username={username}")
                        filled_types.add("username")
                elif any(k in hint for k in [
                    "full_name", "fullname", "full-name",
                    "display_name", "displayname", "display-name",
                    "your name", "اسمك", "الاسم الكامل",
                ]) or (
                    autocomplete == "name" or
                    (name == "name" and "user" not in hint)
                ):
                    await self._fill_field(inp, f"{first} {last}")
                    filled.append(f"name={first} {last}")
                    filled_types.add("name")
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
                else:
                    log.info("-> Unmatched input: type=%s name=%s id=%s placeholder=%s autocomplete=%s",
                             inp_type, name, inp_id, placeholder, autocomplete)
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
                if not await sel.is_visible():
                    continue
                sel_name = (await sel.get_attribute("name") or "").lower()
                sel_id = (await sel.get_attribute("id") or "").lower()
                sel_aria = (await sel.get_attribute("aria-label") or "").lower()
                sel_hint = f"{sel_name} {sel_id} {sel_aria}"

                options = await sel.query_selector_all("option")
                if len(options) <= 1:
                    continue

                if any(k in sel_hint for k in ["month", "شهر", "mm"]):
                    await sel.select_option(index=6)
                    filled.append("select_month=June")
                elif any(k in sel_hint for k in ["day", "يوم", "dd"]):
                    await sel.select_option(index=15)
                    filled.append("select_day=15")
                elif any(k in sel_hint for k in ["year", "سنة", "yyyy"]):
                    for opt in options:
                        val = await opt.get_attribute("value")
                        text = await opt.inner_text()
                        if val and ("1995" in str(val) or "1995" in text):
                            await sel.select_option(val)
                            filled.append("select_year=1995")
                            break
                    else:
                        mid = len(options) // 2
                        val = await options[mid].get_attribute("value")
                        if val:
                            await sel.select_option(val)
                            filled.append(f"select_year={val}")
                else:
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
            await asyncio.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass
        try:
            await inp.fill("")
        except Exception:
            pass
        await inp.type(value, delay=random.randint(30, 90))
        await asyncio.sleep(random.uniform(0.05, 0.2))

    async def _smart_submit(self, page) -> bool:
        _OAUTH_SKIP = [
            "google", "microsoft", "apple", "facebook", "github",
            "twitter", "linkedin", "phone", "sms", "هاتف",
        ]

        submit_texts = [
            "إنشاء حساب", "تسجيل", "سجل", "أنشئ حساب",
            "register", "sign up", "create account", "submit",
            "get started", "join", "continue", "next", "إرسال",
            "التالي", "متابعة", "إنشاء", "create", "start",
        ]

        for text in submit_texts:
            try:
                candidates = page.locator(f'button:has-text("{text}")')
                count = await candidates.count()
                for i in range(min(count, 5)):
                    btn = candidates.nth(i)
                    if await btn.is_visible(timeout=300):
                        btn_text = (await btn.inner_text()).strip().lower()
                        if any(skip in btn_text for skip in _OAUTH_SKIP):
                            continue
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
                    try:
                        btn_text = (await btn.inner_text()).strip().lower()
                    except Exception:
                        btn_text = ""
                    if any(skip in btn_text for skip in _OAUTH_SKIP):
                        continue
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
                        if any(skip in text for skip in _OAUTH_SKIP):
                            continue
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
                url_path = urlparse(url).path.lower()
                if any(k in url_path for k in [
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

            # انتظار إضافي لصفحات auth التي تستغرق وقتاً أطول في التحميل
            _AUTH_DOMAINS = ["auth.openai.com", "auth0.com", "accounts.google.com",
                             "login.microsoftonline.com", "auth.", "/auth/", "/login", "/signin"]
            _is_auth_redirect = any(d in current_url.lower() for d in _AUTH_DOMAINS)
            max_attempts = 9 if _is_auth_redirect else 5  # 16s vs 8s
            sleep_per_attempt = 2

            still_has_inputs = False
            for attempt in range(max_attempts):
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
                    break
                if attempt < max_attempts - 1:
                    await asyncio.sleep(sleep_per_attempt)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=4000)
                    except Exception:
                        pass

            if still_has_inputs:
                return RegistrationResult(
                    True,
                    message="تم إرسال النموذج -- تحقق من النتيجة",
                    page_url=current_url
                )

            if any(k in path for k in [
                "account", "dashboard", "home",
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

    # ================================================================
    # ChatGPT-Specific Signup Flow
    # ================================================================

    # Selector للانتظار على نموذج التسجيل في auth.openai.com
    _OPENAI_EMAIL_SEL = (
        'input[name="email"], input[type="email"], '
        'input[placeholder*="email" i], input[autocomplete="email"]'
    )

    async def _simulate_human(self, page, duration: float = 2.0) -> None:
        """محاكاة سلوك بشري — حركة ماوس + تمرير عشوائي."""
        try:
            vw = page.viewport_size or {"width": 1920, "height": 1080}
            w, h = vw["width"], vw["height"]
            steps = random.randint(3, 6)
            for _ in range(steps):
                x = random.randint(100, w - 100)
                y = random.randint(100, h - 100)
                await page.mouse.move(x, y, steps=random.randint(5, 15))
                await asyncio.sleep(random.uniform(0.1, 0.4))

            if random.random() > 0.5:
                await page.mouse.wheel(0, random.randint(50, 200))
                await asyncio.sleep(random.uniform(0.2, 0.5))

            await asyncio.sleep(max(0, duration - 1.5))
        except Exception:
            await asyncio.sleep(duration)

    async def _wait_for_sentinel(self, page, timeout: float = 15.0) -> None:
        """انتظار sentinel الخاص بـ OpenAI للانتهاء + محاكاة سلوك بشري."""
        log.info("Waiting for sentinel to complete (up to %.0fs)...", timeout)
        await self._simulate_human(page, duration=min(3.0, timeout / 3))

        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            sentinel_done = True
            for frame in page.frames:
                f_url = (frame.url or "").lower()
                if "sentinel" in f_url:
                    try:
                        content = await frame.content()
                        if len(content) < 100:
                            sentinel_done = False
                    except Exception:
                        pass

            if sentinel_done:
                body = ""
                try:
                    body = await page.inner_text("body")
                except Exception:
                    pass
                if body.strip():
                    log.info("Sentinel resolved — page has content")
                    return

            await self._simulate_human(page, duration=1.5)

        log.info("Sentinel wait exhausted")

    async def _wait_for_openai_form(self, page, timeout_ms: int = 25000) -> bool:
        """
        ينتظر حتى يظهر نموذج الإيميل في auth.openai.com
        (React app — يحتاج وقتاً للتهيئة).
        يُرجع True إذا ظهر، False إذا انتهى الوقت.
        """
        log.info("Waiting for OpenAI email form (up to %dms)...", timeout_ms)
        try:
            await page.wait_for_selector(
                self._OPENAI_EMAIL_SEL,
                state="visible",
                timeout=timeout_ms,
            )
            log.info("OpenAI email form appeared at %s", page.url[:80])
            return True
        except Exception:
            await self._log_page_state(page, "form-timeout")
            return False

    async def _navigate_chatgpt_signup(self, page) -> bool:
        """
        تدفق التسجيل الخاص بـ ChatGPT — 3 مراحل مع محاكاة بشرية:
        1. auth.openai.com/u/signup مع sentinel handling
        2. chatgpt.com → Sign up → email modal على chatgpt.com
        3. authorize URL كـ fallback
        """
        self._report("فتح صفحة تسجيل ChatGPT...")

        # ===== المرحلة 1: auth.openai.com/u/signup مع انتظار sentinel =====
        try:
            log.info("ChatGPT: navigating to auth.openai.com/u/signup (headed+stealth)")
            await page.goto(
                "https://auth.openai.com/u/signup",
                timeout=_NAV_TIMEOUT,
                wait_until="domcontentloaded",
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            current = page.url
            log.info("ChatGPT/auth: landed at %s", current[:120])
            await self._log_page_state(page, "auth-direct")

            if "platform.openai.com/login" not in current:
                await self._wait_for_sentinel(page, timeout=20.0)
                if await self._wait_for_openai_form(page, timeout_ms=15000):
                    return True

            log.info("ChatGPT: auth direct failed — trying homepage approach")
        except Exception as exc:
            log.warning("ChatGPT auth direct error: %s", exc)

        # ===== المرحلة 2: chatgpt.com → Sign up =====
        self._report("جاري فتح ChatGPT وضغط Sign up...")
        try:
            await page.goto(
                "https://chatgpt.com",
                timeout=_NAV_TIMEOUT,
                wait_until="domcontentloaded",
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass

            await self._wait_for_cf(page, max_wait=15)
            await self._simulate_human(page, duration=2.0)
        except Exception as exc:
            log.warning("ChatGPT homepage load error: %s", exc)
            return False

        log.info("ChatGPT homepage loaded: %s", page.url[:80])
        await self._log_page_state(page, "homepage")

        clicked = False
        for text in ["Sign up for free", "Sign up", "Get started", "Create account"]:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible(timeout=1500):
                    await self._simulate_human(page, duration=0.8)
                    await btn.click()
                    clicked = True
                    log.info("ChatGPT: clicked '%s'", text)
                    break
            except Exception:
                continue

        if not clicked:
            for sel in [
                'a[href*="signup"]', 'a[href*="sign-up"]',
                'button:has-text("Sign")', '[data-testid*="signup"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=500):
                        await el.click()
                        clicked = True
                        log.info("ChatGPT: clicked selector '%s'", sel[:40])
                        break
                except Exception:
                    continue

        if clicked:
            log.info("ChatGPT: after click, waiting for navigation/modal...")
            # انتظر 10 ثانية — قد ينتقل لـ auth.openai.com أو يبقي على chatgpt.com
            for wait_i in range(10):
                await asyncio.sleep(1)
                cur = page.url
                if "auth.openai.com" in cur or "auth0" in cur:
                    log.info("ChatGPT: redirected to auth: %s", cur[:100])
                    try:
                        await page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:
                        pass
                    await self._wait_for_sentinel(page, timeout=15.0)
                    break
                # فحص إذا ظهر نموذج email على chatgpt.com (modal)
                try:
                    inp = await page.query_selector(self._OPENAI_EMAIL_SEL)
                    if inp and await inp.is_visible():
                        log.info("ChatGPT: email form appeared on chatgpt.com (modal)")
                        return True
                except Exception:
                    pass
            else:
                log.info("ChatGPT: URL still %s after 10s", page.url[:80])

            await self._log_page_state(page, "after-click")

            if await self._wait_for_openai_form(page, timeout_ms=15000):
                return True

        # ===== المرحلة 3: authorize URL مع screen_hint=signup =====
        log.info("ChatGPT: trying authorize URL with screen_hint=signup")
        try:
            authorize_url = (
                "https://auth.openai.com/authorize"
                "?client_id=DRivsnm2Mu42T3KOpqdtwB3NYviHYzwD"
                "&redirect_uri=https%3A%2F%2Fchatgpt.com%2Fapi%2Fauth%2Fcallback%2Flogin-web"
                "&response_type=code"
                "&scope=openid+email+profile+offline_access"
                "&screen_hint=signup"
            )
            await page.goto(authorize_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            await self._log_page_state(page, "authorize-url")
            await self._wait_for_sentinel(page, timeout=20.0)

            if "platform.openai.com/login" not in page.url:
                if await self._wait_for_openai_form(page, timeout_ms=15000):
                    return True
        except Exception as exc:
            log.warning("ChatGPT authorize URL error: %s", exc)

        log.warning("ChatGPT: all approaches failed")
        return False

    async def _log_page_state(self, page, tag: str = "") -> None:
        """تسجيل حالة الصفحة الحالية للتشخيص — يشمل HTML وframes."""
        try:
            url = page.url
            body = ""
            try:
                body = (await page.inner_text("body"))[:400]
            except Exception:
                pass

            html_snippet = ""
            try:
                html_snippet = (await page.content())[:800]
            except Exception:
                pass

            inputs = await page.query_selector_all("input")
            visible_inputs = []
            for inp in inputs:
                try:
                    if await inp.is_visible():
                        t = await inp.get_attribute("type") or "text"
                        n = await inp.get_attribute("name") or ""
                        visible_inputs.append(f"{t}:{n}")
                except Exception:
                    pass

            frames_info = []
            for f in page.frames:
                f_url = f.url or ""
                if f_url and f_url != url and not f_url.startswith("about:"):
                    f_inputs = 0
                    try:
                        f_inputs = len(await f.query_selector_all("input"))
                    except Exception:
                        pass
                    frames_info.append(f"{f_url[:60]}(inputs={f_inputs})")

            log.info("[%s] URL=%s | inputs=%s | frames=%s | body=%s",
                     tag, url[:100], visible_inputs,
                     frames_info[:5] if frames_info else "none",
                     body[:200].replace("\n", " "))
            if not body.strip() and html_snippet:
                log.info("[%s] HTML=%s", tag, html_snippet[:500].replace("\n", " "))
        except Exception as exc:
            log.debug("[%s] log_page_state error: %s", tag, exc)

    async def _detect_arkose(self, page) -> bool:
        """كشف Arkose Labs (FunCaptcha) في الصفحة الحالية."""
        try:
            body = (await page.inner_text("body")).lower()
            if any(p in body for p in _ARKOSE_PHRASES):
                return True
        except Exception:
            pass
        # فحص الـ iframes
        for frame in page.frames:
            url = (frame.url or "").lower()
            if "arkoselabs" in url or "funcaptcha" in url or "arkose" in url:
                log.info("Arkose Labs iframe detected: %s", url[:80])
                return True
        return False

    async def _handle_arkose(self, page) -> bool:
        """
        محاولة حل أو الانتظار لـ Arkose.
        يُرجع True إذا اختفى التحدي، False إذا لم يُحل.
        """
        log.info("Arkose Labs detected — waiting up to 30s for auto-solve or timeout")
        self._report("⚠️ تحدي Arkose Labs — جاري الانتظار...")

        # انتظر حتى 30 ثانية لأن بعض التحديات تُحل تلقائياً
        for _ in range(10):
            await asyncio.sleep(3)
            if not await self._detect_arkose(page):
                log.info("Arkose resolved after wait")
                return True
            # تحقق إذا انتقلنا لصفحة أخرى (يعني تجاوز التحدي)
            url = page.url
            if any(kw in url for kw in ["password", "name", "profile", "chatgpt.com/?", "/c/"]):
                return True
        log.warning("Arkose challenge did not resolve")
        return False

    # ================================================================
    # Account Verification
    # ================================================================

    async def _post_registration_verify(self, page, site_url: str, email: str) -> bool:
        """
        يتحقق من أن الحساب أُنشئ فعلاً بعد انتهاء التسجيل.
        يستخدم الصفحة الحالية بعد التسجيل لفحص مؤشرات النجاح.
        يُرجع True إذا تأكّد وجود الحساب، False إذا لم يُتحقق.
        """
        try:
            parsed = urlparse(site_url)
            host = parsed.netloc.lower().lstrip("www.")
            current_url = page.url.lower()

            # --- تحقق مبني على URL بعد إعادة التوجيه ---
            for success_url in _SIGNUP_SUCCESS_URLS:
                if success_url.lower() in current_url:
                    log.info("_post_verify: URL match '%s' → confirmed", success_url)
                    return True

            # --- تحقق ChatGPT: وصلنا للداشبورد = حساب حقيقي ---
            if "chatgpt.com" in host or "openai.com" in host:
                # إذا تم التحويل لـ chatgpt.com وليس صفحة auth = نجاح
                if "chatgpt.com" in current_url and "auth" not in current_url and "authorize" not in current_url:
                    log.info("_post_verify: ChatGPT dashboard reached → confirmed")
                    return True
                # انتظر قليلاً وراقب URL
                for _ in range(3):
                    await asyncio.sleep(2)
                    url_now = page.url.lower()
                    if "chatgpt.com" in url_now and "auth" not in url_now:
                        log.info("_post_verify: ChatGPT redirect detected → confirmed")
                        return True
                    if any(kw in url_now for kw in ["/onboarding", "/welcome", "/?", "/c/"]):
                        return True
                return False

            # --- تحقق عبر نص الصفحة ---
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                body = ""

            # مؤشرات إيجابية في الصفحة
            positive = [
                "account created", "registration complete", "welcome",
                "check your email", "verify your email", "we sent you",
                "تم إنشاء", "تم التسجيل", "مرحباً", "تحقق من بريدك",
                "confirm your email", "verification link",
            ]
            if any(kw in body for kw in positive):
                return True

            # --- تحقق API: هل تلقينا response ناجح من الخادم؟ ---
            # (لا نملك api_responses هنا — نعتمد على URL)
            if current_url.rstrip("/") != site_url.rstrip("/").lower():
                # تم إعادة التوجيه = علامة إيجابية
                bad_paths = ["login", "signin", "auth/login", "authorize", "register", "signup"]
                is_still_on_auth = any(bad in current_url for bad in bad_paths)
                if not is_still_on_auth:
                    log.info("_post_verify: redirected away from auth → likely confirmed")
                    return True

        except Exception as exc:
            log.debug("_post_registration_verify error: %s", exc)

        return False
