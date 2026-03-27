from __future__ import annotations

import re
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.core.utils import is_valid_email, normalise_url, new_job_id

log = get_logger(__name__)

PRESET_SITES = [
    {"label": "ChatGPT", "url": "chatgpt.com", "icon": "🤖"},
    {"label": "Canva", "url": "canva.com", "icon": "🎨"},
    {"label": "Google", "url": "google.com", "icon": "🔍"},
    {"label": "Outlook", "url": "outlook.com", "icon": "📧"},
    {"label": "GitHub", "url": "github.com", "icon": "💻"},
    {"label": "Discord", "url": "discord.com", "icon": "🎮"},
    {"label": "Twitter/X", "url": "x.com", "icon": "🐦"},
]

PAYMENT_SITES = [
    {"label": "ChatGPT Plus", "url": "chatgpt.com", "icon": "🤖"},
    {"label": "Canva Pro", "url": "canva.com", "icon": "🎨"},
    {"label": "ProtonVPN", "url": "protonvpn.com", "icon": "🔒"},
    {"label": "Pixlr", "url": "pixlr.com", "icon": "🖼"},
    {"label": "Replit", "url": "replit.com", "icon": "💻"},
]

_pending_site = {}
_pending_payment = {}


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


def _build_home_menu():
    keyboard = [
        [InlineKeyboardButton("📝  إنشاء حساب", callback_data="menu:register")],
        [InlineKeyboardButton("💳  تفعيل حساب", callback_data="menu:activate")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _build_register_sites_menu():
    keyboard = []
    row = []
    for i, site in enumerate(PRESET_SITES):
        row.append(InlineKeyboardButton(
            f"{site['icon']} {site['label']}",
            callback_data=f"reg:{site['url']}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🌐 موقع اخر ...", callback_data="reg:custom")])
    keyboard.append([InlineKeyboardButton("◀ رجوع", callback_data="back:home")])
    return InlineKeyboardMarkup(keyboard)


def _build_payment_sites_menu():
    keyboard = []
    row = []
    for i, site in enumerate(PAYMENT_SITES):
        row.append(InlineKeyboardButton(
            f"{site['icon']} {site['label']}",
            callback_data=f"pay:{site['url']}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🌐 موقع اخر ...", callback_data="pay:custom")])
    keyboard.append([InlineKeyboardButton("◀ رجوع", callback_data="back:home")])
    return InlineKeyboardMarkup(keyboard)


def _home_text():
    return (
        "╔══════════════════════════════╗\n"
        "║       STORED DX BOT         ║\n"
        "╠══════════════════════════════╣\n"
        "║                              ║\n"
        "║   📝  إنشاء حساب جديد        ║\n"
        "║   💳  تفعيل حساب (اشتراك)    ║\n"
        "║                              ║\n"
        "╚══════════════════════════════╝\n"
        "\n"
        "  اختر الخدمة المطلوبة:\n"
    )


def _register_text():
    return (
        "╔══════════════════════════════╗\n"
        "║   📝  إنشاء حساب جديد        ║\n"
        "╚══════════════════════════════╝\n"
        "\n"
        "  اختر الموقع:\n"
    )


def _activate_text():
    return (
        "╔══════════════════════════════╗\n"
        "║   💳  تفعيل حساب (اشتراك)    ║\n"
        "╚══════════════════════════════╝\n"
        "\n"
        "  اختر الموقع:\n"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("cmd_start called by user=%s", update.effective_user.id)
    try:
        if not _is_allowed(update.effective_user.id):
            await update.message.reply_text("غير مصرح لك.")
            return
        _pending_site.pop(update.effective_user.id, None)
        _pending_payment.pop(update.effective_user.id, None)
        await update.message.reply_text(_home_text(), reply_markup=_build_home_menu())
    except Exception as exc:
        log.error("cmd_start error: %s\n%s", exc, traceback.format_exc())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = (
            "╔══════════════════════════════╗\n"
            "║         المساعدة             ║\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📝 إنشاء حساب:\n"
            "  ───────────────\n"
            "  1. اضغط /start\n"
            "  2. اختر  إنشاء حساب\n"
            "  3. اختر الموقع\n"
            "  4. ارسل الايميل\n"
            "  5. انتظر النتيجة\n"
            "\n"
            "  💳 تفعيل حساب:\n"
            "  ───────────────\n"
            "  1. اضغط /start\n"
            "  2. اختر  تفعيل حساب\n"
            "  3. اختر الموقع\n"
            "  4. ارسل الايميل\n"
            "  5. ارسل الباسوورد\n"
            "  6. ارسل بيانات البطاقة\n"
            "\n"
            "  الاوامر:\n"
            "  ───────────────\n"
            "  /start - القائمة الرئيسية\n"
            "  /create site.com email\n"
            "  /pay - تفعيل حساب\n"
        )
        await update.message.reply_text(text)
    except Exception as exc:
        log.error("cmd_help error: %s\n%s", exc, traceback.format_exc())


async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("cmd_pay called by user=%s", update.effective_user.id)
    try:
        if not _is_allowed(update.effective_user.id):
            await update.message.reply_text("غير مصرح لك.")
            return
        _pending_site.pop(update.effective_user.id, None)
        _pending_payment.pop(update.effective_user.id, None)
        await update.message.reply_text(_activate_text(), reply_markup=_build_payment_sites_menu())
    except Exception as exc:
        log.error("cmd_pay error: %s\n%s", exc, traceback.format_exc())


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not _is_allowed(user_id):
        await query.edit_message_text("غير مصرح لك.")
        return

    data = query.data or ""
    log.info("callback_handler: user=%s data=%s", user_id, data)

    if data == "back:home":
        _pending_site.pop(user_id, None)
        _pending_payment.pop(user_id, None)
        await query.edit_message_text(_home_text(), reply_markup=_build_home_menu())
        return

    if data == "menu:register":
        _pending_payment.pop(user_id, None)
        await query.edit_message_text(_register_text(), reply_markup=_build_register_sites_menu())
        return

    if data == "menu:activate":
        _pending_site.pop(user_id, None)
        await query.edit_message_text(_activate_text(), reply_markup=_build_payment_sites_menu())
        return

    if data == "back:regsites":
        _pending_site.pop(user_id, None)
        await query.edit_message_text(_register_text(), reply_markup=_build_register_sites_menu())
        return

    if data == "back:paysites":
        _pending_payment.pop(user_id, None)
        await query.edit_message_text(_activate_text(), reply_markup=_build_payment_sites_menu())
        return

    if data == "reg:custom":
        _pending_site[user_id] = "__custom__"
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:regsites")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║   📝  موقع مخصص              ║\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  ارسل الموقع والايميل:\n"
            "\n"
            "  مثال:\n"
            "  site.com email@example.com\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("reg:"):
        site_url = data[4:]
        site_label = site_url
        for ps in PRESET_SITES:
            if ps["url"] == site_url:
                site_label = f"{ps['icon']} {ps['label']}"
                break

        _pending_site[user_id] = site_url
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:regsites")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  📝  {site_label}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📧 ارسل الايميل:\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data == "pay:custom":
        _pending_payment[user_id] = {"step": "custom_site"}
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:paysites")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║   💳  موقع مخصص              ║\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  ارسل رابط الموقع:\n"
            "  مثال: site.com\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("pay:"):
        site_val = data[4:]
        site_label = site_val
        for ps in PAYMENT_SITES:
            if ps["url"] == site_val:
                site_label = f"{ps['icon']} {ps['label']}"
                break

        _pending_payment[user_id] = {"step": "email", "site_url": site_val, "label": site_label}
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:paysites")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  💳  {site_label}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📧 ارسل الايميل (حساب الموقع):\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not _is_allowed(user.id):
        return

    payment = _pending_payment.get(user.id)
    if payment:
        await _handle_payment_text(update, user.id, text, payment)
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


async def _handle_payment_text(update: Update, user_id: int, text: str, payment: dict) -> None:
    step = payment.get("step", "")

    if step == "custom_site":
        raw = text.strip().split()[0] if text.strip() else ""
        if not raw:
            await update.message.reply_text("ارسل رابط الموقع.")
            return
        raw = raw.rstrip("/.,;:!?")
        payment["site_url"] = raw
        payment["label"] = raw
        payment["step"] = "email"
        await update.message.reply_text(
            "╔══════════════════════════════╗\n"
            f"║  💳  {raw}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📧 ارسل الايميل (حساب الموقع):\n"
        )
        return

    if step == "email":
        email = text.strip()
        if not is_valid_email(email):
            await update.message.reply_text(f"  ❌  {email}\n  هذا مو ايميل صحيح.")
            return
        payment["email"] = email
        payment["step"] = "password"
        await update.message.reply_text(
            f"  ✅  {email}\n"
            "\n"
            "  🔑 ارسل الباسوورد:\n"
        )
        return

    if step == "password":
        password = text.strip()
        if len(password) < 4:
            await update.message.reply_text("الباسوورد قصير جدا.")
            return
        payment["password"] = password
        payment["step"] = "card"
        await update.message.reply_text(
            "  ✅  تم حفظ الباسوورد\n"
            "\n"
            "  💳 ارسل بيانات البطاقة:\n"
            "  ─────────────────────\n"
            "  رقم البطاقة\n"
            "  MM/YY\n"
            "  CVV\n"
            "  اسم صاحب البطاقة\n"
            "\n"
            "  مثال:\n"
            "  4111111111111111\n"
            "  12/26\n"
            "  123\n"
            "  Ahmed Ali\n"
        )
        return

    if step == "card":
        card_data = _parse_card(text)
        if not card_data:
            await update.message.reply_text(
                "  ❌ بيانات البطاقة غير صحيحة\n"
                "\n"
                "  ارسلها بهذا التنسيق:\n"
                "  رقم البطاقة\n"
                "  MM/YY\n"
                "  CVV\n"
                "  اسم صاحب البطاقة\n"
            )
            return

        _pending_payment.pop(user_id, None)

        masked = f"****{card_data.number[-4:]}"
        await update.message.reply_text(
            "  ✅  بيانات البطاقة مقبولة\n"
            f"  💳  {masked}\n"
            "\n"
            "  جاري بدء عملية التفعيل...\n"
        )

        await _start_payment_job(
            update,
            site_url=payment["site_url"],
            email=payment["email"],
            password=payment["password"],
            card=card_data,
        )
        return


def _parse_card(text: str):
    from app.storage.models import CardInfo

    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]

    if len(lines) >= 4:
        card_number = re.sub(r"[\s\-]", "", lines[0])
        expiry = lines[1].strip()
        cvv = lines[2].strip()
        holder = " ".join(lines[3:]).strip()
    else:
        parts = text.split()
        if len(parts) < 4:
            return None
        card_number = re.sub(r"[\s\-]", "", parts[0])
        expiry = parts[1]
        cvv = parts[2]
        holder = " ".join(parts[3:]).strip()

    if not re.match(r"^\d{13,19}$", card_number):
        return None

    exp_match = re.match(r"^(\d{1,2})/(\d{2,4})$", expiry)
    if not exp_match:
        return None

    month = exp_match.group(1).zfill(2)
    year = exp_match.group(2)
    if len(year) == 4:
        year = year[2:]

    if not (1 <= int(month) <= 12):
        return None

    if not re.match(r"^\d{3,4}$", cvv):
        return None

    if not holder:
        return None

    return CardInfo(
        number=card_number,
        expiry_month=month,
        expiry_year=year,
        cvv=cvv,
        holder_name=holder,
    )


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


async def _start_payment_job(update: Update, site_url: str, email: str, password: str, card):
    from app.storage.models import PaymentJob

    site_url_full = normalise_url(site_url)
    job_id = new_job_id()

    pjob = PaymentJob(
        job_id=job_id,
        site_url=site_url_full,
        email=email,
        password=password,
        chat_id=update.effective_chat.id,
    )

    log.info("Payment job created: id=%s email=%s site=%s", job_id, email, site_url_full)

    from app.jobs.scheduler import scheduler
    scheduler.submit_payment(pjob, card)
    log.info("Payment job submitted to scheduler: %s", job_id)


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
