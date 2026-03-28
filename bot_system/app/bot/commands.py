from __future__ import annotations

import re
import threading
import traceback
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.core.utils import is_valid_email, normalise_url, new_job_id

log = get_logger(__name__)

_session_lock = threading.Lock()


def _save_session(user_id: int, data: dict) -> None:
    try:
        from app.storage.repositories import PendingSessionRepository
        PendingSessionRepository().save(user_id, data)
    except Exception as e:
        log.warning("Could not save session for user %s: %s", user_id, e)


def _del_session(user_id: int) -> None:
    try:
        from app.storage.repositories import PendingSessionRepository
        PendingSessionRepository().delete(user_id)
    except Exception as e:
        log.warning("Could not delete session for user %s: %s", user_id, e)


def load_all_sessions() -> None:
    """Called at startup — restores pending sessions from DB into _pending_payment."""
    try:
        from app.storage.repositories import PendingSessionRepository
        sessions = PendingSessionRepository().load_all()
        for uid, data in sessions.items():
            _pending_payment[int(uid)] = data
        if sessions:
            log.info("Restored %d pending sessions from DB", len(sessions))
    except Exception as e:
        log.warning("Could not restore sessions: %s", e)


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

SITE_PLANS = {
    "chatgpt.com": [
        {"label": "Plus — $20/شهر", "value": "plus"},
        {"label": "Team — $25/شهر", "value": "team"},
    ],
    "canva.com": [
        {"label": "Pro — $15/شهر", "value": "pro"},
        {"label": "Teams — $10/شهر", "value": "teams"},
    ],
    "protonvpn.com": [
        {"label": "Plus — $10/شهر", "value": "plus"},
        {"label": "Unlimited — $12/شهر", "value": "unlimited"},
    ],
    "pixlr.com": [
        {"label": "Plus — $5/شهر", "value": "plus"},
        {"label": "Premium — $13/شهر", "value": "premium"},
    ],
    "replit.com": [
        {"label": "Core — $15/شهر", "value": "core"},
        {"label": "Teams — $20/شهر", "value": "teams"},
    ],
}

BILLING_COUNTRIES = [
    ("🇸🇦 SA", "SA"), ("🇺🇸 US", "US"), ("🇬🇧 GB", "GB"),
    ("🇦🇪 AE", "AE"), ("🇩🇪 DE", "DE"), ("🇫🇷 FR", "FR"),
]

_pending_site = {}
_pending_payment = {}
_pending_proxy = {}


def _build_proxy_menu(proxies: list) -> InlineKeyboardMarkup:
    keyboard = []
    for p in proxies:
        status = "✅" if p.active else "⏸"
        short = p.label or (p.proxy_url[:35] + "…" if len(p.proxy_url) > 35 else p.proxy_url)
        keyboard.append([
            InlineKeyboardButton(f"{status} {short}", callback_data=f"proxy_toggle:{p.id}"),
            InlineKeyboardButton("🗑", callback_data=f"proxy_del:{p.id}"),
        ])
    keyboard.append([InlineKeyboardButton("➕ إضافة بروكسي", callback_data="proxy_add")])
    keyboard.append([InlineKeyboardButton("◀ رجوع", callback_data="back:home")])
    return InlineKeyboardMarkup(keyboard)


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
        [InlineKeyboardButton("📋  حساباتي", callback_data="menu:accounts")],
        [InlineKeyboardButton("🌐  البروكسيات", callback_data="menu:proxies")],
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


def _build_plan_menu(site_url: str, back_data: str = "back:paysites"):
    plans = SITE_PLANS.get(site_url, [])
    keyboard = []
    for plan in plans:
        keyboard.append([InlineKeyboardButton(plan["label"], callback_data=f"plan:{plan['value']}")])
    keyboard.append([InlineKeyboardButton("📋 خطة أخرى (أدخل يدوياً)", callback_data="plan:custom")])
    keyboard.append([InlineKeyboardButton("◀ رجوع", callback_data=back_data)])
    return InlineKeyboardMarkup(keyboard)


def _build_country_menu():
    keyboard = []
    row = []
    for label, code in BILLING_COUNTRIES:
        row.append(InlineKeyboardButton(label, callback_data=f"country:{code}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def _home_text():
    return (
        "╔══════════════════════════════╗\n"
        "║       STORED DX BOT         ║\n"
        "╠══════════════════════════════╣\n"
        "║                              ║\n"
        "║   📝  إنشاء حساب جديد        ║\n"
        "║   💳  تفعيل حساب (اشتراك)    ║\n"
        "║   📋  حساباتي المحفوظة       ║\n"
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
            "  /cancel - إلغاء العمليات\n"
            "  /accounts - حساباتي المحفوظة\n"
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


async def cmd_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.info("cmd_proxies called by user=%s", user.id)
    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("غير مصرح لك.")
            return
        from app.storage.repositories import ProxyRepository
        proxies = ProxyRepository().list_all()
        count_active = sum(1 for p in proxies if p.active)
        await update.message.reply_text(
            "╔══════════════════════════════╗\n"
            "║   🌐  إدارة البروكسيات        ║\n"
            "╚══════════════════════════════╝\n"
            f"  إجمالي: {len(proxies)}  |  فعّال: {count_active}\n"
            "\n"
            "  ✅ = فعّال   ⏸ = معطّل   🗑 = حذف\n",
            reply_markup=_build_proxy_menu(proxies),
        )
    except Exception as exc:
        log.error("cmd_proxies error: %s\n%s", exc, traceback.format_exc())


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

    if data == "menu:accounts":
        _pending_site.pop(user_id, None)
        _pending_payment.pop(user_id, None)
        await _show_accounts(query)
        return

    if data == "menu:proxies":
        _pending_site.pop(user_id, None)
        _pending_payment.pop(user_id, None)
        from app.storage.repositories import ProxyRepository
        proxies = ProxyRepository().list_all()
        count_active = sum(1 for p in proxies if p.active)
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║   🌐  إدارة البروكسيات        ║\n"
            "╚══════════════════════════════╝\n"
            f"  إجمالي: {len(proxies)}  |  فعّال: {count_active}\n"
            "\n"
            "  ✅ = فعّال   ⏸ = معطّل   🗑 = حذف\n",
            reply_markup=_build_proxy_menu(proxies),
        )
        return

    if data == "proxy_add":
        _pending_proxy[user_id] = {"step": "url"}
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║   🌐  إضافة بروكسي            ║\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  التنسيقات المدعومة:\n"
            "\n"
            "  host:port:user:pass\n"
            "  px.server.com:10780:user:pass\n"
            "\n"
            "  socks5://user:pass@host:port\n"
            "  http://user:pass@host:port\n"
            "  http://host:port\n"
            "\n"
            "  اختياري — أضف تسمية بعد |\n"
            "  host:port:user:pass | اسم\n",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀ رجوع", callback_data="menu:proxies")]]),
        )
        return

    if data.startswith("proxy_del:"):
        proxy_id = int(data[10:])
        from app.storage.repositories import ProxyRepository
        repo = ProxyRepository()
        deleted = repo.delete(proxy_id)
        proxies = repo.list_all()
        count_active = sum(1 for p in proxies if p.active)
        msg = "  ✅ تم الحذف\n\n" if deleted else "  ❌ لم يُحذف\n\n"
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║   🌐  إدارة البروكسيات        ║\n"
            "╚══════════════════════════════╝\n"
            f"{msg}"
            f"  إجمالي: {len(proxies)}  |  فعّال: {count_active}\n"
            "\n"
            "  ✅ = فعّال   ⏸ = معطّل   🗑 = حذف\n",
            reply_markup=_build_proxy_menu(proxies),
        )
        return

    if data.startswith("proxy_toggle:"):
        proxy_id = int(data[13:])
        from app.storage.repositories import ProxyRepository
        repo = ProxyRepository()
        all_p = repo.list_all()
        target = next((p for p in all_p if p.id == proxy_id), None)
        if target:
            new_state = not target.active
            repo.set_active(proxy_id, new_state)
        proxies = repo.list_all()
        count_active = sum(1 for p in proxies if p.active)
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║   🌐  إدارة البروكسيات        ║\n"
            "╚══════════════════════════════╝\n"
            f"  إجمالي: {len(proxies)}  |  فعّال: {count_active}\n"
            "\n"
            "  ✅ = فعّال   ⏸ = معطّل   🗑 = حذف\n",
            reply_markup=_build_proxy_menu(proxies),
        )
        return

    if data.startswith("retry_reg:"):
        site_url = data[10:]
        _pending_site[user_id] = site_url
        site_label = site_url
        for ps in PRESET_SITES:
            if ps["url"] == site_url:
                site_label = f"{ps['icon']} {ps['label']}"
                break
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:regsites")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  🔄  إعادة - {site_label}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📧 ارسل الايميل:\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("retry_pay:"):
        site_url = data[10:]
        site_label = site_url
        for ps in PAYMENT_SITES:
            if ps["url"] == site_url:
                site_label = f"{ps['icon']} {ps['label']}"
                break
        _pending_payment[user_id] = {"step": "plan", "site_url": site_url, "label": site_label}
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  🔄  إعادة - {site_label}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📋 اختر الخطة:\n",
            reply_markup=_build_plan_menu(site_url),
        )
        return

    if data.startswith("pay:"):
        site_val = data[4:]
        site_label = site_val
        for ps in PAYMENT_SITES:
            if ps["url"] == site_val:
                site_label = f"{ps['icon']} {ps['label']}"
                break

        _pending_payment[user_id] = {"step": "plan", "site_url": site_val, "label": site_label}
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  💳  {site_label}\n"
            "╚══════════════════════════════╝\n"
            "\n"
            "  📋 اختر الخطة:\n",
            reply_markup=_build_plan_menu(site_val),
        )
        return

    if data.startswith("plan:"):
        payment = _pending_payment.get(user_id)
        if not payment:
            await query.edit_message_text("انتهت الجلسة، ابدأ من جديد /start")
            return
        plan_val = data[5:]
        if plan_val == "custom":
            payment["step"] = "plan_custom"
            keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:paysites")]]
            await query.edit_message_text(
                "╔══════════════════════════════╗\n"
                "║  📋  اسم الخطة               ║\n"
                "╚══════════════════════════════╝\n"
                "\n"
                "  ارسل اسم الخطة (مثال: pro، plus، premium):\n",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        payment["plan"] = plan_val
        payment["step"] = "email"
        _save_session(user_id, payment)
        site_label = payment.get("label", payment.get("site_url", ""))
        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:paysites")]]
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            f"║  💳  {site_label}\n"
            "╚══════════════════════════════╝\n"
            f"  ✅  الخطة: {plan_val}\n"
            "\n"
            "  📧 ارسل الايميل (حساب الموقع):\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("country:"):
        payment = _pending_payment.get(user_id)
        if not payment or payment.get("step") != "country":
            await query.edit_message_text("انتهت الجلسة، ابدأ من جديد /start")
            return
        country_code = data[8:]
        payment["billing_country"] = country_code
        payment["step"] = "billing_zip"
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║  💳  عنوان الفاتورة           ║\n"
            "╚══════════════════════════════╝\n"
            f"  ✅  الدولة: {country_code}\n"
            "\n"
            "  📮 ارسل الرمز البريدي (ZIP):\n"
            "  مثال: 12345\n"
        )
        return


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not _is_allowed(user.id):
        return

    proxy_state = _pending_proxy.get(user.id)
    if proxy_state and proxy_state.get("step") == "url":
        _pending_proxy.pop(user.id, None)
        raw = text.strip()
        label = ""
        if "|" in raw:
            parts = raw.split("|", 1)
            raw = parts[0].strip()
            label = parts[1].strip()
        proxy_url, parse_err = _parse_proxy_url(raw)
        if parse_err:
            await update.message.reply_text(
                f"  ❌ {parse_err}\n"
                "\n"
                "  التنسيقات المدعومة:\n"
                "  host:port:user:pass\n"
                "  socks5://user:pass@host:port\n"
                "  http://host:port\n"
            )
            return
        from app.storage.repositories import ProxyRepository
        repo = ProxyRepository()
        repo.add(proxy_url, label)
        await update.message.reply_text(
            "  ✅ تم إضافة البروكسي\n"
            f"  {label or proxy_url[:60]}\n"
            "\n"
            "  /proxies لإدارة البروكسيات\n"
        )
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
        payment["step"] = "plan"
        _save_session(user_id, payment)
        plans = SITE_PLANS.get(raw, [])
        if plans:
            await update.message.reply_text(
                "╔══════════════════════════════╗\n"
                f"║  💳  {raw}\n"
                "╚══════════════════════════════╝\n"
                "\n"
                "  📋 اختر الخطة:\n",
                reply_markup=_build_plan_menu(raw),
            )
        else:
            payment["step"] = "plan_custom"
            _save_session(user_id, payment)
            await update.message.reply_text(
                "╔══════════════════════════════╗\n"
                f"║  💳  {raw}\n"
                "╚══════════════════════════════╝\n"
                "\n"
                "  📋 ارسل اسم الخطة (مثال: pro, plus, premium)\n"
                "  أو ارسل 0 إذا ما تعرف:\n"
            )
        return

    if step == "plan_custom":
        plan_val = text.strip()
        if plan_val == "0":
            plan_val = ""
        payment["plan"] = plan_val
        payment["step"] = "email"
        _save_session(user_id, payment)
        await update.message.reply_text(
            f"  ✅  الخطة: {plan_val or 'افتراضية'}\n"
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
        _save_session(user_id, payment)
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
        _save_session(user_id, payment)
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
            "\n"
            "  📦 لإرسال عدة بطاقات: افصل بينها بـ ---\n"
        )
        return

    if step == "card":
        if "---" in text or _count_card_blocks(text) > 1:
            cards, errors = _parse_bulk_cards(text)
            if not cards:
                await update.message.reply_text(
                    f"  ❌ لم أتعرف على أي بطاقة صحيحة\n"
                    f"  {errors[0] if errors else ''}\n"
                    "\n"
                    "  تأكد من التنسيق: 4 أسطر لكل بطاقة مفصولة بـ ---\n"
                )
                return
            bad_count = len(errors)
            good_count = len(cards)
            country = payment.get("billing_country", "US")
            _pending_payment.pop(user_id, None)
            _del_session(user_id)
            card_list = "\n".join(
                f"  {'  ' if i > 0 else ''}  {i+1}.  ****{c.number[-4:]}"
                for i, c in enumerate(cards)
            )
            warn_line = f"\n  ⚠️  {bad_count} بطاقة غير صحيحة تم تجاهلها" if bad_count else ""
            await update.message.reply_text(
                "╔══════════════════════════════╗\n"
                f"║   📦  {good_count} بطاقات في قائمة التفعيل\n"
                "╚══════════════════════════════╝\n"
                f"\n{card_list}\n"
                f"{warn_line}\n"
                "\n"
                "  ستصلك نتيجة كل بطاقة بشكل منفصل\n"
            )
            await _run_bulk_payment(
                update,
                site_url=payment["site_url"],
                email=payment["email"],
                password=payment["password"],
                plan_name=payment.get("plan", ""),
                billing_country=country,
                cards=cards,
            )
            return

        card_data, card_error = _parse_card(text)
        if not card_data:
            await update.message.reply_text(
                f"  ❌ {card_error or 'بيانات البطاقة غير صحيحة'}\n"
                "\n"
                "  ارسلها بهذا التنسيق:\n"
                "  رقم البطاقة\n"
                "  MM/YY\n"
                "  CVV\n"
                "  اسم صاحب البطاقة\n"
            )
            return

        payment["card"] = card_data
        payment["step"] = "country"
        masked = f"****{card_data.number[-4:]}"
        await update.message.reply_text(
            f"  ✅  البطاقة مقبولة  {masked}\n"
            "\n"
            "  🌍 اختر دولة الفاتورة:\n",
            reply_markup=_build_country_menu(),
        )
        return

    if step == "billing_zip":
        zip_code = re.sub(r"\s+", "", text.strip())
        if not zip_code or not re.match(r"^[A-Z0-9\-]{3,10}$", zip_code, re.IGNORECASE):
            await update.message.reply_text("  ❌ الرمز البريدي غير صحيح.\n  مثال: 12345 أو SW1A 1AA")
            return

        card_data = payment.get("card")
        if not card_data:
            await update.message.reply_text("حدث خطأ، ابدأ من جديد /start")
            _pending_payment.pop(user_id, None)
            _del_session(user_id)
            return

        card_data.billing_zip = zip_code.upper()
        card_data.billing_country = payment.get("billing_country", "US")
        _pending_payment.pop(user_id, None)
        _del_session(user_id)

        masked = f"****{card_data.number[-4:]}"
        country = card_data.billing_country
        await update.message.reply_text(
            "  ✅  تم تأكيد بيانات الفاتورة\n"
            f"  💳  {masked}\n"
            f"  🌍  {country} — {zip_code.upper()}\n"
            "\n"
            "  جاري بدء عملية التفعيل...\n"
        )

        await _start_payment_job(
            update,
            site_url=payment["site_url"],
            email=payment["email"],
            password=payment["password"],
            card=card_data,
            plan_name=payment.get("plan", ""),
        )
        return


def _parse_proxy_url(raw: str):
    """
    Parse a proxy string into a standard URL.
    Supported formats:
      host:port:user:pass   → http://user:pass@host:port
      host:port             → http://host:port
      scheme://[user:pass@]host:port  → as-is
    Returns (proxy_url, error) tuple.
    """
    raw = raw.strip()
    if re.match(r"^(https?|socks5|socks4)://", raw):
        if not re.search(r":\d+$", raw.rstrip("/")):
            return None, "يجب أن يحتوي الرابط على رقم المنفذ (port)"
        return raw, None
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        if not port.isdigit():
            return None, "رقم المنفذ (port) يجب أن يكون رقماً"
        from urllib.parse import quote
        proxy_url = f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        return proxy_url, None
    if len(parts) == 2:
        host, port = parts
        if not port.isdigit():
            return None, "رقم المنفذ (port) يجب أن يكون رقماً"
        return f"http://{host}:{port}", None
    return None, "التنسيق غير صحيح — استخدم: host:port:user:pass أو socks5://user:pass@host:port"


_FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph",
    "Thomas", "Charles", "Emily", "Emma", "Olivia", "Sophia", "Isabella", "Ava",
    "Mia", "Charlotte", "Amelia", "Harper", "Lucas", "Liam", "Noah", "Ethan",
    "Mason", "Logan", "Oliver", "Aiden", "Carter", "Jackson", "Daniel", "Matthew",
    "Henry", "Alexander", "Benjamin", "Grace", "Lily", "Chloe", "Victoria", "Natalie",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Wilson", "Anderson", "Taylor", "Thomas", "Jackson", "White", "Harris",
    "Martin", "Thompson", "Moore", "Young", "Allen", "Walker", "Hall", "King",
    "Wright", "Scott", "Green", "Adams", "Baker", "Nelson", "Carter",
]


def _random_holder() -> str:
    import random
    return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"


_PIPE_RE = re.compile(r"^(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})\s*[\s✅❌✔✗\u2714\u2716]*$")


def _is_pipe_line(line: str) -> bool:
    return bool(_PIPE_RE.match(line.strip()))


def _count_card_blocks(text: str) -> int:
    """
    Count card entries — supports:
      - pipe format: one card per line (number|MM|YYYY|cvv)
      - 4-line blocks separated by blank lines or ---
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    pipe_count = sum(1 for l in lines if _is_pipe_line(l))
    if pipe_count >= 1:
        return pipe_count
    blocks = re.split(r"\n\s*\n|-{2,}", text.strip())
    count = 0
    for b in blocks:
        ls = [l.strip() for l in b.strip().split("\n") if l.strip()]
        if len(ls) >= 4:
            count += 1
    return count


def _parse_bulk_cards(text: str):
    """
    Parse multiple cards from text.
    Supports:
      - Pipe format (one per line): number|MM|YYYY|cvv  [✅ optional]
      - 4-line blocks separated by --- or blank lines
    Returns (valid_cards, error_messages).
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    pipe_count = sum(1 for l in lines if _is_pipe_line(l))

    if pipe_count > 0:
        valid_cards = []
        errors = []
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            card, err = _parse_card(line)
            if card:
                valid_cards.append(card)
            elif err:
                errors.append(f"سطر {i}: {err}")
        return valid_cards, errors

    if "---" in text:
        raw_blocks = re.split(r"-{2,}", text)
    else:
        raw_blocks = re.split(r"\n\s*\n", text)

    valid_cards = []
    errors = []
    for i, block in enumerate(raw_blocks, 1):
        block = block.strip()
        if not block:
            continue
        card, err = _parse_card(block)
        if card:
            valid_cards.append(card)
        else:
            errors.append(f"بطاقة {i}: {err}")
    return valid_cards, errors


async def _run_bulk_payment(
    update,
    site_url: str,
    email: str,
    password: str,
    plan_name: str,
    billing_country: str,
    cards: list,
) -> None:
    """
    Submit cards as individual payment jobs one-by-one with adaptive delays.
    Sequential throttling prevents rate-limiting and proxy flagging.
    Pauses automatically after 3 consecutive failures.
    """
    from app.storage.models import PaymentJob, CardInfo
    from app.jobs.scheduler import scheduler
    from app.services.notification_service import NotificationService
    from app.core.throttler import bulk_throttler
    import asyncio
    import threading

    total = len(cards)
    chat_id = update.effective_chat.id
    notify = NotificationService()

    # Reset throttler state for a new bulk session
    bulk_throttler.reset_failures()

    for idx, card in enumerate(cards, 1):
        masked = f"****{card.number[-4:]}"
        card.billing_country = billing_country
        card.billing_zip = card.billing_zip or ""

        # Check if throttler has detected consecutive failures → pause
        if bulk_throttler.should_pause:
            pause_msg = (
                f"⏸ *توقف مؤقت* — تم رصد {3}+ فشل متتالي.\n"
                f"سيُستأنف الجدول تلقائياً.\n"
                f"البطاقة {idx}/{total}: `{masked}`"
            )
            await update.effective_message.reply_text(
                pause_msg, parse_mode="Markdown"
            )
            await asyncio.sleep(120)
            bulk_throttler.reset_failures()

        job_id = new_job_id()
        pjob = PaymentJob(
            job_id=job_id,
            site_url=normalise_url(site_url),
            email=email,
            password=password,
            plan_name=plan_name,
            chat_id=chat_id,
            is_bulk=True,
            card_last4=card.number[-4:],
        )

        scheduler.submit_payment(pjob, card)
        log.info("Bulk job %s submitted: card %d/%d masked=%s", job_id, idx, total, masked)

        # Don't wait after the last card
        if idx < total:
            await bulk_throttler.wait()


def _parse_card(text: str):
    from app.storage.models import CardInfo
    from datetime import date

    raw = text.strip()
    raw = re.sub(r"[\s✅❌✔✗\u2714\u2716]+$", "", raw).strip()

    pipe_m = _PIPE_RE.match(raw)
    if pipe_m:
        card_number = pipe_m.group(1)
        month = pipe_m.group(2).zfill(2)
        year_raw = pipe_m.group(3)
        cvv = pipe_m.group(4)
        holder = _random_holder()
        year = year_raw[2:] if len(year_raw) == 4 else year_raw.zfill(2)

        if not (1 <= int(month) <= 12):
            return None, "الشهر غير صحيح"
        today = date.today()
        full_year = 2000 + int(year)
        if full_year < today.year or (full_year == today.year and int(month) < today.month):
            return None, "البطاقة منتهية الصلاحية"

        return CardInfo(
            number=card_number,
            expiry_month=month,
            expiry_year=year,
            cvv=cvv,
            holder_name=holder,
        ), None

    lines = [l.strip() for l in raw.split("\n") if l.strip()]

    if len(lines) >= 4:
        card_number = re.sub(r"[\s\-]", "", lines[0])
        expiry = lines[1].strip()
        cvv = lines[2].strip()
        holder = " ".join(lines[3:]).strip()
    else:
        parts = raw.split()
        if len(parts) < 4:
            return None, "أرسل 4 أسطر: رقم البطاقة، تاريخ الانتهاء، CVV، الاسم"
        card_number = re.sub(r"[\s\-]", "", parts[0])
        expiry = parts[1]
        cvv = parts[2]
        holder = " ".join(parts[3:]).strip()

    if not re.match(r"^\d{13,19}$", card_number):
        return None, "رقم البطاقة غير صحيح (يجب أن يكون 13-19 رقم)"

    exp_match = re.match(r"^(\d{1,2})/(\d{2,4})$", expiry)
    if not exp_match:
        return None, "تاريخ الانتهاء غير صحيح (استخدم MM/YY مثال: 12/26)"

    month = exp_match.group(1).zfill(2)
    year = exp_match.group(2)
    if len(year) == 4:
        year = year[2:]

    if not (1 <= int(month) <= 12):
        return None, "الشهر غير صحيح (يجب أن يكون بين 01 و 12)"

    today = date.today()
    full_year = 2000 + int(year)
    if full_year < today.year or (full_year == today.year and int(month) < today.month):
        return None, "البطاقة منتهية الصلاحية"

    if not re.match(r"^\d{3,4}$", cvv):
        return None, "CVV غير صحيح (3 أو 4 أرقام)"

    if not holder:
        return None, "اسم صاحب البطاقة مفقود"

    return CardInfo(
        number=card_number,
        expiry_month=month,
        expiry_year=year,
        cvv=cvv,
        holder_name=holder,
    ), None


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
    from app.jobs.scheduler import scheduler

    if scheduler.is_at_limit(update.effective_chat.id):
        await update.message.reply_text(
            f"  ⚠️  عندك عمليات جارية (الحد الاقصى {config.MAX_CONCURRENT_JOBS})\n"
            "  انتظر حتى تنتهي او استخدم /cancel\n"
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

    scheduler.submit(job, config.FIXED_PASSWORD)
    log.info("Job submitted to scheduler: %s", job.job_id)


async def _start_payment_job(update: Update, site_url: str, email: str, password: str, card, plan_name: str = ""):
    from app.jobs.scheduler import scheduler

    if scheduler.is_at_limit(update.effective_chat.id):
        await update.message.reply_text(
            f"  ⚠️  عندك عمليات جارية (الحد الاقصى {config.MAX_CONCURRENT_JOBS})\n"
            "  انتظر حتى تنتهي او استخدم /cancel\n"
        )
        return

    from app.storage.models import PaymentJob

    site_url_full = normalise_url(site_url)
    job_id = new_job_id()

    pjob = PaymentJob(
        job_id=job_id,
        site_url=site_url_full,
        email=email,
        password=password,
        plan_name=plan_name,
        chat_id=update.effective_chat.id,
    )

    log.info("Payment job created: id=%s email=%s site=%s plan=%s", job_id, email, site_url_full, plan_name)

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


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("غير مصرح.")
            return

        _pending_site.pop(user.id, None)
        _pending_payment.pop(user.id, None)

        from app.jobs.scheduler import scheduler
        active = scheduler.get_active_jobs_for_chat(update.effective_chat.id)

        if not active:
            await update.message.reply_text("  لا توجد عمليات جارية للإلغاء.")
            return

        cancelled = 0
        for job_id in active:
            if scheduler.cancel(job_id):
                cancelled += 1

        if cancelled > 0:
            await update.message.reply_text(
                f"  ✅  تم إلغاء {cancelled} عملية جارية.\n"
            )
        else:
            await update.message.reply_text("  لم أتمكن من إلغاء أي عملية.")

    except Exception as exc:
        log.error("cmd_cancel error: %s\n%s", exc, traceback.format_exc())


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    try:
        if not _is_allowed(user.id):
            await update.message.reply_text("غير مصرح.")
            return

        from app.storage.repositories import SavedAccountRepository
        repo = SavedAccountRepository()
        accounts = repo.list_by_chat(update.effective_chat.id, limit=20)

        if not accounts:
            await update.message.reply_text("  لا توجد حسابات محفوظة بعد.")
            return

        lines = [
            "╔══════════════════════════════╗",
            "║       حساباتي المحفوظة       ║",
            "╚══════════════════════════════╝",
            "",
        ]

        for acc in accounts:
            icon = "📝" if acc.job_type == "registration" else "💳"
            lines.append(f"  {icon}  {acc.site_url}")
            lines.append(f"      📧 {acc.email}")
            if acc.password:
                lines.append(f"      🔑 {acc.password}")
            if acc.plan_name:
                lines.append(f"      📋 {acc.plan_name}")
            lines.append(f"      📅 {acc.created_at.strftime('%Y-%m-%d %H:%M')}")
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    except Exception as exc:
        log.error("cmd_accounts error: %s\n%s", exc, traceback.format_exc())


async def _show_accounts(query) -> None:
    try:
        from app.storage.repositories import SavedAccountRepository
        repo = SavedAccountRepository()
        accounts = repo.list_by_chat(query.from_user.id, limit=20)

        keyboard = [[InlineKeyboardButton("◀ رجوع", callback_data="back:home")]]

        if not accounts:
            await query.edit_message_text(
                "╔══════════════════════════════╗\n"
                "║       حساباتي المحفوظة       ║\n"
                "╚══════════════════════════════╝\n"
                "\n"
                "  لا توجد حسابات محفوظة بعد.\n"
                "  سيتم حفظ الحسابات تلقائيا عند النجاح.\n",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        lines = [
            "╔══════════════════════════════╗",
            "║       حساباتي المحفوظة       ║",
            "╚══════════════════════════════╝",
            "",
        ]

        for acc in accounts:
            icon = "📝" if acc.job_type == "registration" else "💳"
            lines.append(f"  {icon}  {acc.site_url}")
            lines.append(f"      📧 {acc.email}")
            if acc.password:
                lines.append(f"      🔑 {acc.password}")
            if acc.plan_name:
                lines.append(f"      📋 {acc.plan_name}")
            lines.append(f"      📅 {acc.created_at.strftime('%Y-%m-%d %H:%M')}")
            lines.append("")

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as exc:
        log.error("_show_accounts error: %s\n%s", exc, traceback.format_exc())
