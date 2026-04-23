"""
Download engine using yt-dlp with platform-specific optimisations
and automatic fallback strategies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

from app.media.url_parser import Platform

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DownloadResult:
    """Outcome of a single download attempt."""

    success: bool
    file_path: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    filesize: Optional[int] = None
    media_type: str = "video"            # "video" | "audio"
    error: Optional[str] = None
    platform: Optional[Platform] = None
    thumbnail: Optional[str] = None


class ProgressTracker:
    """Receives yt-dlp progress hooks and stores the latest state."""

    def __init__(self) -> None:
        self.status: str = "starting"
        self.percent: float = 0.0
        self.speed: Optional[str] = None
        self.eta: Optional[str] = None

    def hook(self, d: dict) -> None:
        if d["status"] == "downloading":
            self.status = "downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                self.percent = (downloaded / total) * 100
            self.speed = d.get("_speed_str", "")
            self.eta = d.get("_eta_str", "")
        elif d["status"] == "finished":
            self.status = "finished"
            self.percent = 100.0


# ---------------------------------------------------------------------------
# Download engine
# ---------------------------------------------------------------------------

class DownloadEngine:
    """Core download engine — wraps yt-dlp with fallback strategies."""

    def __init__(
        self,
        download_dir: str = "/tmp/media_downloads",
        max_file_size_bytes: int = 50 * 1024 * 1024,
        max_retries: int = 3,
        preferred_video_format: str = "mp4",
        preferred_audio_format: str = "mp3",
    ) -> None:
        self.download_dir = download_dir
        self.max_file_size_bytes = max_file_size_bytes
        self.max_retries = max_retries
        self.preferred_video_format = preferred_video_format
        self.preferred_audio_format = preferred_audio_format
        self._stats = {"total": 0, "success": 0, "failed": 0}
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)

    @property
    def stats(self) -> dict:
        return self._stats.copy()

    # ---- option builders ---------------------------------------------------

    def _base_opts(self, output_path: str) -> dict:
        return {
            "outtmpl": output_path,
            "noplaylist": True,
            "no_warnings": True,
            "quiet": True,
            "no_color": True,
            "socket_timeout": 30,
            "retries": self.max_retries,
            "fragment_retries": self.max_retries,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        }

    def _video_opts(
        self, output_path: str, platform: Platform, tracker: ProgressTracker,
    ) -> dict:
        opts = self._base_opts(output_path)
        opts["progress_hooks"] = [tracker.hook]
        opts["merge_output_format"] = self.preferred_video_format

        format_map = {
            Platform.YOUTUBE: (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/best"
            ),
            Platform.REDDIT: "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
        }
        fmt = format_map.get(platform, "best[ext=mp4]/best")
        opts["format"] = fmt

        referer_map = {
            Platform.TIKTOK: "https://www.tiktok.com/",
            Platform.INSTAGRAM: "https://www.instagram.com/",
        }
        if platform in referer_map:
            opts["http_headers"]["Referer"] = referer_map[platform]

        return opts

    def _audio_opts(
        self, output_path: str, platform: Platform, tracker: ProgressTracker,
    ) -> dict:
        opts = self._base_opts(output_path)
        opts["progress_hooks"] = [tracker.hook]

        if platform == Platform.SOUNDCLOUD:
            opts["format"] = "best"
        elif platform == Platform.YOUTUBE:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            opts["format"] = "bestaudio/best"

        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": self.preferred_audio_format,
                "preferredquality": "192",
            },
        ]
        return opts

    def _fallback_opts(
        self, output_path: str, tracker: ProgressTracker,
    ) -> list[dict]:
        """Return progressively more lenient option sets."""
        base = self._base_opts(output_path)
        base["progress_hooks"] = [tracker.hook]
        return [
            {**base, "format": "best[ext=mp4]/best"},
            {**base, "format": "best", "merge_output_format": "mp4"},
            {
                **base,
                "format": "best",
                "http_headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Mobile Safari/537.36"
                    ),
                },
            },
        ]

    # ---- public API -------------------------------------------------------

    async def extract_info(self, url: str) -> Optional[dict]:
        """Extract metadata without downloading."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "no_color": True,
            "skip_download": True,
            "socket_timeout": 15,
        }
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False),
            )
        except Exception as exc:
            log.warning("Info extraction failed for %s: %s", url, exc)
            return None

    async def download_video(self, url: str, platform: Platform) -> DownloadResult:
        self._stats["total"] += 1
        ts = int(time.time() * 1000)
        out = os.path.join(self.download_dir, f"video_{ts}.%(ext)s")
        tracker = ProgressTracker()

        result = await self._execute(url, self._video_opts(out, platform, tracker), "video", platform)

        if not result.success:
            log.info("Primary download failed for %s — trying fallbacks", url)
            for i, fb in enumerate(self._fallback_opts(out, tracker)):
                log.info("Fallback strategy %d for %s", i + 1, url)
                result = await self._execute(url, fb, "video", platform)
                if result.success:
                    break

        self._stats["success" if result.success else "failed"] += 1
        return result

    async def download_audio(self, url: str, platform: Platform) -> DownloadResult:
        self._stats["total"] += 1
        ts = int(time.time() * 1000)
        out = os.path.join(self.download_dir, f"audio_{ts}.%(ext)s")
        tracker = ProgressTracker()

        result = await self._execute(url, self._audio_opts(out, platform, tracker), "audio", platform)
        self._stats["success" if result.success else "failed"] += 1
        return result

    # ---- internals --------------------------------------------------------

    async def _execute(
        self, url: str, opts: dict, media_type: str, platform: Platform,
    ) -> DownloadResult:
        try:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._run_ytdlp(url, opts),
            )
            if info is None:
                return DownloadResult(
                    success=False,
                    error="Download completed but no info was returned.",
                    platform=platform,
                )

            file_path = self._find_file(info, opts.get("outtmpl", ""))
            if not file_path or not os.path.exists(file_path):
                return DownloadResult(
                    success=False,
                    error="Download completed but the file was not found on disk.",
                    platform=platform,
                )

            filesize = os.path.getsize(file_path)
            if filesize > self.max_file_size_bytes:
                os.remove(file_path)
                size_mb = filesize / (1024 * 1024)
                limit_mb = self.max_file_size_bytes / (1024 * 1024)
                return DownloadResult(
                    success=False,
                    error=(
                        f"File too large ({size_mb:.1f} MB). "
                        f"Telegram limit is {limit_mb:.0f} MB."
                    ),
                    platform=platform,
                )

            return DownloadResult(
                success=True,
                file_path=file_path,
                title=info.get("title", "Unknown"),
                duration=info.get("duration"),
                filesize=filesize,
                media_type=media_type,
                platform=platform,
                thumbnail=info.get("thumbnail"),
            )
        except yt_dlp.utils.DownloadError as exc:
            msg = _friendly_error(str(exc))
            log.error("yt-dlp error for %s: %s", url, msg)
            return DownloadResult(success=False, error=msg, platform=platform)
        except Exception as exc:
            log.error("Unexpected download error for %s: %s", url, exc, exc_info=True)
            return DownloadResult(
                success=False,
                error=f"An unexpected error occurred: {type(exc).__name__}",
                platform=platform,
            )

    @staticmethod
    def _run_ytdlp(url: str, opts: dict) -> Optional[dict]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    @staticmethod
    def _find_file(info: dict, outtmpl: str) -> Optional[str]:
        requested = info.get("requested_downloads")
        if requested:
            for item in requested:
                fpath = item.get("filepath") or item.get("filename")
                if fpath and os.path.exists(fpath):
                    return fpath

        if outtmpl:
            directory = os.path.dirname(outtmpl)
            prefix = os.path.basename(outtmpl).split(".")[0]
            if os.path.isdir(directory):
                for name in sorted(os.listdir(directory), reverse=True):
                    if name.startswith(prefix):
                        return os.path.join(directory, name)
        return None

    def cleanup_file(self, file_path: Optional[str]) -> None:
        """Remove a downloaded file from disk."""
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                log.debug("Cleaned up %s", file_path)
            except OSError as exc:
                log.warning("Failed to clean up %s: %s", file_path, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _friendly_error(raw: str) -> str:
    """Translate yt-dlp error strings into user-friendly messages."""
    low = raw.lower()
    if "private" in low or "login" in low or "authentication" in low:
        return "This content is private or requires login."
    if "not found" in low or "404" in low or "does not exist" in low:
        return "Content not found — it may have been deleted."
    if "geo" in low or "country" in low or "region" in low:
        return "This content is not available in the server's region."
    if "copyright" in low or "blocked" in low:
        return "Blocked due to copyright restrictions."
    if "age" in low:
        return "Age-restricted content cannot be downloaded."
    if "format" in low and "available" in low:
        return "No downloadable format found for this content."
    if "unsupported" in low:
        return "This URL or platform is not supported."
    return "Download failed — the content may be unavailable or restricted."
