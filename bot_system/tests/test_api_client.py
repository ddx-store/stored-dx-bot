"""
Unit tests for the site API client — uses responses library to mock HTTP.

Install: pip install responses
Run: pytest tests/test_api_client.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ["SITE_API_BASE_URL"] = "https://mysite.example.com/api/v1"
os.environ["SITE_API_KEY"] = "test-key"

import pytest

try:
    import responses as responses_lib
    HAS_RESPONSES = True
except ImportError:
    responses_lib = None  # type: ignore
    HAS_RESPONSES = False

from app.site.api_client import ApiClient
from app.site.base import DuplicateAccountError, SiteIntegrationError


def _activate(fn):
    """Decorator that skips if responses not installed, else wraps with activate."""
    if not HAS_RESPONSES:
        return pytest.mark.skip(reason="responses library not installed")(fn)
    return responses_lib.activate(fn)


@pytest.mark.skipif(not HAS_RESPONSES, reason="responses library not installed")
class TestApiClientCreateAccount:
    @_activate
    def test_create_account_success(self):
        responses_lib.add(
            responses_lib.POST,
            "https://mysite.example.com/api/v1/register",
            json={"success": True},
            status=200,
        )
        client = ApiClient()
        result = client.create_account("test@example.com", "Password123")
        assert result.success is True

    @_activate
    def test_create_account_duplicate(self):
        responses_lib.add(
            responses_lib.POST,
            "https://mysite.example.com/api/v1/register",
            json={"error": "already_exists"},
            status=409,
        )
        client = ApiClient()
        with pytest.raises(DuplicateAccountError):
            client.create_account("existing@example.com", "Password123")

    @_activate
    def test_create_account_server_error(self):
        responses_lib.add(
            responses_lib.POST,
            "https://mysite.example.com/api/v1/register",
            json={"error": "Internal server error"},
            status=500,
        )
        client = ApiClient()
        with pytest.raises(SiteIntegrationError):
            client.create_account("test@example.com", "Password123")

    @_activate
    def test_submit_otp_success(self):
        responses_lib.add(
            responses_lib.POST,
            "https://mysite.example.com/api/v1/verify-otp",
            json={"verified": True},
            status=200,
        )
        client = ApiClient()
        result = client.submit_otp("test@example.com", "123456")
        assert result.success is True

    @_activate
    def test_submit_otp_failure(self):
        responses_lib.add(
            responses_lib.POST,
            "https://mysite.example.com/api/v1/verify-otp",
            json={"error": "invalid_otp"},
            status=400,
        )
        client = ApiClient()
        with pytest.raises(SiteIntegrationError):
            client.submit_otp("test@example.com", "999999")
