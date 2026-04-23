"""Telegram message / callback handlers."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Config
from .downloader import DownloadFailure, DownloadResult, Downloader
from .platforms import detect_platform, extract_first_url
from .rate_limit import RateLimiter
from .storage import Stats
from .utils import ensure_file_cleanup, human_bytes, human_seconds, safe_html

log = logging.getLogger(__name__)

PENDING_KEY = "mdbot_pending"


@dataclass
class PendingJob:
    url: str
    platform: str


def _mk_action_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📹 Video", callback_data=f"dl:video:{token}"),
                InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl:audio:{token}"),
            ],
            [InlineKeyboardButton("✖️ Cancel", callback_data=f"dl:cancel:{token}")],
        ]
    )


def _gate(update: Update, cfg: Config) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return cfg.is_allowed(user.id)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _gate(update, cfg):
        await update.message.reply_text("This bot is private.")
        return
    msg = (
        "<b>Media Downloader Bot</b>\n\n"
        "Send me a link from TikTok, Instagram, YouTube, X/Twitter, Facebook, "
        "SoundCloud, Pinterest, Reddit, Threads, Vimeo, Dailymotion, or Twitch "
        "and I'll grab the media for you.\n\n"
        "Use /help for commands and tips."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _gate(update, cfg):
        return
    lines = [
        "<b>How to use</b>",
        "• Paste any supported link. I'll show Video / Audio buttons.",
        "• Or use /video &lt;url&gt; or /audio &lt;url&gt; directly.",
        "",
        "<b>Commands</b>",
        "/start – welcome",
        "/help – this message",
        "/video &lt;url&gt; – download as MP4",
        "/audio &lt;url&gt; – extract as MP3",
        "",
        "<b>Limits</b>",
        f"• Max upload: {cfg.max_upload_mb} MB (Telegram Bot API cap).",
        f"• Rate limit: {cfg.rate_limit_count} downloads / {cfg.rate_limit_window_seconds}s.",
        "",
        "Snapchat and some restricted platforms may not work.",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _gate(update, cfg) or update.message is None:
        return
    url = extract_first_url(update.message.text or "")
    if not url:
        await update.message.reply_text(
            "Send me a supported link (TikTok, YouTube, Instagram…)."
        )
        return
    plat = detect_platform(url)
    if plat.name == "snapchat":
        await update.message.reply_text(
            "Snapchat isn't supported reliably. Sorry."
        )
        return
    token = f"{update.message.message_id}"
    pending: dict[str, PendingJob] = context.user_data.setdefault(PENDING_KEY, {})
    pending[token] = PendingJob(url=url, platform=plat.name)

    caveat = ""
    if plat.fragile:
        caveat = f"\n<i>Note: {plat.name} support is best-effort.</i>"
    await update.message.reply_text(
        f"<b>Detected:</b> {plat.name}{caveat}\nPick a format:",
        parse_mode=ParseMode.HTML,
        reply_markup=_mk_action_keyboard(token),
    )


async def _direct_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    kind: str,
) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _gate(update, cfg) or update.message is None:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(f"Usage: /{kind} <url>")
        return
    url = extract_first_url(" ".join(args))
    if not url:
        await update.message.reply_text("That doesn't look like a URL.")
        return
    plat = detect_platform(url)
    await _run_download(update, context, url=url, kind=kind, platform=plat.name)


async def video_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _direct_cmd(update, context, kind="video")


async def audio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _direct_cmd(update, context, kind="audio")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    if not _gate(update, cfg):
        await query.edit_message_text("This bot is private.")
        return
    try:
        _, action, token = query.data.split(":", 2)
    except ValueError:
        return
    pending: dict[str, PendingJob] = context.user_data.get(PENDING_KEY, {})
    job = pending.pop(token, None)
    if job is None:
        await query.edit_message_text("This request expired. Send the link again.")
        return
    if action == "cancel":
        await query.edit_message_text("Cancelled.")
        return
    kind = "audio" if action == "audio" else "video"
    # Replace keyboard with a running status message.
    try:
        await query.edit_message_text(
            f"<b>{job.platform}</b> · {kind}\nQueued…",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass

    await _run_download(
        update,
        context,
        url=job.url,
        kind=kind,
        platform=job.platform,
        status_message=query.message,
    )


async def _edit_status(message: Any, text: str) -> None:
    """Best-effort edit that silently swallows 'message is not modified' errors."""
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.debug("status edit failed: %s", exc)
    except TelegramError as exc:
        log.debug("status edit failed: %s", exc)


async def _run_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    url: str,
    kind: str,
    platform: str,
    status_message: Any | None = None,
) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    downloader: Downloader = context.application.bot_data["downloader"]
    limiter: RateLimiter = context.application.bot_data["limiter"]
    stats: Stats = context.application.bot_data["stats"]

    user = update.effective_user
    assert user is not None
    chat_id = update.effective_chat.id if update.effective_chat else user.id

    allowed, retry_after = limiter.check(user.id)
    if not allowed:
        msg = f"Rate limit — try again in {retry_after}s."
        if status_message:
            await _edit_status(status_message, msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    if status_message is None:
        status_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"<b>{safe_html(platform)}</b> · {kind}\nStarting…",
            parse_mode=ParseMode.HTML,
        )
    else:
        await _edit_status(
            status_message, f"<b>{safe_html(platform)}</b> · {kind}\nStarting…"
        )

    # Schedule throttled status updates posted from the progress hook.
    loop = asyncio.get_running_loop()
    last_text = {"value": ""}

    def _progress(text: str) -> None:
        if text == last_text["value"]:
            return
        last_text["value"] = text
        asyncio.run_coroutine_threadsafe(
            _edit_status(
                status_message,
                f"<b>{safe_html(platform)}</b> · {kind}\n{safe_html(text)}",
            ),
            loop,
        )

    started = time.monotonic()
    result: DownloadResult | None = None
    error: str | None = None
    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VIDEO if kind == "video" else ChatAction.UPLOAD_VOICE,
        )
        result = await downloader.fetch(url, kind=kind, progress=_progress)
        if result.size_bytes > cfg.max_upload_bytes:
            error = (
                f"File is {human_bytes(result.size_bytes)}, but Telegram bots "
                f"can only upload up to {cfg.max_upload_mb} MB. "
                "Try /audio for a smaller file, or host a local Bot API server."
            )
            raise DownloadFailure(error, "too_large")

        await _edit_status(
            status_message,
            f"<b>{safe_html(platform)}</b> · {kind}\nUploading {human_bytes(result.size_bytes)}…",
        )
        await _send_media(context, chat_id, result)
        await _edit_status(
            status_message,
            f"<b>{safe_html(platform)}</b> · {kind}\n"
            f"Done — {human_bytes(result.size_bytes)} in "
            f"{human_seconds(time.monotonic() - started)}.",
        )
    except DownloadFailure as exc:
        error = str(exc)
        log.info("download failed (%s): %s", exc.category, exc)
        await _edit_status(status_message, f"❌ {safe_html(error)}")
    except Exception as exc:  # noqa: BLE001
        error = "Internal error. The admins have been notified."
        log.exception("unexpected handler error")
        await _edit_status(status_message, f"❌ {safe_html(error)}")
        exc  # silence flake
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        stats.record(
            user_id=user.id,
            username=user.username,
            platform=platform,
            kind=kind,
            url=url,
            success=result is not None and error is None,
            size_bytes=result.size_bytes if result else None,
            duration_ms=duration_ms,
            error=error,
        )
        if result:
            ensure_file_cleanup(result.file)


async def _send_media(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    result: DownloadResult,
) -> None:
    caption_parts = [f"<b>{safe_html(result.title)}</b>"]
    if result.uploader:
        caption_parts.append(f"by {safe_html(result.uploader)}")
    caption_parts.append(f"<a href=\"{safe_html(result.webpage_url)}\">source</a>")
    caption = "\n".join(caption_parts)[:1024]
    path: Path = result.file
    with path.open("rb") as fh:
        if result.kind == "audio":
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=fh,
                title=result.title[:60],
                performer=(result.uploader or "")[:60] or None,
                duration=result.duration,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            await context.bot.send_video(
                chat_id=chat_id,
                video=fh,
                duration=result.duration,
                caption=caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
            )


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _gate(update, cfg) or update.message is None:
        return
    await update.message.reply_text(
        "Send me a link from a supported platform, or /help."
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("video", video_cmd))
    app.add_handler(CommandHandler("audio", audio_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^dl:"))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Entity("url"),
            url_handler,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Entity("text_link"),
            url_handler,
        )
    )
    # Fallback for plain text that isn't a command or URL.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text),
    )
