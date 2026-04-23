"""
URL parsing and platform detection.
Extracts URLs from messages and identifies the source platform.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional, Tuple


class Platform(Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    SOUNDCLOUD = "soundcloud"
    PINTEREST = "pinterest"
    REDDIT = "reddit"
    THREADS = "threads"
    SNAPCHAT = "snapchat"
    UNKNOWN = "unknown"


PLATFORM_PATTERNS: List[Tuple[Platform, re.Pattern]] = [
    (Platform.TIKTOK, re.compile(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/", re.IGNORECASE,
    )),
    (Platform.INSTAGRAM, re.compile(
        r"https?://(?:www\.)?instagram\.com/", re.IGNORECASE,
    )),
    (Platform.YOUTUBE, re.compile(
        r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE,
    )),
    (Platform.TWITTER, re.compile(
        r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/", re.IGNORECASE,
    )),
    (Platform.FACEBOOK, re.compile(
        r"https?://(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.watch)/", re.IGNORECASE,
    )),
    (Platform.SOUNDCLOUD, re.compile(
        r"https?://(?:www\.|m\.)?soundcloud\.com/", re.IGNORECASE,
    )),
    (Platform.PINTEREST, re.compile(
        r"https?://(?:www\.|[a-z]{2}\.)?pinterest\.\w+/", re.IGNORECASE,
    )),
    (Platform.REDDIT, re.compile(
        r"https?://(?:www\.|old\.|new\.)?reddit\.com/", re.IGNORECASE,
    )),
    (Platform.THREADS, re.compile(
        r"https?://(?:www\.)?threads\.net/", re.IGNORECASE,
    )),
    (Platform.SNAPCHAT, re.compile(
        r"https?://(?:www\.)?snapchat\.com/", re.IGNORECASE,
    )),
]

RESTRICTED_PLATFORMS = {Platform.SNAPCHAT}

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def extract_urls(text: str) -> List[str]:
    """Extract all URLs from a text message."""
    return _URL_RE.findall(text)


def detect_platform(url: str) -> Platform:
    """Identify which platform a URL belongs to."""
    for platform, pattern in PLATFORM_PATTERNS:
        if pattern.search(url):
            return platform
    return Platform.UNKNOWN


def is_restricted(platform: Platform) -> bool:
    """Check if a platform has known restrictions that prevent downloading."""
    return platform in RESTRICTED_PLATFORMS


def parse_message(text: str) -> Optional[Tuple[str, Platform]]:
    """
    Extract the first valid URL and its platform from a user message.
    Returns ``(url, platform)`` or ``None`` if no URL is found.
    """
    urls = extract_urls(text)
    if not urls:
        return None
    url = urls[0]
    return url, detect_platform(url)
