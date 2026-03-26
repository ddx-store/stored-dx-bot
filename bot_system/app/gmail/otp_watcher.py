"""
OTP email watcher.

Polls the configured Gmail label for new OTP emails and stores parsed
results in the database. The main entry point for a job is
``wait_for_otp(job)``, which blocks (with polling) until an OTP is
found or the timeout is exceeded.

Design notes:
- All fetched messages are persisted to avoid re-processing.
- Matching is delegated to matcher.py so this module stays focused on
  fetching and parsing.
- Push notification support can be layered on top by replacing the poll
  loop with a Cloud Pub/Sub handler that calls _process_messages().
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from app.core.config import config
from app.core.logger import get_logger
from app.gmail.gmail_client import GmailClient, GmailAPIError
from app.gmail.matcher import match_otp_message
from app.gmail.parser import extract_otp
from app.storage.db import get_connection
from app.storage.models import Job, OtpMessage
from app.storage.repositories import OtpMessageRepository

log = get_logger(__name__)


class OtpTimeout(Exception):
    """Raised when the OTP was not received within the configured timeout."""


class OtpWatcher:
    def __init__(self, gmail: GmailClient | None = None) -> None:
        self._gmail = gmail or GmailClient()
        self._conn = get_connection()
        self._otp_repo = OtpMessageRepository(self._conn)
        self._label_id: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def wait_for_otp(self, job: Job) -> OtpMessage:
        """
        Block until an OTP email arrives for *job* or the timeout expires.

        Returns the matched OtpMessage (with otp_value or link_value set).
        Raises OtpTimeout on expiry.
        """
        self._ensure_authenticated()
        self._ensure_label()

        deadline = time.monotonic() + config.OTP_TIMEOUT_SECONDS
        poll_interval = config.OTP_POLL_INTERVAL_SECONDS

        log.info(
            "Waiting for OTP: job=%s email=%s timeout=%ds",
            job.job_id, job.email, config.OTP_TIMEOUT_SECONDS,
        )

        while time.monotonic() < deadline:
            match = self._poll_once(job)
            if match:
                return match
            remaining = int(deadline - time.monotonic())
            log.debug(
                "No OTP yet for %s — sleeping %ds (remaining=%ds)",
                job.email, poll_interval, remaining,
            )
            time.sleep(poll_interval)

        raise OtpTimeout(
            f"OTP not received within {config.OTP_TIMEOUT_SECONDS}s for job {job.job_id}"
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _ensure_authenticated(self) -> None:
        self._gmail.authenticate()

    def _ensure_label(self) -> None:
        if self._label_id is not None:
            return
        label_name = config.GMAIL_OTP_LABEL
        self._label_id = self._gmail.get_label_id(label_name)
        if not self._label_id:
            log.warning(
                "Gmail label %r not found — will search inbox without label filter",
                label_name,
            )

    def _poll_once(self, job: Job) -> Optional[OtpMessage]:
        """Fetch recent messages, parse them, and try to match one to *job*."""
        try:
            stubs = self._gmail.list_messages(
                label_id=self._label_id,
                query=f"to:{job.email}",
                max_results=10,
            )
        except GmailAPIError as exc:
            log.error("Gmail list_messages failed: %s", exc)
            return None

        candidates: list[OtpMessage] = []

        for stub in stubs:
            msg_id = stub["id"]
            if self._otp_repo.is_processed(msg_id):
                continue

            try:
                full = self._gmail.get_message(msg_id)
            except GmailAPIError as exc:
                log.warning("Could not fetch message %s: %s", msg_id, exc)
                continue

            headers = self._gmail.extract_headers(full)
            body = self._gmail.extract_body_text(full)

            otp_code, otp_type, link = extract_otp(body)

            # Parse received date from internalDate (milliseconds since epoch).
            internal_ms = int(full.get("internalDate", 0))
            received_at: Optional[datetime] = None
            if internal_ms:
                received_at = datetime.fromtimestamp(
                    internal_ms / 1000, tz=timezone.utc
                )

            otp_msg = OtpMessage(
                gmail_message_id=msg_id,
                sender=headers.get("from"),
                subject=headers.get("subject"),
                recipient=headers.get("to") or job.email,
                received_at=received_at,
                otp_value=otp_code,
                otp_type=otp_type,
                link_value=link,
            )
            otp_msg = self._otp_repo.save(otp_msg)
            candidates.append(otp_msg)

        match = match_otp_message(job, candidates)
        if match:
            self._otp_repo.mark_processed(match.gmail_message_id, job.job_id)
            self._gmail.mark_as_read(match.gmail_message_id)
            return match

        return None
