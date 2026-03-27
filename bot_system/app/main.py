from __future__ import annotations

import asyncio
import signal
import sys
import os
import threading
import time

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.chdir(project_root)

try:
    from dotenv import load_dotenv
    env_path = os.path.join(project_root, ".env")
    if os.path.isfile(env_path):
        load_dotenv(env_path)
except ImportError:
    pass

from app.core.logger import configure_root, get_logger

configure_root()
log = get_logger("main")


def _start_cleanup_thread():
    from app.core.config import config
    from app.storage.repositories import CleanupRepository

    interval = 6 * 3600

    def worker():
        while True:
            try:
                time.sleep(interval)
                repo = CleanupRepository()
                deleted = repo.delete_old_jobs(config.CLEANUP_DAYS)
                if deleted > 0:
                    log.info("Auto-cleanup: removed %d old records", deleted)
            except Exception as exc:
                log.error("Cleanup thread error: %s", exc)

    t = threading.Thread(target=worker, daemon=True, name="cleanup-worker")
    t.start()
    log.info("Cleanup thread started (every %dh, keep %d days)", interval // 3600, config.CLEANUP_DAYS)


def main() -> None:
    log.info("Initialising bot system")

    from app.storage.db import init_db
    init_db()

    from app.bot.telegram_client import build_application, set_main_loop
    application = build_application()

    from app.bot.handlers import register_handlers
    register_handlers(application)

    _start_cleanup_thread()

    async def post_init(app):
        loop = asyncio.get_running_loop()
        set_main_loop(loop)
        log.info("Main event loop stored for worker threads")
        me = await app.bot.get_me()
        log.info("Bot identity: @%s (id=%s)", me.username, me.id)

    async def post_shutdown(app):
        log.info("Bot shutdown complete")

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    log.info("Bot polling started — press Ctrl-C to stop")
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
