"""
Thin wrapper around python-telegram-bot.

Initialises the Application object and exposes helpers used by
handlers.py and commands.py.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from telegram import Bot
from telegram.ext import Application

from app.core.config import config
from app.core.logger import get_logger

log = get_logger(__name__)

_app: Optional[Application] = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def build_application() -> Application:
    """Build and return the PTB Application (call once at startup)."""
    global _app, _main_loop
    if _app is not None:
        return _app
    _app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )
    return _app


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the main thread's event loop so worker threads can schedule coroutines."""
    global _main_loop
    _main_loop = loop


def get_bot() -> Bot:
    """Return the underlying Bot instance (after build_application)."""
    if _app is None:
        raise RuntimeError("Application not built yet. Call build_application() first.")
    return _app.bot


def send_message(chat_id: int, text: str) -> None:
    """
    Send a text message to *chat_id* from any thread (sync or async).
    Uses run_coroutine_threadsafe to push into the main PTB event loop.
    """
    bot = get_bot()

    coro = bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

    loop = _main_loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            future.result(timeout=15)
        except Exception as exc:
            log.error("Failed to send Telegram message to %d: %s", chat_id, exc)
    else:
        try:
            asyncio.run(coro)
        except Exception as exc:
            log.error("Failed to send Telegram message to %d: %s", chat_id, exc)
