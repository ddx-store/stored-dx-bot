from __future__ import annotations

import asyncio
import threading
from typing import Optional

from telegram import Bot, InlineKeyboardMarkup
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


def _run_coro(coro):
    try:
        if _main_loop is not None and _main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
            return future.result(timeout=15)
        else:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
    except Exception as exc:
        log.error("Telegram API call failed: %s", exc)
        return None


def send_message(chat_id: int, text: str, reply_markup=None) -> Optional[int]:
    bot = get_bot()
    coro = bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    result = _run_coro(coro)
    if result:
        return result.message_id
    return None


def edit_message(chat_id: int, message_id: int, text: str, reply_markup=None) -> bool:
    bot = get_bot()
    coro = bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=reply_markup,
    )
    result = _run_coro(coro)
    return result is not None


def delete_message(chat_id: int, message_id: int) -> bool:
    bot = get_bot()
    coro = bot.delete_message(chat_id=chat_id, message_id=message_id)
    result = _run_coro(coro)
    return result is not None
