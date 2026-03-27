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

import re
import traceback

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.core.utils import is_valid_email, normalise_url

log = get_logger(__name__)


def _is_allowed(user_id: int) -> bool:
    if not config.TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in config.TELEGRAM_ALLOWED_USER_IDS


def _parse_create_args(message_text: str):
    """Parse /create command — extracts site URL and email from the message.

    Supports:
      /create site.com email@x.com
      /create https://site.com/auth/register email@x.com
      /create https://site.com/register?ref=123 email@x.com
    """
    text = message_text.strip()
    if text.startswith("/create"):
        text = text[len("/create"):].strip()
    if text.startswith("@"):
        text = re.sub(r"^@\S+\s*", "", text)

    email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    if not email_match:
        return None, None

    email = email_match.group(0).lower()
    remainder = text[:email_match.start()].strip() + " " + text[email_match.end():].strip()
    remainder = remainder.strip()

    url_match = re.search(r"(https?://\S+)", remainder)
    if url_match:
        raw_site = url_match.group(1)
    else:
        parts = remainder.split()
        if parts:
            raw_site = parts[0]
        else:
            return None, email

    raw_site = raw_site.rstrip("/.,;:!?")
    return raw_site, email


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("cmd_start called by user=%s", update.effective_user.id)
    try:
        await update.message.reply_text(
            "👋 *Registration Bot*\n\n"
            "الأوامر المتاحة:\n\n"
            "  `/create site.com email@example.com`\n"
            "  ← يفتح الموقع وينشئ حساب بالإيميل\n\n"
            "يقبل روابط طويلة أيضاً:\n"
            "  `/create https://site.com/auth email@x.com`\n\n"
            "  `/status JOB_ID`\n"
            "  ← حالة العملية\n\n"
            "  `/jobs`\n"
            "  ← آخر العمليات\n\n"
            "  `/help`\n\n"
            f"الرمز الثابت المستخدم: `{config.FIXED_PASSWORD}`",
            parse_mode="Markdown",
        )
    except Exception as exc:
        log.error("cmd_start error: %s\n%s", exc, traceback.format_exc())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.info("cmd_create called by user=%s text=%s", user.id, update.message.text)

    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("⛔ غير مصرح لك باستخدام هذا البوت.")
            return

        raw_site, email = _parse_create_args(update.message.text or "")

        if not raw_site or not email:
            await update.message.reply_text(
                "الاستخدام:\n`/create site.com email@example.com`\n\n"
                "أمثلة:\n"
                "`/create ddxstore.us myemail@gmail.com`\n"
                "`/create https://site.com/register myemail@gmail.com`",
                parse_mode="Markdown",
            )
            return

        if not is_valid_email(email):
            await update.message.reply_text(
                f"❌ `{email}` ليس إيميل صحيح.", parse_mode="Markdown"
            )
            return

        site_url = normalise_url(raw_site)

        from app.jobs.job_manager import JobManager
        job_manager = JobManager()

        job = job_manager.create_job(
            email=email,
            site_url=site_url,
            chat_id=update.effective_chat.id,
        )
        log.info("Job created: id=%s email=%s site=%s", job.job_id, email, site_url)

        from app.jobs.scheduler import scheduler
        scheduler.submit(job, config.FIXED_PASSWORD)
        log.info("Job submitted to scheduler: %s", job.job_id)

        await update.message.reply_text(
            f"✅ *تم قبول الطلب*\n\n"
            f"ID: `{job.job_id}`\n"
            f"الموقع: `{site_url}`\n"
            f"الإيميل: `{email}`\n\n"
            "سأبلغك بالتقدم فور حدوثه 🔄\n"
            "⏱ الحد الأقصى: 60 ثانية",
            parse_mode="Markdown",
        )
        log.info("Reply sent to user for job %s", job.job_id)

    except Exception as exc:
        log.error("cmd_create CRASHED: %s\n%s", exc, traceback.format_exc())
        try:
            await update.message.reply_text(f"❌ خطأ: {exc}")
        except Exception:
            pass


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /status JOB_ID"""
    user = update.effective_user
    log.info("cmd_status called by user=%s", user.id)

    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("⛔ غير مصرح.")
            return

        args = context.args
        if not args:
            await update.message.reply_text(
                "الاستخدام: `/status JOB_ID`", parse_mode="Markdown"
            )
            return

        from app.jobs.job_manager import JobManager
        job_manager = JobManager()

        job_id = args[0]
        job = job_manager.get(job_id)
        if not job:
            await update.message.reply_text(
                f"Job `{job_id}` غير موجود.", parse_mode="Markdown"
            )
            return

        site_info = f"\n  الموقع: `{job.site_url}`" if job.site_url else ""
        text = (
            f"• ID: `{job.job_id}`\n"
            f"  الإيميل: `{job.email}`{site_info}\n"
            f"  الحالة: `{job.status.value}`\n"
            f"  آخر تحديث: `{job.updated_at.strftime('%Y-%m-%d %H:%M UTC')}`\n"
            + (f"  خطأ: {job.error_msg}\n" if job.error_msg else "")
            + (f"  النتيجة: {job.final_result}\n" if job.final_result else "")
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as exc:
        log.error("cmd_status error: %s\n%s", exc, traceback.format_exc())
        try:
            await update.message.reply_text(f"❌ خطأ: {exc}")
        except Exception:
            pass


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the 10 most recent jobs."""
    user = update.effective_user
    log.info("cmd_jobs called by user=%s", user.id)

    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("⛔ غير مصرح.")
            return

        from app.jobs.job_manager import JobManager
        job_manager = JobManager()

        jobs = job_manager.list_recent(limit=10)
        if not jobs:
            await update.message.reply_text("لا توجد عمليات بعد.")
            return

        lines = ["*آخر العمليات:*\n"]
        for j in jobs:
            site_info = f" | {j.site_url}" if j.site_url else ""
            lines.append(
                f"• `{j.job_id}` — `{j.email}`{site_info}\n"
                f"  الحالة: `{j.status.value}`"
                + (f"\n  خطأ: {j.error_msg}" if j.error_msg else "")
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

    except Exception as exc:
        log.error("cmd_jobs error: %s\n%s", exc, traceback.format_exc())
        try:
            await update.message.reply_text(f"❌ خطأ: {exc}")
        except Exception:
            pass
