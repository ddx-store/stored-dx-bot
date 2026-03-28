"""
DOMAdaptationEngine — اكتشاف ديناميكي لعناصر النماذج.
عندما يُحدّث موقع واجهته وتفشل الـ selectors الثابتة،
هذه الوحدة تُعيد اكتشاف: حقول الإيميل، كلمة المرور، أزرار التسجيل.

ثلاث استراتيجيات بالتسلسل:
1. Semantic: البحث بالنص/placeholder/aria-label
2. Structural: تحليل موضع العناصر داخل form containers
3. Visual heuristic: أقرب input إلى أعلى الصفحة

النتائج تُخزَّن في قاعدة البيانات لتجنّب إعادة الاكتشاف.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Optional

from app.core.logger import get_logger
from app.storage.db import get_connection

log = get_logger(__name__)

_CACHE_TTL_SECONDS = 21600  # 6 ساعات


@dataclass
class FormSelectors:
    email_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    domain: str = ""
    discovered_at: float = 0.0
    strategy_used: str = ""


def _ensure_table() -> None:
    try:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dom_selectors (
                domain          TEXT PRIMARY KEY,
                selectors_json  TEXT NOT NULL,
                discovered_at   REAL NOT NULL
            )
        """)
        conn.commit()
    except Exception as exc:
        log.warning("dom_selectors table init: %s", exc)


_ensure_table()


class DOMAdaptationEngine:
    """
    اكتشاف تلقائي لعناصر نماذج تسجيل الدخول.
    """

    _EMAIL_HINTS = ["email", "e-mail", "mail", "username", "user", "login", "البريد", "الإيميل"]
    _PASSWORD_HINTS = ["password", "pass", "passwd", "كلمة المرور", "secret"]
    _SUBMIT_HINTS = [
        "sign in", "log in", "login", "continue", "next", "submit",
        "تسجيل الدخول", "متابعة", "دخول",
    ]

    def load_cached(self, domain: str) -> Optional[FormSelectors]:
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT selectors_json, discovered_at FROM dom_selectors WHERE domain=?",
                (domain,),
            ).fetchone()
            if not row:
                return None
            if time.time() - row["discovered_at"] > _CACHE_TTL_SECONDS:
                conn.execute("DELETE FROM dom_selectors WHERE domain=?", (domain,))
                conn.commit()
                return None
            data = json.loads(row["selectors_json"])
            return FormSelectors(**data)
        except Exception:
            return None

    def save_cache(self, fs: FormSelectors) -> None:
        try:
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO dom_selectors (domain, selectors_json, discovered_at)
                VALUES (?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    selectors_json = excluded.selectors_json,
                    discovered_at  = excluded.discovered_at
                """,
                (fs.domain, json.dumps(asdict(fs)), time.time()),
            )
            conn.commit()
        except Exception as exc:
            log.warning("dom_selectors save: %s", exc)

    async def discover(self, page, domain: str) -> Optional[FormSelectors]:
        """
        اكتشاف selectors نموذج تسجيل الدخول.
        يُجرّب الـ cache أولاً، ثم الاكتشاف التلقائي.
        """
        cached = self.load_cached(domain)
        if cached:
            if await self._verify_selectors(page, cached):
                log.debug("DOMAdapter: cache HIT for %s", domain)
                return cached
            log.info("DOMAdapter: cache MISS (DOM changed) for %s", domain)

        for strategy_name, strategy_fn in [
            ("semantic", self._semantic_discover),
            ("structural", self._structural_discover),
            ("positional", self._positional_discover),
        ]:
            try:
                fs = await strategy_fn(page, domain)
                if fs and fs.email_selector:
                    fs.strategy_used = strategy_name
                    fs.discovered_at = time.time()
                    self.save_cache(fs)
                    log.info("DOMAdapter: discovered via '%s' for %s", strategy_name, domain)
                    return fs
            except Exception as exc:
                log.debug("DOMAdapter: strategy '%s' failed: %s", strategy_name, exc)

        log.warning("DOMAdapter: all strategies failed for %s", domain)
        return None

    async def _verify_selectors(self, page, fs: FormSelectors) -> bool:
        """تحقق أن الـ selectors المخزنة لا تزال صالحة."""
        try:
            if fs.email_selector:
                count = await page.locator(fs.email_selector).count()
                return count > 0
        except Exception:
            pass
        return False

    async def _semantic_discover(self, page, domain: str) -> Optional[FormSelectors]:
        """استراتيجية دلالية: بحث عبر type/placeholder/aria-label/name."""
        email_sel = await self._find_input_by_hints(page, self._EMAIL_HINTS, input_type="email")
        if not email_sel:
            email_sel = await self._find_input_by_type(page, "email")
        password_sel = await self._find_input_by_type(page, "password")
        submit_sel = await self._find_button_by_hints(page, self._SUBMIT_HINTS)

        if not email_sel:
            return None
        return FormSelectors(
            email_selector=email_sel,
            password_selector=password_sel or 'input[type="password"]',
            submit_selector=submit_sel or 'button[type="submit"]',
            domain=domain,
        )

    async def _structural_discover(self, page, domain: str) -> Optional[FormSelectors]:
        """استراتيجية هيكلية: البحث داخل form containers."""
        result = await page.evaluate("""
        () => {
            const forms = document.querySelectorAll('form, [role="form"], [data-testid*="form"]');
            for (const form of forms) {
                const inputs = form.querySelectorAll('input:not([type="hidden"])');
                const emailInput = Array.from(inputs).find(i =>
                    ['email', 'text'].includes(i.type) &&
                    (i.name || i.id || i.placeholder || '').toLowerCase().includes('email')
                );
                const passInput = Array.from(inputs).find(i => i.type === 'password');
                const btn = form.querySelector('button[type="submit"], button:last-of-type, input[type="submit"]');
                if (emailInput) {
                    return {
                        email: emailInput.id ? '#' + emailInput.id : (emailInput.name ? '[name="' + emailInput.name + '"]' : null),
                        password: passInput ? (passInput.id ? '#' + passInput.id : '[type="password"]') : '[type="password"]',
                        submit: btn ? (btn.id ? '#' + btn.id : btn.type === 'submit' ? 'button[type="submit"]' : null) : null,
                    };
                }
            }
            return null;
        }
        """)
        if not result or not result.get("email"):
            return None
        return FormSelectors(
            email_selector=result["email"],
            password_selector=result.get("password") or 'input[type="password"]',
            submit_selector=result.get("submit") or 'button[type="submit"]',
            domain=domain,
        )

    async def _positional_discover(self, page, domain: str) -> Optional[FormSelectors]:
        """استراتيجية موضعية: أقرب input مرئي إلى أعلى الصفحة."""
        result = await page.evaluate("""
        () => {
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"])'))
                .filter(i => {
                    const r = i.getBoundingClientRect();
                    return r.width > 50 && r.height > 10 && r.top >= 0;
                })
                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);

            const emailIn = inputs.find(i => i.type === 'email' || i.type === 'text');
            const passIn  = inputs.find(i => i.type === 'password');

            const toSel = (el) => {
                if (!el) return null;
                if (el.id) return '#' + el.id;
                if (el.name) return '[name="' + el.name + '"]';
                return el.type ? '[type="' + el.type + '"]' : null;
            };

            return emailIn ? { email: toSel(emailIn), password: toSel(passIn) } : null;
        }
        """)
        if not result or not result.get("email"):
            return None
        return FormSelectors(
            email_selector=result["email"],
            password_selector=result.get("password") or 'input[type="password"]',
            submit_selector='button[type="submit"]',
            domain=domain,
        )

    async def _find_input_by_hints(self, page, hints: list, input_type: str = "") -> Optional[str]:
        for hint in hints:
            for attr in ["placeholder", "name", "id", "aria-label"]:
                sel = f'input[{attr}*="{hint}" i]'
                if input_type:
                    sel += f', input[type="{input_type}"]'
                try:
                    if await page.locator(sel).first.is_visible(timeout=300):
                        return sel.split(",")[0].strip()
                except Exception:
                    continue
        return None

    async def _find_input_by_type(self, page, input_type: str) -> Optional[str]:
        sel = f'input[type="{input_type}"]'
        try:
            if await page.locator(sel).first.is_visible(timeout=300):
                return sel
        except Exception:
            pass
        return None

    async def _find_button_by_hints(self, page, hints: list) -> Optional[str]:
        for hint in hints:
            sel = f'button:has-text("{hint}")'
            try:
                if await page.locator(sel).first.is_visible(timeout=300):
                    return sel
            except Exception:
                continue
        return 'button[type="submit"]'


dom_adapter = DOMAdaptationEngine()
