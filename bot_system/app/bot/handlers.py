from __future__ import annotations

import traceback

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

from app.bot.commands import (
    cmd_create,
    cmd_help,
    cmd_jobs,
    cmd_pay,
    cmd_start,
    cmd_status,
    callback_handler,
    text_handler,
)
from app.core.logger import get_logger

log = get_logger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error(
        "Unhandled exception in update: %s\n%s",
        context.error,
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__),
    )


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("create", cmd_create))
    app.add_handler(CommandHandler("pay", cmd_pay))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    log.info("All handlers registered")
