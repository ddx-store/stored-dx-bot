"""
Command definitions for the Telegram bot.

Commands:
  /start        — greeting
  /create       — create account: /create site.com email@example.com
  /status       — query a job by ID
  /jobs         — list recent jobs
  /help         — show help
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.core.utils import is_valid_email, normalise_url
from app.jobs.job_manager import JobManager
from app.jobs.scheduler import scheduler
from app.storage.models import Job

log = get_logger(__name__)
_job_manager = JobManager()


def _is_allowed(user_id: int) -> bool:
    if not config.TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in config.TELEGRAM_ALLOWED_USER_IDS


def _format_job(job: Job) -> str:
    site = f"\n  Site: `{job.site_url}`" if job.site_url else ""
    return (
        f"• ID: `{job.job_id}`\n"
        f"  Email: `{job.email}`"
        f"{site}\n"
        f"  Status: `{job.status.value}`\n"
        f"  Updated: `{job.updated_at.strftime('%Y-%m-%d %H:%M UTC')}`\n"
        + (f"  Error: {job.error_msg}\n" if job.error_msg else "")
        + (f"  Result: {job.final_result}\n" if job.final_result else "")
    )


# ─────────────────────────────── commands ────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Registration Bot*\n\n"
        "الأوامر المتاحة:\n\n"
        "  `/create site.com email@example.com`\n"
        "  ← يفتح الموقع وينشئ حساب بالإيميل\n\n"
        "  `/status JOB_ID`\n"
        "  ← حالة العملية\n\n"
        "  `/jobs`\n"
        "  ← آخر العمليات\n\n"
        "  `/help`\n\n"
        f"الرمز الثابت المستخدم: `{config.FIXED_PASSWORD}`",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage: /create site.com email@example.com

    - site.com      : الموقع المستهدف (مع أو بدون https://)
    - email         : الإيميل المراد تسجيله
    - الرمز السري   : ثابت في الكود (FIXED_PASSWORD)
    """
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ غير مصرح لك باستخدام هذا البوت.")
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "الاستخدام:\n`/create site.com email@example.com`\n\n"
            "مثال:\n`/create ddxstore.us myemail@gmail.com`",
            parse_mode="Markdown",
        )
        return

    raw_site = args[0].strip()
    email = args[1].lower().strip()

    # Validate email
    if not is_valid_email(email):
        await update.message.reply_text(
            f"❌ `{email}` ليس إيميل صحيح.", parse_mode="Markdown"
        )
        return

    # Normalise site URL
    site_url = normalise_url(raw_site)

    # Create job
    job = _job_manager.create_job(
        email=email,
        site_url=site_url,
        chat_id=update.effective_chat.id,
    )

    # Submit to background scheduler
    scheduler.submit(job, config.FIXED_PASSWORD)

    await update.message.reply_text(
        f"✅ *تم قبول الطلب*\n\n"
        f"ID: `{job.job_id}`\n"
        f"الموقع: `{site_url}`\n"
        f"الإيميل: `{email}`\n\n"
        "سأبلغك بالتقدم فور حدوثه 🔄",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /status JOB_ID"""
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ غير مصرح.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "الاستخدام: `/status JOB_ID`", parse_mode="Markdown"
        )
        return

    job_id = args[0]
    job = _job_manager.get(job_id)
    if not job:
        await update.message.reply_text(
            f"Job `{job_id}` غير موجود.", parse_mode="Markdown"
        )
        return

    await update.message.reply_text(_format_job(job), parse_mode="Markdown")


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the 10 most recent jobs."""
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ غير مصرح.")
        return

    jobs = _job_manager.list_recent(limit=10)
    if not jobs:
        await update.message.reply_text("لا توجد عمليات بعد.")
        return

    lines = ["*آخر العمليات:*\n"] + [_format_job(j) for j in jobs]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
