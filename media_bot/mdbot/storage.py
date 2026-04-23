"""SQLite-backed stats / audit storage."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any


class Stats:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    platform TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    url TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    size_bytes INTEGER,
                    duration_ms INTEGER,
                    error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_ts ON downloads(ts)"
            )

    def record(
        self,
        *,
        user_id: int,
        username: str | None,
        platform: str,
        kind: str,
        url: str,
        success: bool,
        size_bytes: int | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO downloads
                    (ts, user_id, username, platform, kind, url, success,
                     size_bytes, duration_ms, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    user_id,
                    username,
                    platform,
                    kind,
                    url,
                    1 if success else 0,
                    size_bytes,
                    duration_ms,
                    error,
                ),
            )

    def summary(self) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), SUM(success) FROM downloads")
            total, successes = cur.fetchone()
            total = total or 0
            successes = successes or 0

            cur.execute(
                "SELECT COUNT(DISTINCT user_id) FROM downloads"
            )
            (unique_users,) = cur.fetchone()

            cur.execute(
                """
                SELECT platform, COUNT(*) AS n, SUM(success) AS ok
                FROM downloads
                GROUP BY platform
                ORDER BY n DESC
                LIMIT 20
                """
            )
            by_platform = [
                {"platform": p, "total": n, "success": int(ok or 0)}
                for p, n, ok in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT user_id, username, COUNT(*) AS n
                FROM downloads
                GROUP BY user_id
                ORDER BY n DESC
                LIMIT 10
                """
            )
            top_users = [
                {"user_id": uid, "username": uname, "count": n}
                for uid, uname, n in cur.fetchall()
            ]

        return {
            "total": total,
            "success": successes,
            "failure": total - successes,
            "unique_users": unique_users or 0,
            "by_platform": by_platform,
            "top_users": top_users,
        }

    def recent_errors(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ts, user_id, platform, url, error
                FROM downloads
                WHERE success = 0
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "ts": ts,
                    "user_id": uid,
                    "platform": p,
                    "url": url,
                    "error": err,
                }
                for ts, uid, p, url, err in cur.fetchall()
            ]
