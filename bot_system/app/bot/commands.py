"""
Command definitions for the Telegram bot.

Each async function is registered as a command handler in handlers.py.
Commands:
  /start          — greeting
  /create         — single account creation
  /batch_create   — multi-line batch creation
  /status         — query a job by ID
  /retry          — retry a failed job
  /jobs           — list recent jobs
  /help           — show help
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.core.utils import is_valid_email
from app.jobs.job_manager import JobManager
from app.jobs.scheduler import scheduler
from app.storage.models import Job

log = get_logger(__name__)
_job_manager = JobManager()


def _is_allowed(user_id: int) -> bool:
    if not config.TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in config.TELEGRAM_ALLOWED_USER_IDS


def _deny(update: Update) -> str:
    return "⛔ You are not authorised to use this bot."


# ─────────────────────────────── helpers ─────────────────────────────────


def _format_job(job: Job) -> str:
    return (
        f"• ID: `{job.job_id}`\n"
        f"  Email: `{job.email}`\n"
        f"  Status: `{job.status.value}`\n"
        f"  Updated: `{job.updated_at.strftime('%Y-%m-%d %H:%M UTC')}`\n"
        + (f"  Error: {job.error_msg}\n" if job.error_msg else "")
    )


# ─────────────────────────────── commands ────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Registration Bot*\n\n"
        "Commands:\n"
        "  /create email password\n"
        "  /batch\\_create — paste lines of `email password`\n"
        "  /status JOB\\_ID\n"
        "  /retry JOB\\_ID password\n"
        "  /jobs\n"
        "  /help",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /create email@example.com MyPassword"""
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text(_deny(update))
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: `/create email@example.com YourPassword`", parse_mode="Markdown"
        )
        return

    email, password = args[0].lower().strip(), args[1]

    if not is_valid_email(email):
        await update.message.reply_text(f"❌ `{email}` doesn't look like a valid email address.")
        return

    job = _job_manager.create_job(
        email=email,
        chat_id=update.effective_chat.id,
    )
    scheduler.submit(job, password)
    await update.message.reply_text(
        f"✅ Job accepted\nID: `{job.job_id}`\nEmail: `{email}`\n\nI'll update you as it progresses.",
        parse_mode="Markdown",
    )


async def cmd_batch_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage (send as a single message after the command):
        /batch_create
        email1@example.com pass1
        email2@example.com pass2
    """
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text(_deny(update))
        return

    text = update.message.text or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()][1:]  # skip command line

    if not lines:
        await update.message.reply_text(
            "Usage:\n`/batch_create`\n`email1@example.com password1`\n`email2@example.com password2`",
            parse_mode="Markdown",
        )
        return

    accepted, skipped = [], []

    for line in lines:
        parts = line.split(None, 1)
        if len(parts) < 2:
            skipped.append(f"{line} — missing password")
            continue
        email, password = parts[0].lower().strip(), parts[1].strip()
        if not is_valid_email(email):
            skipped.append(f"{email} — invalid email")
            continue
        job = _job_manager.create_job(email=email, chat_id=update.effective_chat.id)
        scheduler.submit(job, password)
        accepted.append(f"`{email}` → job `{job.job_id}`")

    reply_parts = []
    if accepted:
        reply_parts.append("✅ *Accepted:*\n" + "\n".join(accepted))
    if skipped:
        reply_parts.append("⚠️ *Skipped:*\n" + "\n".join(skipped))
    if not reply_parts:
        reply_parts.append("No valid lines found.")

    await update.message.reply_text("\n\n".join(reply_parts), parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /status JOB_ID"""
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text(_deny(update))
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/status JOB_ID`", parse_mode="Markdown")
        return

    job_id = args[0]
    job = _job_manager.get(job_id)
    if not job:
        await update.message.reply_text(f"Job `{job_id}` not found.", parse_mode="Markdown")
        return

    await update.message.reply_text(_format_job(job), parse_mode="Markdown")


async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /retry JOB_ID NewPassword"""
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text(_deny(update))
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: `/retry JOB_ID NewPassword`", parse_mode="Markdown"
        )
        return

    job_id, password = args[0], args[1]
    original = _job_manager.get(job_id)
    if not original:
        await update.message.reply_text(f"Job `{job_id}` not found.", parse_mode="Markdown")
        return

    if scheduler.is_running(job_id):
        await update.message.reply_text(
            f"Job `{job_id}` is still running — cannot retry.", parse_mode="Markdown"
        )
        return

    # Create a new job with the same email.
    new_job = _job_manager.create_job(
        email=original.email,
        chat_id=update.effective_chat.id,
    )
    scheduler.submit(new_job, password)
    await update.message.reply_text(
        f"♻️ Retry accepted\nNew job ID: `{new_job.job_id}`\nEmail: `{original.email}`",
        parse_mode="Markdown",
    )


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the 10 most recent jobs."""
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text(_deny(update))
        return

    jobs = _job_manager.list_recent(limit=10)
    if not jobs:
        await update.message.reply_text("No jobs found.")
        return

    lines = ["*Recent jobs:*\n"] + [_format_job(j) for j in jobs]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
