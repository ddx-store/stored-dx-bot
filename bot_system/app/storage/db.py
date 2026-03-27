"""
SQLite database initialisation.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Generator

from app.core.config import config
from app.core.logger import get_logger

log = get_logger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    site_url     TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    error_msg    TEXT,
    otp_attempts INTEGER NOT NULL DEFAULT 0,
    final_result TEXT,
    chat_id      INTEGER,
    message_id   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_email  ON jobs (email);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS otp_messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT REFERENCES jobs(job_id),
    gmail_message_id TEXT NOT NULL UNIQUE,
    sender           TEXT,
    subject          TEXT,
    recipient        TEXT,
    received_at      TEXT,
    otp_value        TEXT,
    otp_type         TEXT,
    link_value       TEXT,
    processed        INTEGER NOT NULL DEFAULT 0,
    processed_at     TEXT,
    matched          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_otp_job   ON otp_messages (job_id);
CREATE INDEX IF NOT EXISTS idx_otp_gmail ON otp_messages (gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_otp_rcpt  ON otp_messages (recipient);

CREATE TABLE IF NOT EXISTS results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT REFERENCES jobs(job_id),
    success    INTEGER NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_logs (job_id);

CREATE TABLE IF NOT EXISTS payment_jobs (
    job_id       TEXT PRIMARY KEY,
    site_url     TEXT NOT NULL,
    email        TEXT NOT NULL,
    password     TEXT NOT NULL DEFAULT '',
    plan_name    TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    error_msg    TEXT,
    final_result TEXT,
    chat_id      INTEGER,
    message_id   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pjobs_email  ON payment_jobs (email);
CREATE INDEX IF NOT EXISTS idx_pjobs_status ON payment_jobs (status);

CREATE TABLE IF NOT EXISTS saved_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    site_url     TEXT NOT NULL,
    email        TEXT NOT NULL,
    password     TEXT NOT NULL DEFAULT '',
    job_type     TEXT NOT NULL DEFAULT 'registration',
    plan_name    TEXT NOT NULL DEFAULT '',
    detail       TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_chat ON saved_accounts (chat_id);
"""

# Migration: add site_url column to existing databases that don't have it.
_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN site_url TEXT NOT NULL DEFAULT ''",
]


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def get_connection(path: str | None = None) -> sqlite3.Connection:
    db_path = path or config.DB_PATH
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: str | None = None) -> None:
    """Create all tables if they do not exist, then run pending migrations."""
    db_path = path or config.DB_PATH
    _ensure_dir(db_path)
    conn = get_connection(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        # Run migrations (ignore errors for already-applied ones).
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists
        log.info("Database initialised at %s", db_path)
    finally:
        conn.close()
