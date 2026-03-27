"""
Configuration module — reads all settings from environment variables.
All values are typed and validated at startup.
"""

from __future__ import annotations

import os
from typing import List


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            "See .env.example for reference."
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
        raise EnvironmentError(
            f"Environment variable '{key}' must be an integer, got: {raw!r}"
        ) from exc


def _bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no", ""):
        return default
    raise EnvironmentError(
        f"Environment variable '{key}' must be a boolean, got: {raw!r}"
    )


class Config:
    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #
    TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")

    TELEGRAM_ALLOWED_USER_IDS: List[int] = [
        int(uid.strip())
        for uid in _optional("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if uid.strip().isdigit()
    ]

    # ------------------------------------------------------------------ #
    # Gmail — IMAP with App Password
    # ------------------------------------------------------------------ #
    GMAIL_USER: str = _optional("GMAIL_USER", "")
    GMAIL_APP_PASSWORD: str = _optional("GMAIL_APP_PASSWORD", "")
    GMAIL_OTP_LABEL: str = _optional("GMAIL_OTP_LABEL", "TO_BOT")

    # Legacy OAuth fields (kept for backwards compat, not used with IMAP)
    GMAIL_CREDENTIALS_FILE: str = _optional("GMAIL_CREDENTIALS_FILE", "credentials.json")
    GMAIL_TOKEN_FILE: str = _optional("GMAIL_TOKEN_FILE", "token.json")

    # ------------------------------------------------------------------ #
    # Target website integration
    # ------------------------------------------------------------------ #
    SITE_API_BASE_URL: str = _optional(
        "SITE_API_BASE_URL", "https://YOUR_SITE_BASE_URL_HERE/api/v1"
    )
    SITE_API_KEY: str = _optional("SITE_API_KEY", "")
    SITE_INTEGRATION_MODE: str = _optional("SITE_INTEGRATION_MODE", "api")

    # ------------------------------------------------------------------ #
    # OTP behaviour
    # ------------------------------------------------------------------ #
    OTP_TIMEOUT_SECONDS: int = _int("OTP_TIMEOUT_SECONDS", 120)
    OTP_POLL_INTERVAL_SECONDS: int = _int("OTP_POLL_INTERVAL_SECONDS", 5)
    OTP_MAX_ATTEMPTS: int = _int("OTP_MAX_ATTEMPTS", 3)

    # ------------------------------------------------------------------ #
    # Storage
    # ------------------------------------------------------------------ #
    DB_PATH: str = _optional("DB_PATH", "bot_system/data/jobs.db")

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO").upper()

    # ------------------------------------------------------------------ #
    # Network
    # ------------------------------------------------------------------ #
    HTTP_TIMEOUT_SECONDS: int = _int("HTTP_TIMEOUT_SECONDS", 30)
    HTTP_MAX_RETRIES: int = _int("HTTP_MAX_RETRIES", 3)
    HTTP_RETRY_BACKOFF: float = float(_optional("HTTP_RETRY_BACKOFF", "1.5"))


config = Config()
