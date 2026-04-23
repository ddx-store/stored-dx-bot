"""yt-dlp wrapper with fallback strategies and helpful error classification."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from .platforms import (
    AUDIO_FORMAT_TIERS,
    VIDEO_FORMAT_TIERS,
    base_ydl_opts,
    detect_platform,
)

log = logging.getLogger(__name__)


class DownloadFailure(Exception):
    """User-facing download failure with a category."""

    def __init__(self, message: str, category: str = "generic") -> None:
        super().__init__(message)
        self.category = category


@dataclass
class DownloadResult:
    file: Path
    title: str
    uploader: str | None
    duration: int | None
    platform: str
    size_bytes: int
    webpage_url: str
    kind: str  # "video" or "audio"


PROGRESS_THROTTLE_SECONDS = 2.0


def _classify_error(exc: BaseException) -> DownloadFailure:
    msg = str(exc).lower()
    if "private" in msg or "login required" in msg or "members-only" in msg:
        return DownloadFailure(
            "This content is private or requires login.", "private"
        )
    if "age" in msg and "restrict" in msg:
        return DownloadFailure(
            "This content is age-restricted and can't be downloaded anonymously.",
            "age_restricted",
        )
    if "geo" in msg or "not available in your country" in msg:
        return DownloadFailure(
            "This content is geo-blocked from this server's location.", "geo_blocked"
        )
    if "unsupported url" in msg or "no video" in msg or "unable to extract" in msg:
        return DownloadFailure(
            "That URL isn't supported or doesn't contain a downloadable video.",
            "unsupported",
        )
    if "http error 404" in msg or "not found" in msg:
        return DownloadFailure(
            "The post was deleted or the URL is invalid.", "not_found"
        )
    if "timed out" in msg or "timeout" in msg:
        return DownloadFailure(
            "The source timed out. Please try again in a moment.", "timeout"
        )
    if "copyright" in msg or "removed" in msg:
        return DownloadFailure(
            "The post was removed (possibly for copyright reasons).", "removed"
        )
    return DownloadFailure(
        "Download failed. The platform may be temporarily blocking bots.",
        "generic",
    )


def _discover_output(out_dir: Path, stem: str, info: dict[str, Any]) -> Path | None:
    """Find the actual file yt-dlp wrote. The extension depends on merging / postproc."""
    # Try the requested stem first with any extension.
    for p in sorted(out_dir.glob(f"{stem}.*")):
        if p.is_file() and not p.name.endswith(".part"):
            return p
    # Fall back to whatever yt-dlp set in info.
    for key in ("filepath", "_filename"):
        val = info.get(key) if isinstance(info, dict) else None
        if val and Path(val).exists():
            return Path(val)
    # Requested downloads may be listed.
    for req in info.get("requested_downloads", []) or []:
        path = req.get("filepath") or req.get("_filename")
        if path and Path(path).exists():
            return Path(path)
    return None


def _run_ydl(
    url: str,
    opts: dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    out_dir = Path(opts["paths"]["home"])
    stem = Path(opts["outtmpl"]).stem
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if isinstance(info, dict) and info.get("_type") == "playlist":
            entries = [e for e in info.get("entries") or [] if e]
            if not entries:
                raise DownloadFailure(
                    "That link resolved to an empty playlist.", "unsupported"
                )
            info = entries[0]
        path = _discover_output(out_dir, stem, info if isinstance(info, dict) else {})
    return info if isinstance(info, dict) else {}, path


class Downloader:
    """Async facade around yt-dlp with fallbacks."""

    def __init__(
        self,
        download_dir: Path,
        max_download_bytes: int,
        max_concurrent: int,
        cookies_file: str | None = None,
        proxy: str | None = None,
    ) -> None:
        self.download_dir = download_dir
        self.max_download_bytes = max_download_bytes
        self.cookies_file = cookies_file
        self.proxy = proxy
        self._sem = asyncio.Semaphore(max_concurrent)

    def _build_opts(
        self,
        *,
        platform: str,
        kind: str,
        format_spec: str,
        out_dir: Path,
        stem: str,
        progress_hook: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        opts = base_ydl_opts(platform, self.cookies_file, self.proxy)
        opts.update(
            {
                "format": format_spec,
                "paths": {"home": str(out_dir)},
                "outtmpl": f"{stem}.%(ext)s",
                "merge_output_format": "mp4",
                "max_filesize": self.max_download_bytes,
            }
        )
        if kind == "audio":
            opts["format"] = format_spec
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
            opts.pop("merge_output_format", None)
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]
        return opts

    async def fetch(
        self,
        url: str,
        *,
        kind: str = "video",
        progress: Callable[[str], Any] | None = None,
    ) -> DownloadResult:
        if kind not in {"video", "audio"}:
            raise ValueError(f"Unknown kind: {kind}")
        plat = detect_platform(url)
        tiers = AUDIO_FORMAT_TIERS if kind == "audio" else VIDEO_FORMAT_TIERS
        stem = f"{plat.name}_{uuid.uuid4().hex[:12]}"
        job_dir = self.download_dir / stem
        job_dir.mkdir(parents=True, exist_ok=True)

        last_progress_ts = 0.0

        def _hook(d: dict[str, Any]) -> None:
            nonlocal last_progress_ts
            if progress is None:
                return
            status = d.get("status")
            now = time.monotonic()
            if status == "downloading":
                if now - last_progress_ts < PROGRESS_THROTTLE_SECONDS:
                    return
                last_progress_ts = now
                pct = d.get("_percent_str") or ""
                speed = d.get("_speed_str") or ""
                eta = d.get("_eta_str") or ""
                try:
                    progress(
                        f"Downloading {pct.strip()} · {speed.strip()} · ETA {eta.strip()}"
                    )
                except Exception:  # noqa: BLE001
                    pass
            elif status == "finished":
                try:
                    progress("Processing…")
                except Exception:  # noqa: BLE001
                    pass

        last_exc: BaseException | None = None
        async with self._sem:
            loop = asyncio.get_running_loop()
            for fmt in tiers:
                opts = self._build_opts(
                    platform=plat.name,
                    kind=kind,
                    format_spec=fmt,
                    out_dir=job_dir,
                    stem=stem,
                    progress_hook=_hook,
                )
                log.info(
                    "yt-dlp attempt: platform=%s kind=%s format=%r url=%s",
                    plat.name,
                    kind,
                    fmt,
                    url,
                )
                try:
                    info, path = await loop.run_in_executor(
                        None, _run_ydl, url, opts
                    )
                    if not path or not path.exists():
                        raise DownloadFailure(
                            "yt-dlp did not produce an output file.", "generic"
                        )
                    size = path.stat().st_size
                    return DownloadResult(
                        file=path,
                        title=info.get("title") or "video",
                        uploader=info.get("uploader") or info.get("channel"),
                        duration=info.get("duration"),
                        platform=plat.name,
                        size_bytes=size,
                        webpage_url=info.get("webpage_url") or url,
                        kind=kind,
                    )
                except DownloadFailure as exc:
                    last_exc = exc
                    # User-facing classified failure; still try next tier.
                    log.warning("classified failure, trying next tier: %s", exc)
                    continue
                except (DownloadError, ExtractorError) as exc:
                    last_exc = _classify_error(exc)
                    log.warning(
                        "yt-dlp error (tier=%r): %s", fmt, exc, exc_info=False
                    )
                    # Private / not-found / unsupported: bail early, no point retrying.
                    if last_exc.category in {
                        "private",
                        "age_restricted",
                        "geo_blocked",
                        "not_found",
                        "unsupported",
                        "removed",
                    }:
                        break
                    continue
                except Exception as exc:  # noqa: BLE001
                    last_exc = _classify_error(exc)
                    log.exception("unexpected yt-dlp failure")
                    continue

        # All tiers failed. Clean up and raise.
        shutil.rmtree(job_dir, ignore_errors=True)
        if isinstance(last_exc, DownloadFailure):
            raise last_exc
        raise DownloadFailure(
            "Download failed after all fallback strategies.", "generic"
        )
