"""
Register all command handlers with the PTB Application.
Called once at startup from main.py.
"""

from __future__ import annotations

import traceback

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot.commands import (
    cmd_create,
    cmd_help,
    cmd_jobs,
    cmd_start,
    cmd_status,
)
from app.core.logger import get_logger

log = get_logger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — logs unhandled errors."""
    log.error(
        "Unhandled exception in update: %s\n%s",
        context.error,
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__),
    )


async def echo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch all non-command messages and log them."""
    log.info(
        "Received non-command message from user=%s: %s",
        update.effective_user.id if update.effective_user else "?",
        (update.message.text or "")[:100] if update.message else "",
    )


def register_handlers(app: Application) -> None:
    """Attach all handlers to *app*."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("create", cmd_create))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_handler))
    app.add_error_handler(error_handler)
    log.info("All handlers registered")
