"""
Telegram handlers for media downloading.

Provides:
- ``/dl <url>`` command to download media
- ``/mediastats`` admin command showing download statistics
- ``media_url_handler`` that auto-detects pasted URLs
- Callback query handler for video/audio selection buttons
"""

from __future__ import annotations

import html
import os
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from app.core.config import config
from app.core.logger import get_logger
from app.media.downloader import DownloadEngine, DownloadResult
from app.media.rate_limiter import RateLimiter
from app.media.url_parser import Platform, detect_platform, extract_urls, is_restricted

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons (created lazily)
# ---------------------------------------------------------------------------

_engine: Optional[DownloadEngine] = None
_limiter: Optional[RateLimiter] = None


def _get_engine() -> DownloadEngine:
    global _engine
    if _engine is None:
        download_dir = os.environ.get("MEDIA_DOWNLOAD_DIR", "/tmp/media_downloads")
        max_size = int(os.environ.get("MEDIA_MAX_FILE_SIZE_MB", "50")) * 1024 * 1024
        max_retries = int(os.environ.get("MEDIA_MAX_RETRIES", "3"))
        _engine = DownloadEngine(
            download_dir=download_dir,
            max_file_size_bytes=max_size,
            max_retries=max_retries,
        )
    return _engine


def _get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        max_req = int(os.environ.get("MEDIA_RATE_LIMIT", "5"))
        window = int(os.environ.get("MEDIA_RATE_WINDOW", "60"))
        _limiter = RateLimiter(max_req, window)
    return _limiter


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORM_EMOJI = {
    Platform.TIKTOK: "\U0001f3b5",       # 🎵
    Platform.INSTAGRAM: "\U0001f4f8",     # 📸
    Platform.YOUTUBE: "\u25b6\ufe0f",     # ▶️
    Platform.TWITTER: "\U0001f426",       # 🐦
    Platform.FACEBOOK: "\U0001f4d8",      # 📘
    Platform.SOUNDCLOUD: "\U0001f3a7",    # 🎧
    Platform.PINTEREST: "\U0001f4cc",     # 📌
    Platform.REDDIT: "\U0001f916",        # 🤖
    Platform.THREADS: "\U0001f9f5",       # 🧵
    Platform.SNAPCHAT: "\U0001f47b",      # 👻
    Platform.UNKNOWN: "\U0001f310",       # 🌐
}

MEDIA_HELP = (
    "<b>Media Downloader</b>\n\n"
    "Send me a link from any of these platforms:\n\n"
    "  TikTok  |  Instagram  |  YouTube\n"
    "  Twitter/X  |  Facebook  |  SoundCloud\n"
    "  Pinterest  |  Reddit  |  Threads\n\n"
    "<b>Usage:</b>\n"
    "• Paste a link directly\n"
    "• Or use <code>/dl &lt;url&gt;</code>\n\n"
    "Then choose <b>Video</b> or <b>Audio</b>."
)


# ---------------------------------------------------------------------------
# Authorisation helper
# ---------------------------------------------------------------------------

def _is_allowed(user_id: int) -> bool:
    if not config.TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in config.TELEGRAM_ALLOWED_USER_IDS


# ---------------------------------------------------------------------------
# /dl command
# ---------------------------------------------------------------------------

async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/dl <url>`` — download media from a supported platform."""
    user_id = update.effective_user.id
    if not _is_allowed(user_id):
        await update.message.reply_text("You are not authorised to use this bot.")
        return

    text = update.message.text or ""
    urls = extract_urls(text)
    if not urls:
        await update.message.reply_text(MEDIA_HELP, parse_mode=ParseMode.HTML)
        return

    await _offer_format(update, urls[0])


async def cmd_media_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/mediahelp`` — show media downloader instructions."""
    await update.message.reply_text(MEDIA_HELP, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /mediastats (admin only)
# ---------------------------------------------------------------------------

async def cmd_media_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/mediastats`` — show download statistics (admin only)."""
    user_id = update.effective_user.id
    admin_id = config.ADMIN_CHAT_ID
    allowed_ids = config.TELEGRAM_ALLOWED_USER_IDS

    is_admin = (admin_id and user_id == admin_id) or (allowed_ids and user_id in allowed_ids)
    if not is_admin:
        await update.message.reply_text("You are not authorised.")
        return

    stats = _get_engine().stats
    total = stats["total"]
    success = stats["success"]
    failed = stats["failed"]
    rate = (success / total * 100) if total > 0 else 0

    text = (
        "<b>Media Download Statistics</b>\n\n"
        f"Total downloads: <b>{total}</b>\n"
        f"Successful: <b>{success}</b>\n"
        f"Failed: <b>{failed}</b>\n"
        f"Success rate: <b>{rate:.1f}%</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Auto-detect handler (called from existing text_handler pipeline)
# ---------------------------------------------------------------------------

async def media_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Inspect a text message for media URLs. If one is found, show the
    video/audio choice and return ``True`` so the caller knows the message
    was consumed.  Returns ``False`` if no media URL was detected.
    """
    text = update.message.text or ""
    urls = extract_urls(text)
    if not urls:
        return False

    url = urls[0]
    platform = detect_platform(url)
    if platform == Platform.UNKNOWN:
        return False

    user_id = update.effective_user.id
    if not _is_allowed(user_id):
        return False

    await _offer_format(update, url)
    return True


# ---------------------------------------------------------------------------
# Callback handler (video / audio buttons)
# ---------------------------------------------------------------------------

CALLBACK_PREFIX = "mdl:"          # media-download prefix

async def media_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """
    Handle callback queries whose ``data`` starts with ``mdl:``.
    Returns ``True`` if the callback was consumed, ``False`` otherwise.
    """
    query = update.callback_query
    data = query.data or ""
    if not data.startswith(CALLBACK_PREFIX):
        return False

    await query.answer()

    payload = data[len(CALLBACK_PREFIX):]
    if "|" not in payload:
        return True

    media_type, url = payload.split("|", 1)
    platform = detect_platform(url)
    user_id = update.effective_user.id

    limiter = _get_limiter()
    allowed, wait = limiter.check(user_id)
    if not allowed:
        await query.edit_message_text(
            f"Rate limit reached. Please wait {wait}s before trying again.",
        )
        return True

    limiter.record(user_id)

    emoji = PLATFORM_EMOJI.get(platform, "\U0001f310")
    kind = "video" if media_type == "video" else "audio"
    status_msg = await query.edit_message_text(
        f"{emoji} <b>Downloading {kind}…</b>\n\nPlease wait.",
        parse_mode=ParseMode.HTML,
    )

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_VIDEO if media_type == "video" else ChatAction.UPLOAD_VOICE,
    )

    engine = _get_engine()
    if media_type == "video":
        result = await engine.download_video(url, platform)
    else:
        result = await engine.download_audio(url, platform)

    if not result.success:
        await status_msg.edit_text(
            f"<b>Download failed</b>\n\n{html.escape(result.error or 'Unknown error')}",
            parse_mode=ParseMode.HTML,
        )
        return True

    await status_msg.edit_text(
        f"{emoji} <b>Uploading {kind} to Telegram…</b>",
        parse_mode=ParseMode.HTML,
    )

    try:
        await _send_media(update, result, media_type)

        title = html.escape(result.title or "Unknown")
        size_mb = (result.filesize or 0) / (1024 * 1024)
        dur = _fmt_duration(result.duration)

        caption = f"<b>{title}</b>"
        if dur:
            caption += f"\n{dur}"
        caption += f"\n{size_mb:.1f} MB"

        await status_msg.edit_text(
            f"<b>Done!</b>\n\n{caption}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        log.error("Failed to send media to Telegram: %s", exc, exc_info=True)
        await status_msg.edit_text(
            "<b>Upload failed</b>\n\n"
            "The file could not be sent — it may exceed Telegram's limit.",
            parse_mode=ParseMode.HTML,
        )
    finally:
        engine.cleanup_file(result.file_path)

    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _offer_format(update: Update, url: str) -> None:
    """Show the video / audio inline-keyboard for a URL."""
    platform = detect_platform(url)

    if is_restricted(platform):
        emoji = PLATFORM_EMOJI.get(platform, "\U0001f310")
        await update.message.reply_text(
            f"{emoji} Sorry, {platform.value.capitalize()} is not supported "
            "due to platform restrictions.",
        )
        return

    limiter = _get_limiter()
    allowed, wait = limiter.check(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(
            f"Rate limit reached. Please wait {wait}s before trying again.",
        )
        return

    emoji = PLATFORM_EMOJI.get(platform, "\U0001f310")
    name = platform.value.capitalize() if platform != Platform.UNKNOWN else "Unknown platform"

    keyboard = [
        [
            InlineKeyboardButton("Video", callback_data=f"{CALLBACK_PREFIX}video|{url}"),
            InlineKeyboardButton("Audio", callback_data=f"{CALLBACK_PREFIX}audio|{url}"),
        ],
    ]

    await update.message.reply_text(
        f"{emoji} <b>{name}</b> link detected!\n\nChoose download format:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def _send_media(
    update: Update, result: DownloadResult, media_type: str,
) -> None:
    chat_id = update.effective_chat.id
    with open(result.file_path, "rb") as f:
        if media_type == "video":
            await update.get_bot().send_video(
                chat_id=chat_id,
                video=f,
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
        else:
            await update.get_bot().send_audio(
                chat_id=chat_id,
                audio=f,
                title=result.title,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )


def _fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
