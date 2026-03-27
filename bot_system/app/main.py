"""
Application entry point.
"""

from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import configure_root, get_logger

configure_root()
log = get_logger("main")


def main() -> None:
    log.info("Initialising bot system")

    from app.storage.db import init_db
    init_db()

    from app.bot.telegram_client import build_application, set_main_loop
    application = build_application()

    from app.bot.handlers import register_handlers
    register_handlers(application)

    async def post_init(app):
        loop = asyncio.get_running_loop()
        set_main_loop(loop)
        log.info("Main event loop stored for worker threads")
        me = await app.bot.get_me()
        log.info("Bot identity: @%s (id=%s)", me.username, me.id)

    application.post_init = post_init

    log.info("Bot polling started — press Ctrl-C to stop")
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
