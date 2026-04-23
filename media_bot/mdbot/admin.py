"""Admin-only commands."""
from __future__ import annotations

import datetime as dt
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Config
from .storage import Stats
from .utils import safe_html

log = logging.getLogger(__name__)


def _require_admin(update: Update, cfg: Config) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return cfg.is_admin(user.id)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _require_admin(update, cfg):
        return
    stats: Stats = context.application.bot_data["stats"]
    data = stats.summary()
    lines = [
        "<b>Stats</b>",
        f"• Total: {data['total']} (ok: {data['success']}, fail: {data['failure']})",
        f"• Unique users: {data['unique_users']}",
        "",
        "<b>By platform</b>",
    ]
    for row in data["by_platform"]:
        lines.append(
            f"• {safe_html(row['platform'])}: {row['total']} "
            f"(ok {row['success']})"
        )
    if data["top_users"]:
        lines.append("")
        lines.append("<b>Top users</b>")
        for row in data["top_users"]:
            uname = f"@{row['username']}" if row["username"] else f"id={row['user_id']}"
            lines.append(f"• {safe_html(uname)}: {row['count']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _require_admin(update, cfg):
        return
    stats: Stats = context.application.bot_data["stats"]
    errors = stats.recent_errors(limit=15)
    if not errors:
        await update.message.reply_text("No recent errors.")
        return
    lines = ["<b>Recent errors</b>"]
    for row in errors:
        when = dt.datetime.utcfromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"• {when} UTC · {safe_html(row['platform'])} · "
            f"uid {row['user_id']}\n  {safe_html(row['error'] or '')[:200]}\n"
            f"  <a href=\"{safe_html(row['url'])}\">link</a>"
        )
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    cfg: Config = context.application.bot_data["cfg"]
    role = "admin" if cfg.is_admin(user.id) else "user"
    await update.message.reply_text(
        f"id={user.id} role={role} username={user.username or '-'}"
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("whoami", whoami_cmd))
