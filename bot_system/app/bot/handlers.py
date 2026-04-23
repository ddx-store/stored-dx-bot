from __future__ import annotations

import traceback

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

from app.bot.commands import (
    cmd_accounts,
    cmd_cancel,
    cmd_create,
    cmd_help,
    cmd_jobs,
    cmd_pay,
    cmd_proxies,
    cmd_start,
    cmd_status,
    callback_handler,
    load_all_sessions,
    text_handler,
)
from app.media.handlers import (
    cmd_download,
    cmd_media_help,
    cmd_media_stats,
    media_callback_handler,
    media_url_handler,
)
from app.core.logger import get_logger

log = get_logger(__name__)


async def _combined_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delegate to media callback first, then fall back to existing handler."""
    consumed = await media_callback_handler(update, context)
    if not consumed:
        await callback_handler(update, context)


async def _combined_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Try media URL detection first, then fall back to existing text handler."""
    consumed = await media_url_handler(update, context)
    if not consumed:
        await text_handler(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error(
        "Unhandled exception in update: %s\n%s",
        context.error,
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__),
    )


def register_handlers(app: Application) -> None:
    load_all_sessions()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("create", cmd_create))
    app.add_handler(CommandHandler("pay", cmd_pay))
    app.add_handler(CommandHandler("proxies", cmd_proxies))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    # Media download commands
    app.add_handler(CommandHandler("dl", cmd_download))
    app.add_handler(CommandHandler("mediahelp", cmd_media_help))
    app.add_handler(CommandHandler("mediastats", cmd_media_stats))
    # Combined callback and text handlers (media first, then existing)
    app.add_handler(CallbackQueryHandler(_combined_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _combined_text))
    app.add_error_handler(error_handler)
    log.info("All handlers registered (including media download)")
