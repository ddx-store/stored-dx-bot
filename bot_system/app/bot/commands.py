from __future__ import annotations

import re
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.core.utils import is_valid_email, normalise_url

log = get_logger(__name__)

PRESET_SITES = [
    {"label": "ChatGPT", "url": "chatgpt.com", "icon": "🤖"},
    {"label": "Google", "url": "google.com", "icon": "🔍"},
    {"label": "Outlook", "url": "outlook.com", "icon": "📧"},
    {"label": "GitHub", "url": "github.com", "icon": "💻"},
    {"label": "Discord", "url": "discord.com", "icon": "🎮"},
    {"label": "Twitter/X", "url": "x.com", "icon": "🐦"},
]

_pending_site = {}


def _is_allowed(user_id: int) -> bool:
    if not config.TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in config.TELEGRAM_ALLOWED_USER_IDS


def _parse_create_args(message_text: str):
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


def _build_main_menu():
    keyboard = []
    row = []
    for i, site in enumerate(PRESET_SITES):
        row.append(InlineKeyboardButton(
            f"{site['icon']} {site['label']}",
            callback_data=f"site:{site['url']}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🌐 موقع اخر ...", callback_data="site:custom")])
    return InlineKeyboardMarkup(keyboard)


def _start_text():
    return (
        "╔══════════════════════════════╗\n"
        "║    بوت التسجيل التلقائي      ║\n"
        "╚══════════════════════════════╝\n"
        "\n"
        "  اختر الموقع المطلوب:\n"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("cmd_start called by user=%s", update.effective_user.id)
    try:
        if not _is_allowed(update.effective_user.id):
            await update.message.reply_text("غير مصرح لك.")
            return
        await update.message.reply_text(_start_text(), reply_markup=_build_main_menu())
    except Exception as exc:
        log.error("cmd_start error: %s\n%s", exc, traceback.format_exc())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = (
            "╔══════════════════════════════╗\n"
            "║         المساعدة             ║\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  1. اضغط /start\n"
            "  2. اختر الموقع من الازرار\n"
            "  3. ارسل الايميل\n"
            "  4. انتظر النتيجة\n"
            "\n"
            "  او استخدم:\n"
            "  /create site.com email@x.com\n"
        )
        await update.message.reply_text(text)
    except Exception as exc:
        log.error("cmd_help error: %s\n%s", exc, traceback.format_exc())


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not _is_allowed(user_id):
        await query.edit_message_text("غير مصرح لك.")
        return

    data = query.data or ""
    log.info("callback_handler: user=%s data=%s", user_id, data)

    if data == "site:custom":
        _pending_site[user_id] = "__custom__"
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:menu")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║       موقع مخصص             ║\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  ارسل الموقع والايميل:\n"
            "\n"
            "  مثال:\n"
            "  site.com email@example.com\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data == "back:menu":
        _pending_site.pop(user_id, None)
        await query.edit_message_text(_start_text(), reply_markup=_build_main_menu())
        return

    if data.startswith("site:"):
        site_url = data[5:]
        site_label = site_url
        for ps in PRESET_SITES:
            if ps["url"] == site_url:
                site_label = f"{ps['icon']} {ps['label']}"
                break

        _pending_site[user_id] = site_url
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:menu")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  {site_label}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📧 ارسل الايميل:\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not _is_allowed(user.id):
        return

    pending = _pending_site.get(user.id)

    if pending == "__custom__":
        _pending_site.pop(user.id, None)
        email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        if not email_match:
            await update.message.reply_text("ما لقيت ايميل. ارسل الموقع + الايميل.")
            return

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
                await update.message.reply_text("ارسل رابط الموقع مع الايميل.")
                return

        raw_site = raw_site.rstrip("/.,;:!?")
        await _start_job(update, raw_site, email)
        return

    if pending and pending != "__custom__":
        _pending_site.pop(user.id, None)
        email = text.strip()
        if not is_valid_email(email):
            await update.message.reply_text(f"  ❌  {email}\n  هذا مو ايميل صحيح.")
            return
        await _start_job(update, pending, email)
        return

    log.info("Received text from user=%s: %s", user.id, text[:100])


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.info("cmd_create called by user=%s text=%s", user.id, update.message.text)

    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("غير مصرح لك.")
            return

        raw_site, email = _parse_create_args(update.message.text or "")

        if not raw_site or not email:
            await update.message.reply_text(
                "  الاستخدام:\n"
                "  /create site.com email@example.com\n\n"
                "  او اضغط /start"
            )
            return

        if not is_valid_email(email):
            await update.message.reply_text(f"  ❌  {email} ليس ايميل صحيح.")
            return

        await _start_job(update, raw_site, email)

    except Exception as exc:
        log.error("cmd_create CRASHED: %s\n%s", exc, traceback.format_exc())
        try:
            await update.message.reply_text(f"خطأ: {exc}")
        except Exception:
            pass


async def _start_job(update: Update, raw_site: str, email: str):
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


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("غير مصرح.")
            return

        args = context.args
        if not args:
            await update.message.reply_text("  الاستخدام: /status JOB_ID")
            return

        from app.jobs.job_manager import JobManager
        job_manager = JobManager()

        job_id = args[0]
        job = job_manager.get(job_id)
        if not job:
            await update.message.reply_text(f"  ❌  {job_id} غير موجود.")
            return

        status_icon = "✅" if job.status.value == "completed" else "❌" if job.status.value == "failed" else "⏳"
        text = (
            f"  {status_icon}  {job.status.value}\n"
            f"  📧  {job.email}\n"
            f"  🌐  {job.site_url}\n"
        )
        if job.error_msg:
            text += f"  ❌  {job.error_msg}\n"
        if job.final_result:
            text += f"  📋  {job.final_result}\n"
        await update.message.reply_text(text)

    except Exception as exc:
        log.error("cmd_status error: %s\n%s", exc, traceback.format_exc())


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("غير مصرح.")
            return

        from app.jobs.job_manager import JobManager
        job_manager = JobManager()

        jobs = job_manager.list_recent(limit=10)
        if not jobs:
            await update.message.reply_text("  لا توجد عمليات بعد.")
            return

        lines = ["╔══════════════════════════════╗"]
        lines.append("║       اخر العمليات           ║")
        lines.append("╚══════════════════════════════╝")
        lines.append("")
        for j in jobs:
            status_icon = "✅" if j.status.value == "completed" else "❌" if j.status.value == "failed" else "⏳"
            lines.append(f"  {status_icon}  {j.email}")
            if j.site_url:
                lines.append(f"      🌐 {j.site_url}")
            lines.append("")
        await update.message.reply_text("\n".join(lines))

    except Exception as exc:
        log.error("cmd_jobs error: %s\n%s", exc, traceback.format_exc())
