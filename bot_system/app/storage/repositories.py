"""
Repository layer — all SQLite read/write operations.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Optional

from app.core.enums import JobStatus, OtpType
from app.core.logger import get_logger
from app.core.utils import utcnow
from app.storage.db import get_connection
from app.storage.models import AuditLog, Job, OtpMessage, Result, SavedAccount

log = get_logger(__name__)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        email=row["email"],
        site_url=row["site_url"] if "site_url" in row.keys() else "",
        status=JobStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        error_msg=row["error_msg"],
        otp_attempts=row["otp_attempts"],
        final_result=row["final_result"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
    )


def _row_to_otp(row: sqlite3.Row) -> OtpMessage:
    return OtpMessage(
        id=row["id"],
        job_id=row["job_id"],
        gmail_message_id=row["gmail_message_id"],
        sender=row["sender"],
        subject=row["subject"],
        recipient=row["recipient"],
        received_at=datetime.fromisoformat(row["received_at"]) if row["received_at"] else None,
        otp_value=row["otp_value"],
        otp_type=OtpType(row["otp_type"]) if row["otp_type"] else OtpType.UNKNOWN,
        link_value=row["link_value"],
        processed=bool(row["processed"]),
        processed_at=datetime.fromisoformat(row["processed_at"]) if row["processed_at"] else None,
        matched=bool(row["matched"]),
    )


class JobRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_connection()

    def create(self, job: Job) -> Job:
        now = utcnow().isoformat()
        self._conn.execute(
            """
            INSERT INTO jobs
                (job_id, email, site_url, status, created_at, updated_at,
                 error_msg, otp_attempts, final_result, chat_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id, job.email, job.site_url, job.status.value,
                now, now,
                job.error_msg, job.otp_attempts, job.final_result,
                job.chat_id, job.message_id,
            ),
        )
        self._conn.commit()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return _row_to_job(row) if row else None

    def get_by_email(self, email: str) -> Optional[Job]:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE email = ? ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        return _row_to_job(row) if row else None

    def list_recent(self, limit: int = 10) -> List[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        error_msg: Optional[str] = None,
        final_result: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE jobs
            SET status = ?, error_msg = ?, final_result = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (status.value, error_msg, final_result, utcnow().isoformat(), job_id),
        )
        self._conn.commit()

    def increment_otp_attempts(self, job_id: str) -> int:
        self._conn.execute(
            "UPDATE jobs SET otp_attempts = otp_attempts + 1, updated_at = ? WHERE job_id = ?",
            (utcnow().isoformat(), job_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT otp_attempts FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row["otp_attempts"] if row else 0


class OtpMessageRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_connection()

    def save(self, msg: OtpMessage) -> OtpMessage:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO otp_messages
                (job_id, gmail_message_id, sender, subject, recipient,
                 received_at, otp_value, otp_type, link_value,
                 processed, matched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg.job_id, msg.gmail_message_id, msg.sender, msg.subject,
                msg.recipient,
                msg.received_at.isoformat() if msg.received_at else None,
                msg.otp_value, msg.otp_type.value, msg.link_value,
                int(msg.processed), int(msg.matched),
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM otp_messages WHERE gmail_message_id = ?",
            (msg.gmail_message_id,),
        ).fetchone()
        msg.id = row["id"] if row else None
        return msg

    def mark_processed(self, gmail_message_id: str, job_id: str) -> None:
        self._conn.execute(
            """
            UPDATE otp_messages
            SET processed = 1, processed_at = ?, matched = 1, job_id = ?
            WHERE gmail_message_id = ?
            """,
            (utcnow().isoformat(), job_id, gmail_message_id),
        )
        self._conn.commit()

    def is_processed(self, gmail_message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT processed FROM otp_messages WHERE gmail_message_id = ?",
            (gmail_message_id,),
        ).fetchone()
        return bool(row["processed"]) if row else False

    def find_unprocessed_for_email(
        self, recipient: str, after: Optional[datetime] = None
    ) -> List[OtpMessage]:
        if after:
            rows = self._conn.execute(
                """
                SELECT * FROM otp_messages
                WHERE recipient = ? AND processed = 0
                  AND (received_at IS NULL OR received_at >= ?)
                ORDER BY received_at DESC
                """,
                (recipient, after.isoformat()),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM otp_messages
                WHERE recipient = ? AND processed = 0
                ORDER BY received_at DESC
                """,
                (recipient,),
            ).fetchall()
        return [_row_to_otp(r) for r in rows]


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_connection()

    def log(self, event: str, detail: Optional[str] = None, job_id: Optional[str] = None) -> None:
        self._conn.execute(
            "INSERT INTO audit_logs (job_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
            (job_id, event, detail, utcnow().isoformat()),
        )
        self._conn.commit()

    def recent(self, job_id: Optional[str] = None, limit: int = 50) -> List[AuditLog]:
        if job_id:
            rows = self._conn.execute(
                "SELECT * FROM audit_logs WHERE job_id = ? ORDER BY created_at DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AuditLog(
                id=r["id"], job_id=r["job_id"], event=r["event"],
                detail=r["detail"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]


class ResultRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_connection()

    def save(self, result: Result) -> None:
        self._conn.execute(
            "INSERT INTO results (job_id, success, detail, created_at) VALUES (?, ?, ?, ?)",
            (result.job_id, int(result.success), result.detail, result.created_at.isoformat()),
        )
        self._conn.commit()


class SavedAccountRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_connection()

    def save(self, account: SavedAccount) -> None:
        self._conn.execute(
            """
            INSERT INTO saved_accounts
                (chat_id, site_url, email, password, job_type, plan_name, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.chat_id, account.site_url, account.email,
                account.password, account.job_type, account.plan_name,
                account.detail, account.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def list_by_chat(self, chat_id: int, limit: int = 20) -> List[SavedAccount]:
        rows = self._conn.execute(
            "SELECT * FROM saved_accounts WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [
            SavedAccount(
                id=r["id"], chat_id=r["chat_id"], site_url=r["site_url"],
                email=r["email"], password=r["password"], job_type=r["job_type"],
                plan_name=r["plan_name"], detail=r["detail"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def delete_by_id(self, account_id: int, chat_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM saved_accounts WHERE id = ? AND chat_id = ?",
            (account_id, chat_id),
        )
        self._conn.commit()
        return cur.rowcount > 0


class CleanupRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_connection()

    def delete_old_jobs(self, days: int) -> int:
        cutoff = (utcnow() - __import__("datetime").timedelta(days=days)).isoformat()
        c1 = self._conn.execute(
            "DELETE FROM jobs WHERE created_at < ? AND status IN ('completed', 'failed', 'cancelled')",
            (cutoff,),
        ).rowcount
        c2 = self._conn.execute(
            "DELETE FROM payment_jobs WHERE created_at < ? AND status IN ('completed', 'failed', 'cancelled')",
            (cutoff,),
        ).rowcount
        c3 = self._conn.execute(
            "DELETE FROM audit_logs WHERE created_at < ?", (cutoff,)
        ).rowcount
        c4 = self._conn.execute(
            "DELETE FROM results WHERE created_at < ?", (cutoff,)
        ).rowcount
        self._conn.commit()
        total = c1 + c2 + c3 + c4
        if total > 0:
            log.info("Cleanup: deleted %d old records (jobs=%d, pjobs=%d, audit=%d, results=%d)",
                     total, c1, c2, c3, c4)
        return total
