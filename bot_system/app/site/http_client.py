"""
HTTP-based site registration client.

Uses requests + BeautifulSoup to:
1. Visit the registration page
2. Parse the HTML form (CSRF tokens, fields, action URL)
3. Fill and submit via POST
4. Report success/failure

Much faster than Playwright — no browser needed.
"""

from __future__ import annotations

import random
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.core.logger import get_logger
from app.core.utils import fake_first_name, fake_last_name, fake_username

log = get_logger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
]

_SIGNUP_PATHS = [
    "/signup", "/register", "/join", "/sign-up", "/account/register",
    "/accounts/register", "/user/register", "/en/register", "/en/signup",
    "/auth/register", "/auth/signup",
]

_SIGNUP_LINK_PATTERNS = [
    r"sign\s*up", r"register", r"create.*account", r"join", r"إنشاء.*حساب",
    r"تسجيل", r"get\s*started",
]


class RegistrationResult:
    def __init__(self, success: bool, needs_otp: bool = False,
                 message: str = "", page_url: str = "",
                 status_code: int = 0) -> None:
        self.success = success
        self.needs_otp = needs_otp
        self.message = message
        self.page_url = page_url
        self.status_code = status_code


class HttpSiteClient:
    """Fast HTTP-based registration — no browser needed."""

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout

    def register(self, site_url: str, email: str, password: str) -> RegistrationResult:
        first = fake_first_name()
        last = fake_last_name()
        username = fake_username(email)

        session = requests.Session()
        session.headers.update({
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        })

        try:
            # Step 1: Find registration page
            log.info("→ Opening %s", site_url)
            reg_url, reg_page = self._find_registration_page(session, site_url)
            if not reg_url:
                return RegistrationResult(False, message="لم أجد صفحة تسجيل على هذا الموقع")

            log.info("→ Found registration page: %s", reg_url)

            # Step 2: Parse form
            form_data, action_url, method = self._parse_form(reg_page, reg_url, email, password, first, last, username)
            if not form_data:
                return RegistrationResult(False, message="لم أجد نموذج تسجيل في الصفحة")

            log.info("→ Form parsed: %d fields, action=%s", len(form_data), action_url)

            # Step 3: Submit form
            if method.upper() == "GET":
                resp = session.get(action_url, params=form_data, timeout=self._timeout, allow_redirects=True)
            else:
                resp = session.post(action_url, data=form_data, timeout=self._timeout, allow_redirects=True)

            log.info("→ Form submitted: status=%d url=%s", resp.status_code, resp.url)

            # Step 4: Analyze response
            return self._analyze_response(resp, reg_url)

        except requests.Timeout:
            return RegistrationResult(False, message="انتهى وقت الاتصال بالموقع")
        except requests.ConnectionError as exc:
            return RegistrationResult(False, message=f"لا يمكن الاتصال بالموقع: {exc}")
        except Exception as exc:
            log.error("HTTP registration error: %s", exc)
            return RegistrationResult(False, message=f"خطأ: {exc}")

    def _find_registration_page(self, session: requests.Session, site_url: str) -> Tuple[Optional[str], Optional[BeautifulSoup]]:
        """Find the registration page by trying common paths and link patterns."""
        # Try the homepage first
        try:
            resp = session.get(site_url, timeout=self._timeout, allow_redirects=True)
            soup = BeautifulSoup(resp.text, "lxml")

            # Look for signup links in the page
            for pattern in _SIGNUP_LINK_PATTERNS:
                for link in soup.find_all("a", href=True):
                    link_text = link.get_text(strip=True).lower()
                    if re.search(pattern, link_text, re.IGNORECASE):
                        href = urljoin(site_url, link["href"])
                        log.info("→ Found signup link: %s → %s", link_text, href)
                        try:
                            r2 = session.get(href, timeout=self._timeout, allow_redirects=True)
                            s2 = BeautifulSoup(r2.text, "lxml")
                            if self._has_registration_form(s2):
                                return r2.url, s2
                        except Exception:
                            continue

            # Check if current page has a form
            if self._has_registration_form(soup):
                return resp.url, soup

        except Exception as exc:
            log.warning("Could not load homepage: %s", exc)

        # Try common signup paths
        for path in _SIGNUP_PATHS:
            try:
                url = urljoin(site_url, path)
                resp = session.get(url, timeout=self._timeout, allow_redirects=True)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    if self._has_registration_form(soup):
                        return resp.url, soup
            except Exception:
                continue

        return None, None

    def _has_registration_form(self, soup: BeautifulSoup) -> bool:
        """Check if page has email + password fields."""
        has_email = bool(
            soup.find("input", {"type": "email"}) or
            soup.find("input", {"name": re.compile(r"email", re.I)}) or
            soup.find("input", {"placeholder": re.compile(r"email", re.I)})
        )
        has_password = bool(
            soup.find("input", {"type": "password"})
        )
        return has_email and has_password

    def _parse_form(self, soup: BeautifulSoup, page_url: str,
                    email: str, password: str,
                    first: str, last: str, username: str) -> Tuple[Optional[Dict], str, str]:
        """Parse the registration form and fill in fields."""
        # Find the form with email/password fields
        forms = soup.find_all("form")
        target_form = None

        for form in forms:
            if form.find("input", {"type": "email"}) or form.find("input", {"name": re.compile(r"email", re.I)}):
                if form.find("input", {"type": "password"}):
                    target_form = form
                    break

        if not target_form and forms:
            for form in forms:
                if form.find("input", {"type": "password"}):
                    target_form = form
                    break

        if not target_form:
            return None, "", ""

        # Get action URL and method
        action = target_form.get("action", "")
        action_url = urljoin(page_url, action) if action else page_url
        method = target_form.get("method", "POST")

        # Collect all form fields
        form_data: Dict[str, str] = {}

        for inp in target_form.find_all(["input", "select", "textarea"]):
            name = inp.get("name")
            if not name:
                continue
            input_type = inp.get("type", "text").lower()
            value = inp.get("value", "")

            # Skip submit buttons
            if input_type in ("submit", "button", "image", "reset"):
                continue

            # Hidden fields (CSRF tokens, etc.) — keep their values
            if input_type == "hidden":
                form_data[name] = value
                continue

            # Checkbox — check it by default
            if input_type == "checkbox":
                form_data[name] = value or "on"
                continue

            # Smart field detection
            name_lower = name.lower()

            if input_type == "email" or "email" in name_lower:
                form_data[name] = email
            elif input_type == "password" or "password" in name_lower or "pass" in name_lower:
                form_data[name] = password
            elif any(k in name_lower for k in ["first_name", "firstname", "fname"]):
                form_data[name] = first
            elif any(k in name_lower for k in ["last_name", "lastname", "lname"]):
                form_data[name] = last
            elif any(k in name_lower for k in ["full_name", "fullname", "name"]):
                form_data[name] = f"{first} {last}"
            elif any(k in name_lower for k in ["username", "user_name", "login"]):
                form_data[name] = username
            elif any(k in name_lower for k in ["phone", "tel", "mobile"]):
                form_data[name] = f"+1{random.randint(2000000000, 9999999999)}"
            elif any(k in name_lower for k in ["birth", "dob", "age"]):
                form_data[name] = "1995-06-15"
            else:
                form_data[name] = value

        return form_data, action_url, method

    def _analyze_response(self, resp: requests.Response, original_url: str) -> RegistrationResult:
        """Analyze the response after form submission."""
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(separator=" ", strip=True).lower()

        # Check for OTP/verification indicators
        otp_indicators = [
            "verification code", "verify your email", "check your email",
            "enter the code", "confirm your email", "we sent you",
            "تحقق من بريدك", "رمز التحقق", "تأكيد البريد", "تم إرسال",
        ]
        if any(ind in text for ind in otp_indicators):
            return RegistrationResult(
                True, needs_otp=True,
                message="تم التسجيل — بانتظار رمز التحقق",
                page_url=resp.url, status_code=resp.status_code,
            )

        # Check for error messages
        error_indicators = [
            "already exists", "already registered", "email taken",
            "حساب موجود", "مسجل مسبقاً",
        ]
        if any(ind in text for ind in error_indicators):
            return RegistrationResult(
                False, message="الحساب موجود مسبقاً بهذا الإيميل",
                page_url=resp.url, status_code=resp.status_code,
            )

        # Check for success indicators
        success_indicators = [
            "welcome", "success", "thank you", "account created",
            "congratulations", "registration complete",
            "مرحباً", "تم بنجاح", "شكراً", "تم إنشاء",
        ]
        if any(ind in text for ind in success_indicators):
            return RegistrationResult(
                True, message="✅ تم إنشاء الحساب بنجاح",
                page_url=resp.url, status_code=resp.status_code,
            )

        # URL changed = likely success redirect
        if resp.url.rstrip("/") != original_url.rstrip("/"):
            return RegistrationResult(
                True, message="تم إرسال النموذج (تم التحويل لصفحة أخرى)",
                page_url=resp.url, status_code=resp.status_code,
            )

        # 2xx = probably OK
        if 200 <= resp.status_code < 300:
            return RegistrationResult(
                True, message="تم إرسال النموذج",
                page_url=resp.url, status_code=resp.status_code,
            )

        return RegistrationResult(
            False, message=f"الموقع أرجع كود: {resp.status_code}",
            page_url=resp.url, status_code=resp.status_code,
        )
