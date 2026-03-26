"""
Thin wrapper around python-telegram-bot.

Initialises the Application object and exposes helpers used by
handlers.py and commands.py.
"""

from __future__ import annotations

from typing import Optional

from telegram import Bot
from telegram.ext import Application

from app.core.config import config
from app.core.logger import get_logger

log = get_logger(__name__)

_app: Optional[Application] = None


def build_application() -> Application:
    """Build and return the PTB Application (call once at startup)."""
    global _app
    if _app is not None:
        return _app
    _app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )
    return _app


def get_bot() -> Bot:
    """Return the underlying Bot instance (after build_application)."""
    if _app is None:
        raise RuntimeError("Application not built yet. Call build_application() first.")
    return _app.bot


def send_message(chat_id: int, text: str) -> None:
    """
    Synchronously send a text message to *chat_id*.

    Only use this outside of an async context (e.g., from a worker thread).
    Inside async handlers, use context.bot.send_message directly.
    """
    import asyncio
    bot = get_bot()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Schedule coroutine from a sync context.
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown"),
                loop,
            ).result(timeout=10)
        else:
            loop.run_until_complete(
                bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            )
    except Exception as exc:
        log.error("Failed to send Telegram message to %d: %s", chat_id, exc)
