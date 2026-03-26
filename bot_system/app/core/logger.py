"""
Structured logging setup using Python's standard logging module.
Outputs JSON-like structured lines to stdout so logs are easy to grep
and forward to external systems.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from app.core.config import config

# Use a simple, readable format for development; swap for JSON in prod.
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return a named logger with the configured level."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    logger.setLevel(level or config.LOG_LEVEL)
    # Prevent log records from bubbling up to the root logger.
    logger.propagate = False
    return logger


def configure_root() -> None:
    """Call once at startup to configure the root logger."""
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format=_FORMAT,
        stream=sys.stdout,
    )
    # Silence noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
