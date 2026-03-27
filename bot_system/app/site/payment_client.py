from __future__ import annotations

import asyncio
import os
import random
import shutil
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from app.core.logger import get_logger

log = get_logger(__name__)

PAYMENT_TIMEOUT = 300

_NAV_TIMEOUT = 20_000

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

_UPGRADE_URLS = {
    "chatgpt.com": "https://chatgpt.com/#pricing",
    "canva.com": "https://www.canva.com/pricing/",
    "protonvpn.com": "https://protonvpn.com/pricing",
    "pixlr.com": "https://pixlr.com/pricing/",
    "replit.com": "https://replit.com/pricing",
}

_LOGIN_URLS = {
    "chatgpt.com": "https://chatgpt.com/auth/login",
    "canva.com": "https://www.canva.com/login",
    "protonvpn.com": "https://account.proton.me/login",
    "pixlr.com": "https://pixlr.com/myaccount/",
    "replit.com": "https://replit.com/login",
}

_LOGIN_BUTTON_TEXTS = [
    "log in", "sign in", "login", "signin",
    "تسجيل الدخول", "دخول",
]

_UPGRADE_BUTTON_TEXTS = [
    "upgrade", "subscribe", "get plus", "get pro", "get premium",
    "buy", "purchase", "go pro", "try pro", "start trial",
    "upgrade plan", "upgrade to", "الترقية", "اشتراك",
    "get started", "choose plan", "select plan",
    "get canva pro", "start free trial",
]

_PLAN_BUTTON_TEXTS = {
    "plus": ["plus", "get plus", "upgrade to plus"],
    "pro": ["pro", "get pro", "go pro", "try pro", "get canva pro"],
    "premium": ["premium", "get premium"],
    "basic": ["basic", "starter"],
}


@dataclass
class PaymentResult:
    success: bool
    message: str = ""
    page_url: str = ""


class PaymentClient:
    def __init__(self, timeout: int = 8_000):
        self._timeout = timeout
        self._progress_callback: Optional[Callable] = None

    def _report(self, msg: str):
        if self._progress_callback:
            self._progress_callback(msg)

    async def pay(
        self,
        site_url: str,
        email: str,
        password: str,
        card_number: str,
        card_expiry_month: str,
        card_expiry_year: str,
        card_cvv: str,
        card_holder: str,
        plan_name: str = "",
        progress_callback: Optional[Callable] = None,
    ) -> PaymentResult:
        self._progress_callback = progress_callback
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return PaymentResult(False, message="Playwright غير مثبت")

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
                ],
            }
            if chromium_path:
                launch_args["executable_path"] = chromium_path

            browser = await pw_instance.chromium.launch(**launch_args)
            context = await browser.new_context(
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
            await context.add_init_script(_STEALTH_JS)
            page = await context.new_page()

            result = await asyncio.wait_for(
                self._do_payment(
                    page, site_url, email, password,
                    card_number, card_expiry_month, card_expiry_year,
                    card_cvv, card_holder, plan_name,
                ),
                timeout=PAYMENT_TIMEOUT,
            )
            return result

        except asyncio.TimeoutError:
            return PaymentResult(False, message=f"انتهى الوقت ({PAYMENT_TIMEOUT}ث)")
        except Exception as exc:
            log.error("Payment error: %s", exc)
            return PaymentResult(False, message=str(exc)[:200])
        finally:
            try:
                if browser:
                    await asyncio.wait_for(browser.close(), timeout=5)
            except Exception:
                pass
            try:
                if pw_instance:
                    await asyncio.wait_for(pw_instance.stop(), timeout=5)
            except Exception:
                pass

    async def _do_payment(
        self, page, site_url, email, password,
        card_number, card_expiry_month, card_expiry_year,
        card_cvv, card_holder, plan_name,
    ) -> PaymentResult:
        domain = urlparse(site_url).netloc.replace("www.", "")

        self._report("فتح الموقع...")
        login_result = await self._login(page, site_url, domain, email, password)
        if not login_result:
            return PaymentResult(False, message="فشل تسجيل الدخول")

        self._report("البحث عن صفحة الاشتراك...")
        upgrade_found = await self._navigate_to_upgrade(page, domain, plan_name)
        if not upgrade_found:
            return PaymentResult(False, message="لم أجد صفحة الاشتراك/الترقية")

        self._report("البحث عن نموذج الدفع...")
        payment_form = await self._find_payment_form(page)
        if not payment_form:
            return PaymentResult(False, message="لم أجد نموذج الدفع")

        self._report("تعبئة بيانات البطاقة...")
        filled = await self._fill_card(
            page, card_number, card_expiry_month, card_expiry_year,
            card_cvv, card_holder,
        )
        if not filled:
            return PaymentResult(False, message="فشل تعبئة بيانات البطاقة")

        self._report("تأكيد الدفع...")
        confirmed = await self._confirm_payment(page)
        if not confirmed:
            return PaymentResult(False, message="فشل تأكيد الدفع")

        self._report("التحقق من نتيجة الدفع...")
        await asyncio.sleep(3)
        success = await self._check_payment_result(page)

        if success:
            return PaymentResult(True, message="تم الدفع والاشتراك بنجاح", page_url=page.url)
        else:
            body = ""
            try:
                body = (await page.inner_text("body"))[:500].lower()
            except Exception:
                pass
            error_hints = ["declined", "insufficient", "invalid", "expired", "مرفوض", "غير صالح", "failed"]
            for hint in error_hints:
                if hint in body:
                    return PaymentResult(False, message=f"البطاقة مرفوضة أو غير صالحة: {hint}")
            return PaymentResult(False, message="لم أتأكد من نجاح الدفع -- تحقق يدويا")

    async def _login(self, page, site_url, domain, email, password) -> bool:
        login_url = _LOGIN_URLS.get(domain, site_url)
        self._report("تسجيل الدخول...")
        try:
            await page.goto(login_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            await self._wait_spa(page)
        except Exception as exc:
            log.error("Login navigation failed: %s", exc)
            return False

        await self._wait_for_cf(page)

        for _ in range(3):
            email_input = await self._find_input(page, ["email", "username", "login", "identifier"])
            if email_input:
                break
            for text in _LOGIN_BUTTON_TEXTS:
                try:
                    btn = page.get_by_text(text, exact=False).first
                    if await btn.is_visible(timeout=500):
                        await btn.click()
                        await self._wait_spa(page)
                        break
                except Exception:
                    continue
            await asyncio.sleep(1)

        email_input = await self._find_input(page, ["email", "username", "login", "identifier"])
        if not email_input:
            log.warning("No email input found on login page")
            return False

        await self._fill_input(email_input, email)

        password_input = await self._find_input(page, ["password", "passwd"])
        if password_input:
            await self._fill_input(password_input, password)

        submitted = await self._click_submit(page, ["log in", "sign in", "login", "continue", "next", "التالي", "دخول", "تسجيل الدخول"])
        if not submitted:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass

        await asyncio.sleep(2)
        await self._wait_spa(page)

        if not password_input:
            password_input = await self._find_input(page, ["password", "passwd"])
            if password_input:
                await self._fill_input(password_input, password)
                await self._click_submit(page, ["log in", "sign in", "login", "continue", "next", "التالي", "دخول"])
                await asyncio.sleep(2)
                await self._wait_spa(page)

        body = ""
        try:
            body = (await page.inner_text("body"))[:1000].lower()
        except Exception:
            pass

        login_fail = ["incorrect", "invalid", "wrong password", "try again", "خطأ", "غير صحيح"]
        for kw in login_fail:
            if kw in body:
                log.warning("Login appears to have failed: %s", kw)
                return False

        log.info("Login completed, URL: %s", page.url[:120])
        return True

    async def _navigate_to_upgrade(self, page, domain, plan_name) -> bool:
        upgrade_url = _UPGRADE_URLS.get(domain)
        if upgrade_url:
            try:
                await page.goto(upgrade_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
                await self._wait_spa(page)
                log.info("Navigated to upgrade URL: %s", upgrade_url)
            except Exception:
                log.warning("Direct upgrade URL failed, trying buttons")

        for text in _UPGRADE_BUTTON_TEXTS:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await asyncio.sleep(1.5)
                    await self._wait_spa(page)
                    log.info("Clicked upgrade button: %s", text)
                    break
            except Exception:
                continue

        if plan_name:
            plan_texts = _PLAN_BUTTON_TEXTS.get(plan_name.lower(), [plan_name])
            for text in plan_texts:
                try:
                    btn = page.get_by_text(text, exact=False).first
                    if await btn.is_visible(timeout=500):
                        await btn.click()
                        await asyncio.sleep(1.5)
                        await self._wait_spa(page)
                        log.info("Selected plan: %s", text)
                        break
                except Exception:
                    continue

        return True

    async def _find_payment_form(self, page) -> bool:
        for _ in range(5):
            stripe_frame = await self._find_stripe_iframe(page)
            if stripe_frame:
                return True

            card_input = await self._find_input(page, [
                "card", "cardnumber", "cc-number", "card-number",
                "cardNumber", "number",
            ])
            if card_input:
                return True

            for text in _UPGRADE_BUTTON_TEXTS:
                try:
                    btn = page.get_by_text(text, exact=False).first
                    if await btn.is_visible(timeout=300):
                        await btn.click()
                        await asyncio.sleep(1.5)
                        await self._wait_spa(page)
                        break
                except Exception:
                    continue

            await asyncio.sleep(1)

        return False

    async def _fill_card(
        self, page,
        card_number, expiry_month, expiry_year,
        cvv, holder_name,
    ) -> bool:
        expiry = f"{expiry_month}/{expiry_year}"

        stripe_frame = await self._find_stripe_iframe(page)
        if stripe_frame:
            return await self._fill_stripe(stripe_frame, page, card_number, expiry, cvv, holder_name)

        return await self._fill_direct_card(page, card_number, expiry_month, expiry_year, cvv, holder_name)

    async def _find_stripe_iframe(self, page):
        try:
            for frame in page.frames:
                url = frame.url or ""
                if "js.stripe.com" in url or "stripe" in frame.name.lower():
                    return frame

            iframes = await page.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src") or ""
                name = await iframe.get_attribute("name") or ""
                title = await iframe.get_attribute("title") or ""
                if any(k in (src + name + title).lower() for k in ["stripe", "card", "payment"]):
                    frame = await iframe.content_frame()
                    if frame:
                        return frame
        except Exception as exc:
            log.debug("Stripe iframe search: %s", exc)
        return None

    async def _fill_stripe(self, card_frame, page, card_number, expiry, cvv, holder_name) -> bool:
        try:
            card_input = card_frame.locator('input[name="cardnumber"], input[autocomplete="cc-number"], input[data-elements-stable-field-name="cardNumber"]').first
            if await card_input.is_visible(timeout=3000):
                await card_input.click()
                await card_input.fill(card_number)
                await asyncio.sleep(0.3)
        except Exception as exc:
            log.warning("Stripe card number fill failed: %s", exc)
            return False

        try:
            exp_input = card_frame.locator('input[name="exp-date"], input[autocomplete="cc-exp"], input[data-elements-stable-field-name="cardExpiry"]').first
            if await exp_input.is_visible(timeout=2000):
                await exp_input.click()
                await exp_input.fill(expiry)
                await asyncio.sleep(0.3)
        except Exception:
            pass

        try:
            cvv_input = card_frame.locator('input[name="cvc"], input[autocomplete="cc-csc"], input[data-elements-stable-field-name="cardCvc"]').first
            if await cvv_input.is_visible(timeout=2000):
                await cvv_input.click()
                await cvv_input.fill(cvv)
                await asyncio.sleep(0.3)
        except Exception:
            pass

        try:
            zip_input = card_frame.locator('input[name="postal"], input[autocomplete="postal-code"]').first
            if await zip_input.is_visible(timeout=1000):
                await zip_input.fill("10001")
        except Exception:
            pass

        name_input = await self._find_input(page, ["cardholder", "card-holder", "name", "billing"])
        if name_input:
            await self._fill_input(name_input, holder_name)

        zip_on_page = await self._find_input(page, ["postal", "zip", "zipcode"])
        if zip_on_page:
            await self._fill_input(zip_on_page, "10001")

        country_select = page.locator('select[name*="country"], select[id*="country"]').first
        try:
            if await country_select.is_visible(timeout=1000):
                await country_select.select_option(value="US")
        except Exception:
            pass

        return True

    async def _fill_direct_card(self, page, card_number, exp_month, exp_year, cvv, holder_name) -> bool:
        card_input = await self._find_input(page, [
            "card", "cardnumber", "cc-number", "card-number", "cardNumber", "number",
        ])
        if not card_input:
            return False

        await self._fill_input(card_input, card_number)

        exp_input = await self._find_input(page, ["expir", "exp-date", "cc-exp", "expiry", "mm"])
        if exp_input:
            await self._fill_input(exp_input, f"{exp_month}/{exp_year}")
        else:
            month_input = await self._find_input(page, ["month", "exp-month", "cc-exp-month"])
            year_input = await self._find_input(page, ["year", "exp-year", "cc-exp-year"])
            if month_input:
                await self._fill_input(month_input, exp_month)
            if year_input:
                await self._fill_input(year_input, exp_year)

        cvv_input = await self._find_input(page, ["cvv", "cvc", "security", "cc-csc", "securityCode"])
        if cvv_input:
            await self._fill_input(cvv_input, cvv)

        name_input = await self._find_input(page, ["cardholder", "card-holder", "holder", "name", "billing-name"])
        if name_input:
            await self._fill_input(name_input, holder_name)

        zip_input = await self._find_input(page, ["postal", "zip", "zipcode"])
        if zip_input:
            await self._fill_input(zip_input, "10001")

        return True

    async def _confirm_payment(self, page) -> bool:
        confirm_texts = [
            "subscribe", "pay", "confirm", "complete purchase",
            "place order", "submit payment", "upgrade",
            "start subscription", "ادفع", "تأكيد", "اشترك",
            "confirm payment", "pay now", "buy now",
        ]

        for text in confirm_texts:
            try:
                btn = page.locator(f'button:has-text("{text}")').first
                if await btn.is_visible(timeout=500):
                    btn_text = (await btn.inner_text()).strip().lower()
                    skip = ["cancel", "back", "إلغاء", "رجوع"]
                    if any(s in btn_text for s in skip):
                        continue
                    await btn.click()
                    log.info("Clicked payment confirm: %s", text)
                    return True
            except Exception:
                continue

        for sel in ['button[type="submit"]', 'input[type="submit"]']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    return True
            except Exception:
                continue

        return False

    async def _check_payment_result(self, page) -> bool:
        await self._wait_spa(page)
        await asyncio.sleep(2)

        body = ""
        try:
            body = (await page.inner_text("body"))[:2000].lower()
        except Exception:
            pass

        success_kw = [
            "thank you", "success", "welcome", "subscribed",
            "payment complete", "order confirmed", "activated",
            "شكرا", "نجح", "تم الاشتراك", "مفعل",
            "you're all set", "enjoy", "receipt",
        ]
        fail_kw = [
            "declined", "failed", "error", "invalid card",
            "insufficient", "expired", "مرفوض", "فشل",
        ]

        for kw in fail_kw:
            if kw in body:
                return False

        for kw in success_kw:
            if kw in body:
                return True

        return False

    async def _find_input(self, page, keywords) -> Optional[object]:
        try:
            inputs = await page.query_selector_all("input:visible")
            for inp in inputs:
                name = (await inp.get_attribute("name") or "").lower()
                type_ = (await inp.get_attribute("type") or "").lower()
                id_ = (await inp.get_attribute("id") or "").lower()
                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                autocomplete = (await inp.get_attribute("autocomplete") or "").lower()
                aria = (await inp.get_attribute("aria-label") or "").lower()

                combined = f"{name} {type_} {id_} {placeholder} {autocomplete} {aria}"

                if type_ in ("hidden", "checkbox", "radio", "file"):
                    continue

                for kw in keywords:
                    if kw in combined:
                        return inp
        except Exception:
            pass
        return None

    async def _fill_input(self, inp, value: str):
        try:
            await inp.click()
            await asyncio.sleep(0.05)
        except Exception:
            pass
        try:
            await inp.fill(value)
        except Exception:
            try:
                await inp.click(force=True)
                await asyncio.sleep(0.1)
                await inp.type(value, delay=30)
            except Exception:
                pass
        await asyncio.sleep(0.05)

    async def _click_submit(self, page, texts) -> bool:
        for text in texts:
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
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    async def _wait_spa(self, page):
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        await asyncio.sleep(0.3)

    async def _wait_for_cf(self, page, max_wait=15):
        cf_phrases = ["checking your browser", "verify you are human", "just a moment"]
        elapsed = 0
        while elapsed < max_wait:
            try:
                body = (await page.inner_text("body"))[:500].lower()
                if not any(p in body for p in cf_phrases):
                    return
            except Exception:
                return
            await asyncio.sleep(1)
            elapsed += 1
