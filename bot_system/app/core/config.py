"""
Configuration module — reads all settings from environment variables.

Supports .env files via python-dotenv (loaded in main.py).
All secrets come from env vars — never hardcoded.
"""

from __future__ import annotations

import os
from typing import List


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set."
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise EnvironmentError(f"'{key}' must be an integer, got: {raw!r}") from exc


class Config:
    TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
    TELEGRAM_ALLOWED_USER_IDS: List[int] = [
        int(uid.strip())
        for uid in _optional("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if uid.strip().isdigit()
    ]

    FIXED_PASSWORD: str = _optional("FIXED_PASSWORD", "Hh123456789Hh")

    GMAIL_USER: str = _optional("GMAIL_USER", "")
    GMAIL_APP_PASSWORD: str = _optional("GMAIL_APP_PASSWORD", "")
    GMAIL_OTP_LABEL: str = _optional("GMAIL_OTP_LABEL", "TO_BOT")

    OTP_TIMEOUT_SECONDS: int = _int("OTP_TIMEOUT_SECONDS", 120)
    OTP_POLL_INTERVAL_SECONDS: int = _int("OTP_POLL_INTERVAL_SECONDS", 5)
    OTP_MAX_ATTEMPTS: int = _int("OTP_MAX_ATTEMPTS", 3)

    DB_PATH: str = _optional("DB_PATH", "data/jobs.db")

    LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO").upper()

    HTTP_TIMEOUT_SECONDS: int = _int("HTTP_TIMEOUT_SECONDS", 30)
    HTTP_MAX_RETRIES: int = _int("HTTP_MAX_RETRIES", 3)
    HTTP_RETRY_BACKOFF: float = float(_optional("HTTP_RETRY_BACKOFF", "1.5"))

    SITE_API_BASE_URL: str = _optional("SITE_API_BASE_URL", "")
    SITE_API_KEY: str = _optional("SITE_API_KEY", "")
    SITE_INTEGRATION_MODE: str = _optional("SITE_INTEGRATION_MODE", "playwright")
    GMAIL_CREDENTIALS_FILE: str = _optional("GMAIL_CREDENTIALS_FILE", "credentials.json")
    GMAIL_TOKEN_FILE: str = _optional("GMAIL_TOKEN_FILE", "token.json")

    MAX_CONCURRENT_JOBS: int = _int("MAX_CONCURRENT_JOBS", 2)
    ADMIN_CHAT_ID: int = _int("ADMIN_CHAT_ID", 0)
    CLEANUP_DAYS: int = _int("CLEANUP_DAYS", 30)

    # Multi-Gmail pool (JSON array: [{"user":..,"app_password":..}])
    GMAIL_ACCOUNTS: str = _optional("GMAIL_ACCOUNTS", "")

    # CAPTCHA solvers (at least one needed for auto-solving)
    CAPMONSTER_API_KEY: str = _optional("CAPMONSTER_API_KEY", "")
    TWOCAPTCHA_API_KEY: str = _optional("TWOCAPTCHA_API_KEY", "")
    ANTICAPTCHA_API_KEY: str = _optional("ANTICAPTCHA_API_KEY", "")

    # Session cache TTL in minutes
    SESSION_CACHE_TTL_MINUTES: int = _int("SESSION_CACHE_TTL_MINUTES", 40)

    # Bulk throttler delays (seconds)
    BULK_INITIAL_DELAY: float = float(_optional("BULK_INITIAL_DELAY", "35"))
    BULK_MIN_DELAY: float = float(_optional("BULK_MIN_DELAY", "10"))
    BULK_MAX_DELAY: float = float(_optional("BULK_MAX_DELAY", "180"))


config = Config()
