"""Platform detection and per-platform yt-dlp option tuning."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# Ordered: first match wins. Patterns are matched against the hostname only.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("youtube", re.compile(r"(?:^|\.)(youtube\.com|youtu\.be|m\.youtube\.com)$")),
    ("tiktok", re.compile(r"(?:^|\.)(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)$")),
    ("instagram", re.compile(r"(?:^|\.)(instagram\.com|instagr\.am)$")),
    ("threads", re.compile(r"(?:^|\.)(threads\.net)$")),
    ("twitter", re.compile(r"(?:^|\.)(twitter\.com|x\.com|mobile\.twitter\.com|t\.co)$")),
    ("facebook", re.compile(r"(?:^|\.)(facebook\.com|fb\.watch|m\.facebook\.com)$")),
    ("soundcloud", re.compile(r"(?:^|\.)(soundcloud\.com|on\.soundcloud\.com)$")),
    ("pinterest", re.compile(r"(?:^|\.)(pinterest\.com|pin\.it)$")),
    ("reddit", re.compile(r"(?:^|\.)(reddit\.com|redd\.it|v\.redd\.it)$")),
    ("snapchat", re.compile(r"(?:^|\.)(snapchat\.com)$")),
    ("vimeo", re.compile(r"(?:^|\.)(vimeo\.com)$")),
    ("dailymotion", re.compile(r"(?:^|\.)(dailymotion\.com|dai\.ly)$")),
    ("twitch", re.compile(r"(?:^|\.)(twitch\.tv|clips\.twitch\.tv)$")),
]

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# Platforms that are documented to be unreliable / unsupported.
FRAGILE_PLATFORMS = {"snapchat", "threads"}


@dataclass(frozen=True)
class PlatformInfo:
    name: str
    url: str
    fragile: bool


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(".,);]")


def detect_platform(url: str) -> PlatformInfo:
    host = (urlparse(url).hostname or "").lower()
    for name, pat in _PATTERNS:
        if pat.search(host):
            return PlatformInfo(name=name, url=url, fragile=name in FRAGILE_PLATFORMS)
    return PlatformInfo(name="generic", url=url, fragile=False)


# --- yt-dlp format specifications -----------------------------------------

# These are kept simple and defensive. yt-dlp will fall through to the next
# chunk of the spec if the preferred one isn't available.
VIDEO_FORMAT_TIERS: list[str] = [
    # Prefer merged <=720p mp4 -> merged <=1080p mp4 -> best merged -> best single
    "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]",
    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]",
    "bestvideo+bestaudio/best",
    "best",
]

AUDIO_FORMAT_TIERS: list[str] = [
    "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio",
    "bestaudio/best",
]


def base_ydl_opts(platform: str, cookies_file: str | None, proxy: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "restrictfilenames": True,
        "overwrites": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        },
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    if proxy:
        opts["proxy"] = proxy

    # Platform-specific overrides
    if platform == "tiktok":
        # TikTok sometimes needs mobile-ish UA to get non-watermarked stream
        opts["http_headers"]["User-Agent"] = (
            "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Mobile Safari/537.36"
        )
    elif platform == "instagram":
        # Instagram anonymous access is flaky without cookies; keep retries high
        opts["retries"] = 5
    elif platform == "twitter":
        # x.com extractor can be slow
        opts["socket_timeout"] = 45
    elif platform == "facebook":
        opts["retries"] = 5
    elif platform == "soundcloud":
        # SoundCloud is audio; ensure we never try to merge with video
        opts["format_sort"] = ["abr"]
    return opts
