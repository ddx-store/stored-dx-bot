"""
Register all command handlers with the PTB Application.
Called once at startup from main.py.
"""

from __future__ import annotations

from telegram.ext import Application, CommandHandler

from app.bot.commands import (
    cmd_batch_create,
    cmd_create,
    cmd_help,
    cmd_jobs,
    cmd_retry,
    cmd_start,
    cmd_status,
)


def register_handlers(app: Application) -> None:
    """Attach all handlers to *app*."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("create", cmd_create))
    app.add_handler(CommandHandler("batch_create", cmd_batch_create))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
