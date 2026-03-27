from __future__ import annotations

import threading
from typing import Dict, Optional, List, Tuple

from app.core.logger import get_logger
from app.storage.models import Job

log = get_logger(__name__)

_PROGRESS_STEPS = [
    ("فتح الموقع", "🔘"),
    ("البحث عن التسجيل", "🔘"),
    ("تعبئة البيانات", "🔘"),
    ("إرسال النموذج", "🔘"),
    ("التحقق من البريد", "🔘"),
    ("إكمال الملف", "🔘"),
]


class JobProgress:
    def __init__(self, job: Job):
        self.job = job
        self.message_id: Optional[int] = None
        self.steps: List[Tuple[str, str]] = [
            (label, icon) for label, icon in _PROGRESS_STEPS
        ]
        self.current_step = -1
        self.status_line = ""
        self.is_done = False
        self.is_failed = False
        self.result_text = ""

    def _build_text(self) -> str:
        lines = []
        lines.append(f"{'━' * 28}")
        lines.append(f"  الموقع: {self.job.site_url}")
        lines.append(f"  الايميل: {self.job.email}")
        lines.append(f"{'━' * 28}")
        lines.append("")

        for i, (label, _) in enumerate(self.steps):
            if self.is_done:
                icon = "✅"
            elif self.is_failed and i > self.current_step:
                icon = "⬜"
            elif i < self.current_step:
                icon = "✅"
            elif i == self.current_step:
                icon = "⏳" if not self.is_failed else "❌"
            else:
                icon = "⬜"
            lines.append(f"  {icon}  {label}")

        lines.append("")

        if self.is_done:
            lines.append(f"{'━' * 28}")
            lines.append(f"  ✅  {self.result_text}")
            lines.append(f"{'━' * 28}")
        elif self.is_failed:
            lines.append(f"{'━' * 28}")
            lines.append(f"  ❌  {self.result_text}")
            lines.append(f"{'━' * 28}")
        elif self.status_line:
            lines.append(f"  💬  {self.status_line}")

        return "\n".join(lines)

    def send_or_update(self):
        from app.bot.telegram_client import send_message, edit_message
        text = self._build_text()
        chat_id = self.job.chat_id
        if not chat_id:
            return

        if self.message_id:
            ok = edit_message(chat_id, self.message_id, text)
            if not ok:
                mid = send_message(chat_id, text)
                if mid:
                    self.message_id = mid
        else:
            mid = send_message(chat_id, text)
            if mid:
                self.message_id = mid


_lock = threading.Lock()
_active: Dict[str, JobProgress] = {}


def _get_progress(job: Job) -> JobProgress:
    with _lock:
        if job.job_id not in _active:
            _active[job.job_id] = JobProgress(job)
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


def _detect_step(msg: str) -> int:
    msg_lower = msg.lower()
    for step_idx in sorted(_STEP_KEYWORDS.keys(), reverse=True):
        for kw in _STEP_KEYWORDS[step_idx]:
            if kw in msg_lower:
                return step_idx
    return -1


class NotificationService:
    def step(self, job: Job, icon: str, message: str) -> None:
        if not job.chat_id:
            return
        try:
            progress = _get_progress(job)
            detected = _detect_step(message)
            if detected >= 0 and detected > progress.current_step:
                progress.current_step = detected
            elif progress.current_step < 0:
                progress.current_step = 0

            progress.status_line = message
            progress.send_or_update()
        except Exception as exc:
            log.error("Notification failed for job %s: %s", job.job_id, exc)

    def complete(self, job: Job, message: str) -> None:
        if not job.chat_id:
            return
        try:
            progress = _get_progress(job)
            progress.is_done = True
            progress.result_text = message
            progress.send_or_update()
            _cleanup(job.job_id)
        except Exception as exc:
            log.error("Notification complete failed for job %s: %s", job.job_id, exc)

    def fail(self, job: Job, message: str) -> None:
        if not job.chat_id:
            return
        try:
            progress = _get_progress(job)
            progress.is_failed = True
            progress.result_text = message
            progress.send_or_update()
            _cleanup(job.job_id)
        except Exception as exc:
            log.error("Notification fail failed for job %s: %s", job.job_id, exc)
