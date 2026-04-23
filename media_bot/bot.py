"""Entry point for the Telegram media downloader bot.

Run:
    python bot.py

Environment variables are loaded from .env (see .env.example).
"""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

# Allow running as "python bot.py" without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from mdbot import admin, handlers
from mdbot.config import Config, load_config
from mdbot.downloader import Downloader
from mdbot.logging_setup import setup_logging
from mdbot.rate_limit import RateLimiter
from mdbot.storage import Stats


def build_app(cfg: Config) -> Application:
    app = ApplicationBuilder().token(cfg.token).concurrent_updates(True).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["downloader"] = Downloader(
        download_dir=cfg.download_dir,
        max_download_bytes=cfg.max_download_bytes,
        max_concurrent=cfg.max_concurrent_downloads,
        cookies_file=cfg.cookies_file,
        proxy=cfg.proxy,
    )
    app.bot_data["limiter"] = RateLimiter(
        max_events=cfg.rate_limit_count,
        window_seconds=cfg.rate_limit_window_seconds,
    )
    app.bot_data["stats"] = Stats(cfg.db_path)

    handlers.register(app)
    admin.register(app)

    async def _post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Welcome + how to use"),
                BotCommand("help", "Show help"),
                BotCommand("video", "Download link as MP4"),
                BotCommand("audio", "Extract link as MP3"),
                BotCommand("whoami", "Show your user id"),
            ]
        )

    app.post_init = _post_init
    return app


def main() -> None:
    try:
        cfg = load_config()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)

    logger = setup_logging(cfg.log_level, log_file=Path("logs/bot.log"))
    logger.info("starting media downloader bot")

    app = build_app(cfg)

    # Graceful shutdown on SIGTERM (run_polling already handles SIGINT).
    def _handle_term(signum: int, _frame: object) -> None:
        logging.getLogger("mdbot").info("received signal %s, shutting down", signum)

    signal.signal(signal.SIGTERM, _handle_term)

    app.run_polling(
        allowed_updates=None,
        close_loop=False,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
