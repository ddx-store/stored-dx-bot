"""
Application entry point.

Start the bot with:
    python -m app.main

Or from the bot_system directory:
    python app/main.py
"""

from __future__ import annotations

import sys
import os

# Ensure the package root is on sys.path when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bot.handlers import register_handlers
from app.bot.telegram_client import build_application, send_message
from app.core.logger import configure_root, get_logger
from app.services.notification_service import NotificationService
from app.storage.db import init_db

configure_root()
log = get_logger("main")


def main() -> None:
    log.info("Initialising bot system")

    # 1. Create DB tables if needed.
    init_db()

    # 2. Build the PTB Application.
    application = build_application()

    # 3. Wire notification service send function to the Telegram bot.
    notifier = NotificationService(send_fn=send_message)
    # Make the singleton accessible to RegistrationService instances.
    import app.services.notification_service as _ns_module
    _ns_module._default_notifier = notifier  # type: ignore[attr-defined]

    # 4. Register command handlers.
    register_handlers(application)

    log.info("Bot polling started — press Ctrl-C to stop")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
