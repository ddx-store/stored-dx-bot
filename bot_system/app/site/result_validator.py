"""
SubscriptionValidator — HTTP-based post-payment subscription verification.
After Playwright completes payment, makes direct API calls to confirm
the subscription is actually active (avoids false positives from URL patterns).
"""
from __future__ import annotations

import asyncio
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

from app.core.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 12


def _chatgpt_check(data: dict) -> bool:
    plan = (data.get("plan_type") or "").lower()
    return plan not in ("", "free", None) or data.get("has_active_subscription") is True


def _canva_check(data: dict) -> bool:
    return data.get("isPro") is True or data.get("plan", "free") != "free"


def _proton_check(data: dict) -> bool:
    plan_name = data.get("Plans", [{}])
    if isinstance(plan_name, list) and plan_name:
        return plan_name[0].get("Name", "free").lower() != "free"
    return data.get("isPaid") is True


_VALIDATORS: Dict[str, dict] = {
    "chatgpt.com": {
        "url": "https://chatgpt.com/backend-api/me",
        "check": _chatgpt_check,
        "fallback_keywords": ["plus", "pro", "team", "enterprise"],
        "headers": {"accept": "application/json"},
    },
    "canva.com": {
        "url": "https://www.canva.com/_ajax/subscription-status",
        "check": _canva_check,
        "fallback_keywords": ["canva pro", "canva for teams"],
        "headers": {},
    },
    "protonvpn.com": {
        "url": "https://api.proton.me/payments/v4/subscription",
        "check": _proton_check,
        "fallback_keywords": ["plus", "unlimited", "visionary"],
        "headers": {"x-pm-appversion": "web-vpn@4.0.0"},
    },
}


class SubscriptionValidator:

    async def validate(self, domain: str, page) -> Optional[bool]:
        """
        Validate subscription status via direct API call using page cookies.
        Returns True = subscribed, False = not subscribed, None = unknown/error.
        """
        validator = _VALIDATORS.get(domain)
        if not validator:
            log.debug("No validator defined for domain=%s", domain)
            return None

        try:
            import aiohttp
            # Extract cookies from Playwright page
            context = page.context
            cookies = await context.cookies()
            cookie_jar = {c["name"]: c["value"] for c in cookies if domain in c.get("domain", "")}

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36"
                ),
                **validator.get("headers", {}),
            }

            async with aiohttp.ClientSession(
                cookies=cookie_jar,
                headers=headers,
            ) as session:
                async with session.get(
                    validator["url"],
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
                    allow_redirects=True,
                ) as resp:
                    if resp.status == 401:
                        log.warning("Validator: 401 for %s — session may have expired", domain)
                        return None
                    if resp.status != 200:
                        log.warning("Validator: HTTP %d for %s", resp.status, domain)
                        return None
                    data = await resp.json(content_type=None)
                    result = validator["check"](data)
                    log.info("Validator: domain=%s → subscribed=%s", domain, result)
                    return result

        except ImportError:
            log.debug("aiohttp not available — skipping validation")
            return None
        except Exception as exc:
            log.warning("Validator error for %s: %s", domain, exc)

        # Fallback: keyword scan on page body
        try:
            body = (await page.inner_text("body"))[:3000].lower()
            kws = validator.get("fallback_keywords", [])
            found = any(kw in body for kw in kws)
            if found:
                log.info("Validator: domain=%s → subscribed=True (keyword fallback)", domain)
            return found if found else None
        except Exception:
            return None

    def get_domain(self, site_url: str) -> str:
        return urlparse(site_url).netloc.replace("www.", "")


subscription_validator = SubscriptionValidator()
