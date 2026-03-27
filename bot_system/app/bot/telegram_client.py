"""
Thin wrapper around python-telegram-bot.
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
_lock = threading.Lock()


def build_application() -> Application:
    global _app
    if _app is not None:
        return _app
    _app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )
    return _app


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop
    log.info("Main event loop stored (id=%s)", id(loop))


def get_bot() -> Bot:
    if _app is None:
        raise RuntimeError("Application not built yet")
    return _app.bot


def send_message(chat_id: int, text: str) -> None:
    """Send a plain-text message from any thread via the main PTB event loop."""
    bot = get_bot()
    coro = bot.send_message(chat_id=chat_id, text=text)
    try:
        if _main_loop is not None and _main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
            future.result(timeout=15)
        else:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()
    except Exception as exc:
        log.error("send_message failed: %s", exc)
