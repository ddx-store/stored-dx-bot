"""
BIN Intelligence Engine — تتبّع معدلات نجاح البطاقات حسب BIN ونطاق الموقع.
يُرتّب قائمة البطاقات الجماعية من الأعلى احتمالاً إلى الأدنى.
يستخدم Bayesian prior للـ BINs غير المجرّبة (50% افتراضي).
"""
from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, List, Optional, Tuple

from app.core.logger import get_logger
from app.storage.db import get_connection

if TYPE_CHECKING:
    from app.storage.models import CardInfo

log = get_logger(__name__)

_BAYESIAN_PRIOR_ALPHA = 1.0  # نجاح افتراضي
_BAYESIAN_PRIOR_BETA = 1.0   # فشل افتراضي


def _ensure_table() -> None:
    try:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bin_stats (
                bin6    TEXT NOT NULL,
                domain  TEXT NOT NULL,
                wins    INTEGER NOT NULL DEFAULT 0,
                losses  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (bin6, domain)
            )
        """)
        conn.commit()
    except Exception as exc:
        log.warning("bin_stats table init failed: %s", exc)


class BINIntelligenceEngine:
    """
    محرك ذكاء BIN — يُسجّل ويُحلّل نتائج البطاقات حسب أول 6 أرقام.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _ensure_table()

    def record(self, card_number: str, domain: str, success: bool) -> None:
        """سجّل نتيجة بطاقة واحدة."""
        bin6 = card_number[:6]
        col = "wins" if success else "losses"
        try:
            with self._lock:
                conn = get_connection()
                conn.execute(
                    f"""
                    INSERT INTO bin_stats (bin6, domain, {col})
                    VALUES (?, ?, 1)
                    ON CONFLICT(bin6, domain)
                    DO UPDATE SET {col} = {col} + 1
                    """,
                    (bin6, domain),
                )
                conn.commit()
        except Exception as exc:
            log.warning("BINIntelligence.record failed: %s", exc)

    def success_probability(self, card_number: str, domain: str) -> float:
        """
        احتمال نجاح البطاقة باستخدام Bayesian estimate.
        للـ BINs غير المجرّبة → 0.5 (prior متوازن).
        """
        bin6 = card_number[:6]
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT wins, losses FROM bin_stats WHERE bin6=? AND domain=?",
                (bin6, domain),
            ).fetchone()
            if not row:
                return _BAYESIAN_PRIOR_ALPHA / (_BAYESIAN_PRIOR_ALPHA + _BAYESIAN_PRIOR_BETA)
            wins = row["wins"] + _BAYESIAN_PRIOR_ALPHA
            losses = row["losses"] + _BAYESIAN_PRIOR_BETA
            return wins / (wins + losses)
        except Exception:
            return 0.5

    def rank_cards(self, cards: "List[CardInfo]", domain: str) -> "List[CardInfo]":
        """رتّب قائمة البطاقات من الأعلى احتمالاً إلى الأدنى."""
        ranked = sorted(
            cards,
            key=lambda c: self.success_probability(c.number, domain),
            reverse=True,
        )
        if ranked:
            top = ranked[0]
            log.info(
                "BINIntelligence: sorted %d cards for %s — top BIN=%s prob=%.0f%%",
                len(ranked), domain, top.number[:6],
                self.success_probability(top.number, domain) * 100,
            )
        return ranked

    def report(self, domain: str) -> str:
        """تقرير بأفضل وأسوأ BIN ranges لموقع معيّن."""
        try:
            conn = get_connection()
            rows = conn.execute(
                """
                SELECT bin6,
                       wins,
                       losses,
                       CAST(wins + 0.5 AS REAL) / (wins + losses + 1.0) AS rate
                FROM bin_stats
                WHERE domain = ?
                ORDER BY rate DESC
                LIMIT 20
                """,
                (domain,),
            ).fetchall()
            if not rows:
                return f"لا توجد بيانات لـ {domain} بعد."
            lines = [f"📊 *إحصاءات BIN لـ {domain}:*\n"]
            for r in rows:
                icon = "✅" if r["rate"] >= 0.5 else "❌"
                lines.append(
                    f"{icon} `{r['bin6']}` — نجاح: {r['wins']} | فشل: {r['losses']} | معدل: {r['rate']*100:.0f}%"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"خطأ في تقرير BIN: {exc}"

    def top_bins(self, domain: str, n: int = 5) -> List[Tuple[str, float]]:
        """أفضل N BINs لموقع معيّن."""
        try:
            conn = get_connection()
            rows = conn.execute(
                """
                SELECT bin6,
                       CAST(wins + 0.5 AS REAL) / (wins + losses + 1.0) AS rate
                FROM bin_stats
                WHERE domain = ? AND (wins + losses) >= 2
                ORDER BY rate DESC
                LIMIT ?
                """,
                (domain, n),
            ).fetchall()
            return [(r["bin6"], r["rate"]) for r in rows]
        except Exception:
            return []


bin_intelligence = BINIntelligenceEngine()
