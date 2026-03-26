"""
HTTP API client for your website.

THIS IS WHERE YOU PLUG IN YOUR OWN ENDPOINTS.

Every method contains a clearly marked placeholder block showing exactly
which URL, HTTP method, and request/response fields you need to fill in.
The HTTP transport layer (retries, auth headers, timeouts) is already
wired up — you only need to adjust endpoints and payloads.

Authentication:
    SITE_API_KEY env var → sent as  Authorization: Bearer <key>
    If your site uses a different auth scheme, update _auth_headers().
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import config
from app.core.logger import get_logger
from app.site.base import (
    AccountResult,
    DuplicateAccountError,
    SiteIntegrationBase,
    SiteIntegrationError,
)

log = get_logger(__name__)


class ApiClient(SiteIntegrationBase):
    """HTTP API integration with the target website."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self._base_url = (base_url or config.SITE_API_BASE_URL).rstrip("/")
        self._api_key = api_key or config.SITE_API_KEY
        self._session = self._build_session()

    # ------------------------------------------------------------------ #
    # Transport helpers
    # ------------------------------------------------------------------ #

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=config.HTTP_MAX_RETRIES,
            backoff_factor=config.HTTP_RETRY_BACKOFF,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET", "PUT"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _post(self, path: str, payload: Dict[str, Any]) -> requests.Response:
        url = f"{self._base_url}{path}"
        log.debug("POST %s payload_keys=%s", url, list(payload.keys()))
        resp = self._session.post(
            url,
            json=payload,
            headers=self._auth_headers(),
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        return resp

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        url = f"{self._base_url}{path}"
        log.debug("GET %s params=%s", url, params)
        resp = self._session.get(
            url,
            params=params,
            headers=self._auth_headers(),
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        return resp

    # ------------------------------------------------------------------ #
    # SiteIntegrationBase implementation
    # ------------------------------------------------------------------ #

    def create_account(self, email: str, password: str) -> AccountResult:
        """
        Register a new account.

        ┌─────────────────────────────────────────────────────────────────┐
        │  PLUG IN YOUR ENDPOINT HERE                                      │
        │                                                                  │
        │  Endpoint : POST /register   (adjust path below)                 │
        │  Request  : {"email": email, "password": password, ...}          │
        │  Success  : HTTP 200/201, body {"success": true, ...}            │
        │  Duplicate: HTTP 409 or body {"error": "already_exists", ...}    │
        └─────────────────────────────────────────────────────────────────┘
        """
        # ▼▼▼  REPLACE THIS SECTION  ▼▼▼
        endpoint = "/register"           # ← your registration endpoint
        payload: Dict[str, Any] = {
            "email": email,
            "password": password,
            # Add any other fields your site requires, e.g.:
            # "username": email.split("@")[0],
            # "terms_accepted": True,
        }
        # ▲▲▲  REPLACE THIS SECTION  ▲▲▲

        try:
            resp = self._post(endpoint, payload)
        except requests.RequestException as exc:
            raise SiteIntegrationError(f"Network error during create_account: {exc}") from exc

        log.debug("create_account response: status=%d body=%s", resp.status_code, resp.text[:200])

        if resp.status_code in (409,):
            raise DuplicateAccountError(
                f"Account already exists for {email}", status_code=resp.status_code
            )

        if not resp.ok:
            body = self._safe_json(resp)
            # ▼▼▼  Adjust the error field name your site uses  ▼▼▼
            reason = body.get("error") or body.get("message") or resp.text[:200]
            raise SiteIntegrationError(
                f"create_account failed ({resp.status_code}): {reason}",
                status_code=resp.status_code,
            )

        body = self._safe_json(resp)
        return AccountResult(success=True, message="Account created", extra=body)

    def request_otp(self, email: str) -> AccountResult:
        """
        Ask the site to (re-)send an OTP to *email*.

        ┌─────────────────────────────────────────────────────────────────┐
        │  PLUG IN YOUR ENDPOINT HERE                                      │
        │                                                                  │
        │  Endpoint : POST /resend-otp  (adjust or return early if unused) │
        │  Request  : {"email": email}                                     │
        │  Success  : HTTP 200/202                                         │
        └─────────────────────────────────────────────────────────────────┘
        """
        # ▼▼▼  If your site sends OTP automatically after registration, just return success ▼▼▼
        # return AccountResult(success=True, message="OTP sent automatically")

        endpoint = "/resend-otp"   # ← adjust or comment out
        payload: Dict[str, Any] = {"email": email}

        try:
            resp = self._post(endpoint, payload)
        except requests.RequestException as exc:
            raise SiteIntegrationError(f"Network error during request_otp: {exc}") from exc

        if not resp.ok:
            body = self._safe_json(resp)
            reason = body.get("error") or body.get("message") or resp.text[:200]
            raise SiteIntegrationError(
                f"request_otp failed ({resp.status_code}): {reason}",
                status_code=resp.status_code,
            )
        return AccountResult(success=True, message="OTP resent")

    def submit_otp(self, email: str, otp: str) -> AccountResult:
        """
        Submit the OTP code for *email*.

        ┌─────────────────────────────────────────────────────────────────┐
        │  PLUG IN YOUR ENDPOINT HERE                                      │
        │                                                                  │
        │  Endpoint : POST /verify-otp  (adjust path below)               │
        │  Request  : {"email": email, "otp": otp}                        │
        │  Success  : HTTP 200, body {"verified": true, ...}              │
        │  Bad OTP  : HTTP 400/422                                         │
        └─────────────────────────────────────────────────────────────────┘
        """
        # ▼▼▼  REPLACE THIS SECTION  ▼▼▼
        endpoint = "/verify-otp"   # ← your OTP verification endpoint
        payload: Dict[str, Any] = {
            "email": email,
            "otp": otp,
            # Some sites use different field names:
            # "code": otp,
            # "token": otp,
        }
        # ▲▲▲  REPLACE THIS SECTION  ▲▲▲

        try:
            resp = self._post(endpoint, payload)
        except requests.RequestException as exc:
            raise SiteIntegrationError(f"Network error during submit_otp: {exc}") from exc

        log.debug("submit_otp response: status=%d body=%s", resp.status_code, resp.text[:200])

        if not resp.ok:
            body = self._safe_json(resp)
            reason = body.get("error") or body.get("message") or resp.text[:200]
            raise SiteIntegrationError(
                f"submit_otp failed ({resp.status_code}): {reason}",
                status_code=resp.status_code,
            )

        body = self._safe_json(resp)
        return AccountResult(success=True, message="OTP verified", extra=body)

    def finalize_account(self, email: str) -> AccountResult:
        """
        Post-verification step.

        ┌─────────────────────────────────────────────────────────────────┐
        │  PLUG IN YOUR ENDPOINT HERE (or return success immediately if   │
        │  no post-verification step is needed on your site)              │
        │                                                                  │
        │  Endpoint : POST /finalize  (optional)                          │
        └─────────────────────────────────────────────────────────────────┘
        """
        # ▼▼▼  If not needed, just return success  ▼▼▼
        return AccountResult(success=True, message="Account finalised (no-op)")

        # Example if your site requires a POST to /finalize:
        # try:
        #     resp = self._post("/finalize", {"email": email})
        # except requests.RequestException as exc:
        #     raise SiteIntegrationError(f"finalize_account: {exc}") from exc
        # if not resp.ok:
        #     raise SiteIntegrationError(f"finalize_account failed: {resp.text[:200]}")
        # return AccountResult(success=True, message="Account finalised")

    def get_account_status(self, email: str) -> AccountResult:
        """
        Check whether *email* is already registered / verified.

        ┌─────────────────────────────────────────────────────────────────┐
        │  PLUG IN YOUR ENDPOINT HERE                                      │
        │                                                                  │
        │  Endpoint : GET /account/status?email=...                       │
        │  Success  : HTTP 200, body {"exists": bool, "verified": bool}   │
        │  Not found: HTTP 404                                             │
        └─────────────────────────────────────────────────────────────────┘
        """
        # ▼▼▼  REPLACE THIS SECTION  ▼▼▼
        endpoint = "/account/status"   # ← your status check endpoint
        params = {"email": email}
        # ▲▲▲  REPLACE THIS SECTION  ▲▲▲

        try:
            resp = self._get(endpoint, params)
        except requests.RequestException as exc:
            raise SiteIntegrationError(f"Network error during get_account_status: {exc}") from exc

        if resp.status_code == 404:
            return AccountResult(success=False, message="Account not found")

        if not resp.ok:
            raise SiteIntegrationError(
                f"get_account_status failed ({resp.status_code}): {resp.text[:200]}",
                status_code=resp.status_code,
            )

        body = self._safe_json(resp)
        return AccountResult(
            success=True,
            message=f"exists={body.get('exists')}, verified={body.get('verified')}",
            extra=body,
        )

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_json(resp: requests.Response) -> Dict[str, Any]:
        try:
            return resp.json()
        except Exception:
            return {}
