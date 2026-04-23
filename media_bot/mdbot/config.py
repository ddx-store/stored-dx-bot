"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001 - dotenv is optional at runtime
    pass


def _csv_ints(raw: str) -> list[int]:
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    token: str
    admin_user_ids: list[int] = field(default_factory=list)
    allowed_user_ids: list[int] = field(default_factory=list)
    max_upload_mb: int = 50
    max_download_mb: int = 500
    rate_limit_count: int = 5
    rate_limit_window_seconds: int = 60
    max_concurrent_downloads: int = 4
    download_dir: Path = Path("/tmp/media_bot_downloads")
    db_path: Path = Path("data/media_bot.db")
    cookies_file: str | None = None
    proxy: str | None = None
    log_level: str = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    def is_allowed(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids


def load_config() -> Config:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is required. Set it in the environment "
            "or a .env file next to bot.py."
        )

    download_dir = Path(os.getenv("DOWNLOAD_DIR", "/tmp/media_bot_downloads"))
    db_path = Path(os.getenv("DB_PATH", "data/media_bot.db"))
    download_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    cookies = os.getenv("COOKIES_FILE", "").strip() or None
    proxy = os.getenv("PROXY", "").strip() or None

    return Config(
        token=token,
        admin_user_ids=_csv_ints(os.getenv("ADMIN_USER_IDS", "")),
        allowed_user_ids=_csv_ints(os.getenv("ALLOWED_USER_IDS", "")),
        max_upload_mb=_int_env("MAX_UPLOAD_MB", 50),
        max_download_mb=_int_env("MAX_DOWNLOAD_MB", 500),
        rate_limit_count=_int_env("RATE_LIMIT_COUNT", 5),
        rate_limit_window_seconds=_int_env("RATE_LIMIT_WINDOW_SECONDS", 60),
        max_concurrent_downloads=_int_env("MAX_CONCURRENT_DOWNLOADS", 4),
        download_dir=download_dir,
        db_path=db_path,
        cookies_file=cookies,
        proxy=proxy,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
