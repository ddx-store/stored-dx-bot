"""
Application entry point.

Start the bot with:
    python -m app.main

Or from the bot_system directory:
    python app/main.py
"""

from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bot.handlers import register_handlers
from app.bot.telegram_client import build_application, set_main_loop
from app.core.logger import configure_root, get_logger
from app.storage.db import init_db

configure_root()
log = get_logger("main")


def main() -> None:
    log.info("Initialising bot system")

    init_db()

    application = build_application()

    register_handlers(application)

    async def post_init(app):
        set_main_loop(asyncio.get_running_loop())
        log.info("Main event loop stored for worker threads")

    application.post_init = post_init

    log.info("Bot polling started — press Ctrl-C to stop")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
