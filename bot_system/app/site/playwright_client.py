"""
Browser-based fallback integration using Playwright.

Use this ONLY when HTTP API access is unavailable. Set:
    SITE_INTEGRATION_MODE=playwright

This stub shows the structure; fill in the selectors and URLs for your
own website. Install Playwright first:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

from typing import Optional

from app.core.config import config
from app.core.logger import get_logger
from app.site.base import (
    AccountResult,
    DuplicateAccountError,
    SiteIntegrationBase,
    SiteIntegrationError,
)

log = get_logger(__name__)

# ── Site-specific constants — fill these in ─────────────────────────────────
SITE_BASE_URL: str = config.SITE_API_BASE_URL  # e.g. "https://mysite.com"
REGISTER_PATH: str = "/register"               # path to the registration page
OTP_PATH: str = "/verify"                      # path to the OTP entry page

# CSS selectors — update to match your site's HTML
SEL_EMAIL_INPUT: str = 'input[name="email"]'
SEL_PASSWORD_INPUT: str = 'input[name="password"]'
SEL_SUBMIT_BUTTON: str = 'button[type="submit"]'
SEL_OTP_INPUT: str = 'input[name="otp"]'
SEL_OTP_SUBMIT: str = 'button[type="submit"]'
SEL_SUCCESS_INDICATOR: str = ".success-message"   # element visible on success
SEL_ERROR_INDICATOR: str = ".error-message"       # element visible on error
# ────────────────────────────────────────────────────────────────────────────


class PlaywrightClient(SiteIntegrationBase):
    """Browser-automation fallback. Prefer ApiClient when possible."""

    def __init__(self) -> None:
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------ #
    # Browser lifecycle
    # ------------------------------------------------------------------ #

    def _start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise SiteIntegrationError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            ) from exc

        pw = sync_playwright().start()
        self._browser = pw.chromium.launch(headless=True)
        self._page = self._browser.new_page()
        log.info("Playwright browser launched")

    def _stop(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
            self._page = None

    # ------------------------------------------------------------------ #
    # SiteIntegrationBase implementation
    # ------------------------------------------------------------------ #

    def create_account(self, email: str, password: str) -> AccountResult:
        """Navigate to the registration page and submit the form."""
        self._start()
        try:
            page = self._page
            url = f"{SITE_BASE_URL}{REGISTER_PATH}"
            log.info("Playwright: navigating to %s", url)
            page.goto(url)

            # ▼▼▼  Update selectors to match your site  ▼▼▼
            page.fill(SEL_EMAIL_INPUT, email)
            page.fill(SEL_PASSWORD_INPUT, password)
            page.click(SEL_SUBMIT_BUTTON)
            page.wait_for_load_state("networkidle")
            # ▲▲▲  Update selectors to match your site  ▲▲▲

            if page.locator(SEL_ERROR_INDICATOR).is_visible():
                error_text = page.locator(SEL_ERROR_INDICATOR).text_content() or "Unknown error"
                if "already" in error_text.lower() or "exists" in error_text.lower():
                    raise DuplicateAccountError(f"Account exists: {error_text}")
                raise SiteIntegrationError(f"Registration error: {error_text}")

            return AccountResult(success=True, message="Account created via browser")
        finally:
            self._stop()

    def request_otp(self, email: str) -> AccountResult:
        """Most sites trigger OTP automatically — implement only if needed."""
        return AccountResult(success=True, message="OTP triggered automatically")

    def submit_otp(self, email: str, otp: str) -> AccountResult:
        """Fill and submit the OTP form."""
        self._start()
        try:
            page = self._page
            url = f"{SITE_BASE_URL}{OTP_PATH}"
            log.info("Playwright: navigating to OTP page %s", url)
            page.goto(url)

            # ▼▼▼  Update selectors to match your site  ▼▼▼
            page.fill(SEL_OTP_INPUT, otp)
            page.click(SEL_OTP_SUBMIT)
            page.wait_for_load_state("networkidle")
            # ▲▲▲  Update selectors to match your site  ▲▲▲

            if page.locator(SEL_ERROR_INDICATOR).is_visible():
                error_text = page.locator(SEL_ERROR_INDICATOR).text_content() or "Unknown error"
                raise SiteIntegrationError(f"OTP submission error: {error_text}")

            return AccountResult(success=True, message="OTP verified via browser")
        finally:
            self._stop()

    def finalize_account(self, email: str) -> AccountResult:
        return AccountResult(success=True, message="No post-verification step needed")

    def get_account_status(self, email: str) -> AccountResult:
        return AccountResult(success=False, message="Status check not implemented in browser mode")
