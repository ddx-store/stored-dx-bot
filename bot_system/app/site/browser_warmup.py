"""
BrowserWarmupEngine — تسخين الـ browser context قبل العمليات الحساسة.
يزور مواقع حيادية عشوائية لبناء:
- Browsing history حقيقي
- Cookies متنوعة من مواقع مختلفة (advertising + analytics)
- Session storage + IndexedDB entries طبيعية

يتجاوز أنظمة مثل Arkose Labs و PerimeterX و DataDome
التي تُحلّل وجود/غياب cookies من مواقع أخرى كمؤشر bot.

يُطبَّق فقط عند غياب session cache (أول استخدام لحساب جديد).
"""
from __future__ import annotations

import asyncio
import random
from typing import List, Tuple

from app.core.logger import get_logger

log = get_logger(__name__)

_WARMUP_SITES: List[Tuple[str, int]] = [
    ("https://www.reddit.com", 12),
    ("https://news.ycombinator.com", 8),
    ("https://github.com/trending", 8),
    ("https://www.wikipedia.org", 6),
    ("https://stackoverflow.com", 7),
    ("https://medium.com", 5),
    ("https://www.bbc.com/news", 6),
    ("https://www.producthunt.com", 5),
]


class BrowserWarmupEngine:
    """
    يُنشئ browsing history طبيعي في الـ browser context قبل العملية الرئيسية.
    المدة الإجمالية: 30-80 ثانية (قابل للضبط).
    """

    def __init__(
        self,
        min_sites: int = 2,
        max_sites: int = 4,
        enabled: bool = True,
    ) -> None:
        self.min_sites = min_sites
        self.max_sites = max_sites
        self.enabled = enabled

    async def warm(self, context) -> None:
        """
        الدالة الرئيسية: تُشغّل warmup في صفحة مؤقتة.
        آمنة تماماً — أي خطأ لا يُوقف العملية الأصلية.
        """
        if not self.enabled:
            return
        try:
            await asyncio.wait_for(self._run(context), timeout=90)
        except asyncio.TimeoutError:
            log.debug("BrowserWarmup: timeout (OK)")
        except Exception as exc:
            log.debug("BrowserWarmup: skipped — %s", exc)

    async def _run(self, context) -> None:
        sites = random.sample(_WARMUP_SITES, k=random.randint(self.min_sites, self.max_sites))
        page = await context.new_page()
        try:
            for url, dwell_seconds in sites:
                try:
                    await page.goto(url, timeout=12000, wait_until="domcontentloaded")
                    await self._simulate_reading(page, dwell_seconds)
                    log.debug("BrowserWarmup: visited %s (%.0fs)", url, dwell_seconds)
                except Exception:
                    continue
                await asyncio.sleep(random.uniform(0.5, 1.5))
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _simulate_reading(self, page, seconds: int) -> None:
        """محاكاة القراءة البشرية: تمرير تدريجي + توقفات عشوائية."""
        elapsed = 0.0
        while elapsed < seconds:
            action = random.random()
            if action < 0.5:
                # تمرير للأسفل
                scroll_amount = random.randint(80, 300)
                await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                delay = random.uniform(0.8, 2.5)
            elif action < 0.75:
                # تمرير للأعلى قليلاً (كأنه يُراجع)
                await page.evaluate(f"window.scrollBy(0, -{random.randint(30, 100)})")
                delay = random.uniform(0.5, 1.2)
            else:
                # توقف (قراءة)
                delay = random.uniform(1.0, 3.0)

            await asyncio.sleep(delay)
            elapsed += delay


browser_warmup = BrowserWarmupEngine()
