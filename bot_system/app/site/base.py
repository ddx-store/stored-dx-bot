"""
Abstract base class for website integration providers.

Every method that talks to your website must be implemented here.
Add a new concrete class in api_client.py (HTTP) or playwright_client.py
(browser) and switch between them via SITE_INTEGRATION_MODE.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class AccountResult:
    """Outcome of a single site API call."""
    success: bool
    message: str
    extra: Optional[dict] = None


class SiteIntegrationBase(ABC):
    """Provider contract — implement all methods for your site."""

    @abstractmethod
    def create_account(self, email: str, password: str) -> AccountResult:
        """
        Register a new account on the target website.

        Expected behaviour:
        - On success, return AccountResult(success=True, ...).
        - If OTP/email verification is required after this step,
          the caller (registration_service) will handle that separately.
        - On duplicate account, raise DuplicateAccountError.
        - On any other failure, raise SiteIntegrationError with a reason.
        """
        ...

    @abstractmethod
    def request_otp(self, email: str) -> AccountResult:
        """
        Trigger the site to (re-)send an OTP email to *email*.
        Some sites do this automatically after create_account; others need
        an explicit call.  Implement as a no-op if not applicable.
        """
        ...

    @abstractmethod
    def submit_otp(self, email: str, otp: str) -> AccountResult:
        """Submit an OTP code (or click an activation link) to the site."""
        ...

    @abstractmethod
    def finalize_account(self, email: str) -> AccountResult:
        """
        Any post-verification step needed (e.g. profile setup, login check).
        Implement as a no-op if not applicable.
        """
        ...

    @abstractmethod
    def get_account_status(self, email: str) -> AccountResult:
        """Query whether *email* already exists / is verified on the site."""
        ...


class SiteIntegrationError(Exception):
    """Raised when the site returns an error response."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DuplicateAccountError(SiteIntegrationError):
    """Raised when the email is already registered on the site."""
