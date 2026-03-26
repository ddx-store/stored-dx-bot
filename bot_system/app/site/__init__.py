"""
Site integration factory.

Returns the correct SiteIntegrationBase implementation based on
SITE_INTEGRATION_MODE environment variable.
"""

from __future__ import annotations

from app.core.config import config
from app.core.enums import IntegrationMode
from app.site.base import SiteIntegrationBase


def get_site_integration() -> SiteIntegrationBase:
    mode = config.SITE_INTEGRATION_MODE.lower()
    if mode == IntegrationMode.PLAYWRIGHT.value:
        from app.site.playwright_client import PlaywrightClient
        return PlaywrightClient()
    # Default: HTTP API
    from app.site.api_client import ApiClient
    return ApiClient()
