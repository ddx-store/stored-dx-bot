from __future__ import annotations

import threading
import time
from typing import Dict, Optional, List, Tuple

from app.core.logger import get_logger
from app.storage.models import Job

log = get_logger(__name__)

_PROGRESS_STEPS = [
    "فتح الموقع",
    "البحث عن التسجيل",
    "تعبئة البيانات",
    "إرسال النموذج",
    "التحقق من البريد",
    "إكمال الملف",
]

_PAYMENT_STEPS = [
    "فتح الموقع",
    "تسجيل الدخول",
    "صفحة الاشتراك",
    "تعبئة البطاقة",
    "تأكيد الدفع",
    "التحقق من النتيجة",
]

_ANIM_FRAMES = ["◐", "◓", "◑", "◒"]


class JobProgress:
    def __init__(self, job, is_payment: bool = False):
        self.job = job
        self.is_payment = is_payment
        self.message_id: Optional[int] = None
        self.current_step = -1
        self.status_line = ""
        self.is_done = False
        self.is_failed = False
        self.result_text = ""
        self._frame = 0
        self._start_time = time.time()

    def _elapsed(self) -> str:
        secs = int(time.time() - self._start_time)
        m, s = divmod(secs, 60)
        return f"{m:02d}:{s:02d}"

    @property
    def _steps(self) -> List[str]:
        return _PAYMENT_STEPS if self.is_payment else _PROGRESS_STEPS

    def _progress_bar(self) -> str:
        total = len(self._steps)
        if self.is_done:
            done = total
        elif self.current_step < 0:
            done = 0
        else:
            done = self.current_step
        filled = min(done, total)
        bar = "█" * filled + "░" * (total - filled)
        pct = int((filled / total) * 100)
        return f"  [{bar}] {pct}%"

    def _build_text(self) -> str:
        lines = []

        if self.is_payment:
            title = "║   💳 تفعيل حساب (اشتراك)     ║"
        else:
            title = "║   📝  إنشاء حساب جديد        ║"
        lines.append("╔══════════════════════════════╗")
        lines.append(title)
        lines.append("╚══════════════════════════════╝")
        lines.append("")

        lines.append(f"  🌐  {self.job.site_url}")
        lines.append(f"  📧  {self.job.email}")
        lines.append("")

        lines.append(self._progress_bar())
        lines.append("")

        steps = self._steps
        for i, label in enumerate(steps):
            if self.is_done:
                icon = "✅"
            elif self.is_failed and i > self.current_step:
                icon = "▫️"
            elif i < self.current_step:
                icon = "✅"
            elif i == self.current_step:
                if self.is_failed:
                    icon = "❌"
                else:
                    self._frame = (self._frame + 1) % len(_ANIM_FRAMES)
                    icon = _ANIM_FRAMES[self._frame]
            else:
                icon = "▫️"

            lines.append(f"  {icon}  {label}")
            if i < len(steps) - 1:
                if self.is_done or i < self.current_step:
                    lines.append(f"  ┃")
                else:
                    lines.append(f"  ╎")

        lines.append("")
        lines.append(f"  ⏱  {self._elapsed()}")

        if self.is_done:
            lines.append("")
            lines.append("┌─────────────────────────────┐")
            lines.append(f"│  ✅  {self.result_text}")
            lines.append("└─────────────────────────────┘")
        elif self.is_failed:
            lines.append("")
            lines.append("┌─────────────────────────────┐")
            lines.append(f"│  ❌  {self.result_text}")
            lines.append("└─────────────────────────────┘")
        elif self.status_line:
            lines.append(f"  💬  {self.status_line}")

        return "\n".join(lines)

    def send_or_update(self, reply_markup=None):
        from app.bot.telegram_client import send_message, edit_message
        text = self._build_text()
        chat_id = self.job.chat_id
        if not chat_id:
            return

        if self.message_id:
            ok = edit_message(chat_id, self.message_id, text, reply_markup=reply_markup)
            if not ok:
                mid = send_message(chat_id, text, reply_markup=reply_markup)
                if mid:
                    self.message_id = mid
        else:
            mid = send_message(chat_id, text, reply_markup=reply_markup)
            if mid:
                self.message_id = mid


_lock = threading.Lock()
_active: Dict[str, JobProgress] = {}


def _get_progress(job, is_payment: bool = False) -> JobProgress:
    with _lock:
        if job.job_id not in _active:
            _active[job.job_id] = JobProgress(job, is_payment=is_payment)
        return _active[job.job_id]


def _cleanup(job_id: str):
    with _lock:
        _active.pop(job_id, None)


_STEP_KEYWORDS = {
    0: ["فتح", "open", "navigat", "loading"],
    1: ["بحث", "search", "register", "signup", "sign-up", "تسجيل", "نموذج", "form", "صفحة"],
    2: ["fill", "تعبئة", "ملء", "email", "password", "ايميل", "كلمة", "بيانات", "input"],
    3: ["submit", "إرسال", "ارسال", "click", "ضغط", "sent", "API"],
    4: ["otp", "رمز", "تحقق", "verif", "code", "بريد", "mail", "انتظار"],
    5: ["profile", "ملف", "شخصي", "about", "إكمال", "اكمال", "name", "اسم", "birthday", "complete"],
}

_PAYMENT_STEP_KEYWORDS = {
    0: ["فتح", "open", "navigat", "loading"],
    1: ["دخول", "login", "sign in", "تسجيل الدخول"],
    2: ["اشتراك", "upgrade", "subscribe", "pricing", "plan", "صفحة الاشتراك"],
    3: ["بطاقة", "card", "تعبئة", "fill", "stripe", "payment form", "بيانات البطاقة"],
    4: ["تأكيد", "confirm", "pay", "submit", "دفع"],
    5: ["نتيجة", "result", "success", "تحقق", "check", "التحقق من النتيجة"],
}


def _detect_step(msg: str, is_payment: bool = False) -> int:
    msg_lower = msg.lower()
    keywords = _PAYMENT_STEP_KEYWORDS if is_payment else _STEP_KEYWORDS
    for step_idx in sorted(keywords.keys(), reverse=True):
        for kw in keywords[step_idx]:
            if kw in msg_lower:
                return step_idx
    return -1


class NotificationService:
    def step(self, job, icon: str, message: str, is_payment: bool = False) -> None:
        if not job.chat_id:
            return
        if getattr(job, "is_bulk", False):
            return
        try:
            progress = _get_progress(job, is_payment=is_payment)
            detected = _detect_step(message, is_payment=progress.is_payment)
            if detected >= 0 and detected > progress.current_step:
                progress.current_step = detected
            elif progress.current_step < 0:
                progress.current_step = 0

            progress.status_line = message
            progress.send_or_update()
        except Exception as exc:
            log.error("Notification failed for job %s: %s", job.job_id, exc)

    def complete(self, job, message: str) -> None:
        if not job.chat_id:
            return
        try:
            from app.bot.telegram_client import send_message
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            if getattr(job, "is_bulk", False):
                last4 = getattr(job, "card_last4", "")
                card_label = f"****{last4}" if last4 else "البطاقة"
                send_message(job.chat_id, f"  ✅  {card_label}  —  {message}")
                _cleanup(job.job_id)
                return

            progress = _get_progress(job)
            progress.is_done = True
            progress.result_text = message
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="back:home")]
            ])
            progress.send_or_update(reply_markup=keyboard)

            notify_text = (
                "🔔 تم إنشاء الحساب بنجاح!\n"
                "╔══════════════════════════════╗\n"
                f"  🌐  {job.site_url}\n"
                f"  📧  {job.email}\n"
                f"  ✅  {message}\n"
                "╚══════════════════════════════╝"
            )
            send_message(job.chat_id, notify_text)

            _cleanup(job.job_id)
        except Exception as exc:
            log.error("Notification complete failed for job %s: %s", job.job_id, exc)

    def fail(self, job, message: str) -> None:
        if not job.chat_id:
            return
        try:
            from app.bot.telegram_client import send_message
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            if getattr(job, "is_bulk", False):
                last4 = getattr(job, "card_last4", "")
                card_label = f"****{last4}" if last4 else "البطاقة"
                send_message(job.chat_id, f"  ❌  {card_label}  —  {message}")
                _cleanup(job.job_id)
                return

            progress = _get_progress(job)
            is_payment = progress.is_payment
            progress.is_failed = True
            progress.result_text = message

            is_cancel = "إلغاء" in message or "cancel" in message.lower()
            buttons = []
            if not is_cancel:
                if is_payment:
                    site_url = getattr(job, "site_url", "")
                    if site_url:
                        buttons.append([InlineKeyboardButton(
                            "🔄 إعادة المحاولة",
                            callback_data=f"retry_pay:{site_url}"
                        )])
                else:
                    buttons.append([InlineKeyboardButton(
                        "🔄 إعادة المحاولة",
                        callback_data="retry_reg"
                    )])
            buttons.append([InlineKeyboardButton("◀ القائمة الرئيسية", callback_data="back:home")])
            keyboard = InlineKeyboardMarkup(buttons)

            progress.send_or_update(reply_markup=keyboard)
            _cleanup(job.job_id)
        except Exception as exc:
            log.error("Notification fail failed for job %s: %s", job.job_id, exc)
