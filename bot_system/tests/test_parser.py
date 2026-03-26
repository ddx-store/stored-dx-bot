"""
Unit tests for the OTP parser.
Run from bot_system/ with:  pytest tests/test_parser.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub minimal env before importing config-dependent modules.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("SITE_API_BASE_URL", "https://example.com/api/v1")

from app.core.enums import OtpType
from app.gmail.parser import extract_otp


class TestExtractOtp:
    def test_numeric_otp_explicit_label(self):
        body = "Your OTP is: 482916. It expires in 10 minutes."
        code, kind, link = extract_otp(body)
        assert code == "482916"
        assert kind == OtpType.NUMERIC
        assert link is None

    def test_numeric_otp_code_label(self):
        body = "Your verification code is 123456"
        code, kind, link = extract_otp(body)
        assert code == "123456"
        assert kind == OtpType.NUMERIC

    def test_numeric_otp_reversed_sentence(self):
        body = "123456 is your one-time password"
        code, kind, link = extract_otp(body)
        assert code == "123456"
        assert kind == OtpType.NUMERIC

    def test_activation_link(self):
        body = "Click here to verify your account: https://example.com/verify?token=abc123xyz"
        code, kind, link = extract_otp(body)
        assert kind == OtpType.LINK
        assert link is not None
        assert "verify" in link

    def test_empty_body(self):
        code, kind, link = extract_otp("")
        assert code is None
        assert kind == OtpType.UNKNOWN
        assert link is None

    def test_no_otp_in_body(self):
        body = "Welcome to our platform. Please log in to your account."
        code, kind, link = extract_otp(body)
        assert kind == OtpType.UNKNOWN


class TestExtractOtpEdgeCases:
    def test_otp_with_spaces(self):
        body = "Code: 7 7 7 1 2 3"
        # Should not match because numbers have spaces between them — OK to return UNKNOWN
        # (The regex looks for contiguous digits)
        code, kind, link = extract_otp(body)
        # Just ensuring no crash
        assert kind in (OtpType.NUMERIC, OtpType.UNKNOWN)

    def test_link_with_confirm_keyword(self):
        body = "Please confirm your email: https://example.com/confirm/abc123"
        code, kind, link = extract_otp(body)
        assert kind == OtpType.LINK

    def test_link_preferred_over_number(self):
        body = "Your code is 123456. Or click: https://example.com/verify?code=123456"
        code, kind, link = extract_otp(body)
        # Link should take priority.
        assert kind == OtpType.LINK
