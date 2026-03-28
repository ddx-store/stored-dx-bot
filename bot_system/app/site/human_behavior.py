"""
HumanBehaviorSimulator — makes browser interactions look human.
Bézier-curve mouse paths, variable WPM typing, pre-action scrolling,
micro-pauses between actions, and occasional hesitation patterns.
"""
from __future__ import annotations

import asyncio
import random
from typing import Tuple

from app.core.logger import get_logger

log = get_logger(__name__)


def _bezier_cubic(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
    t: float,
) -> Tuple[float, float]:
    u = 1 - t
    x = u**3*p0[0] + 3*u**2*t*p1[0] + 3*u*t**2*p2[0] + t**3*p3[0]
    y = u**3*p0[1] + 3*u**2*t*p1[1] + 3*u*t**2*p2[1] + t**3*p3[1]
    return (x, y)


class HumanBehaviorSimulator:

    async def move_to_and_click(self, page, element) -> None:
        """Move mouse along Bézier curve to element then click."""
        try:
            box = await element.bounding_box()
            if not box:
                await element.click()
                return
            tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await self._move_mouse(page, tx, ty)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await element.click()
        except Exception:
            try:
                await element.click()
            except Exception:
                pass

    async def _move_mouse(self, page, target_x: float, target_y: float) -> None:
        try:
            current = await page.evaluate(
                "() => ({ x: window._mouseX || Math.floor(Math.random()*400)+100,"
                "         y: window._mouseY || Math.floor(Math.random()*300)+100 })"
            )
            p0 = (float(current.get("x", 200)), float(current.get("y", 200)))
            p3 = (target_x, target_y)
            spread_x = abs(p3[0] - p0[0]) * 0.4 + 30
            spread_y = abs(p3[1] - p0[1]) * 0.4 + 30
            p1 = (
                p0[0] + random.uniform(-spread_x, spread_x),
                p0[1] + random.uniform(-spread_y, spread_y),
            )
            p2 = (
                p3[0] + random.uniform(-spread_x, spread_x),
                p3[1] + random.uniform(-spread_y, spread_y),
            )
            steps = random.randint(12, 25)
            for i in range(steps + 1):
                t = i / steps
                # Ease-in-out timing
                t_eased = t * t * (3 - 2 * t)
                px, py = _bezier_cubic(p0, p1, p2, p3, t_eased)
                await page.mouse.move(px, py)
                await asyncio.sleep(random.uniform(0.004, 0.018))
        except Exception:
            pass

    async def human_type(self, element, text: str) -> None:
        """Type with variable WPM (60-120), micro-pauses, and rare typos."""
        try:
            await element.click()
            await asyncio.sleep(random.uniform(0.1, 0.25))
        except Exception:
            pass

        char_count = 0
        for i, char in enumerate(text):
            char_count += 1
            # Micro-pause every few characters (simulates natural rhythm)
            if char_count % random.randint(4, 9) == 0:
                await asyncio.sleep(random.uniform(0.06, 0.22))

            # Rare deliberate hesitation (re-reading)
            if random.random() < 0.015 and i > 2:
                await asyncio.sleep(random.uniform(0.3, 0.7))

            try:
                await element.type(char, delay=random.randint(40, 115))
            except Exception:
                try:
                    await element.fill(text[:i+1])
                    break
                except Exception:
                    pass

            # Extremely rare typo + backspace
            if random.random() < 0.012 and char.isalpha():
                typo = random.choice("qwertyuiopasdfghjklzxcvbnm")
                try:
                    await element.type(typo, delay=random.randint(30, 80))
                    await asyncio.sleep(random.uniform(0.08, 0.18))
                    await element.press("Backspace")
                    await asyncio.sleep(random.uniform(0.05, 0.12))
                except Exception:
                    pass

        await asyncio.sleep(random.uniform(0.05, 0.15))

    async def pre_action_scroll(self, page, amount: int = 0) -> None:
        """Scroll slightly before interacting — mimics human browsing."""
        try:
            scroll_y = amount or random.randint(40, 180)
            await page.evaluate(f"window.scrollBy(0, {scroll_y})")
            await asyncio.sleep(random.uniform(0.2, 0.6))
            await page.evaluate(f"window.scrollBy(0, -{scroll_y // 2})")
            await asyncio.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass

    async def random_micro_pause(self, min_s: float = 0.3, max_s: float = 1.2) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def hover_before_click(self, page, element) -> None:
        """Hover over element briefly before clicking."""
        try:
            await element.hover()
            await asyncio.sleep(random.uniform(0.1, 0.35))
        except Exception:
            pass


human_sim = HumanBehaviorSimulator()
