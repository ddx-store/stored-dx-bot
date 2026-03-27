"""
OTP email watcher — IMAP polling version.

Polls the configured Gmail label for new OTP emails every
OTP_POLL_INTERVAL_SECONDS seconds until an OTP is found or the
OTP_TIMEOUT_SECONDS deadline is reached.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

from app.core.config import config
from app.core.logger import get_logger
from app.gmail.gmail_client import GmailClient, GmailAPIError, GmailAuthError
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
        self._connected = False

    def wait_for_otp(self, job: Job) -> OtpMessage:
        """
        Block until an OTP email arrives for *job* or the timeout expires.
        Returns the matched OtpMessage with otp_value or link_value set.
        Raises OtpTimeout on expiry.
        """
        self._ensure_connected()
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

    def _ensure_connected(self) -> None:
        if not self._connected:
            try:
                self._gmail.connect()
                self._connected = True
            except GmailAuthError as exc:
                log.error("Gmail IMAP auth failed: %s", exc)
                raise

    def _ensure_label(self) -> None:
        if self._label_id is None:
            self._label_id = config.GMAIL_OTP_LABEL

    def _poll_once(self, job: Job) -> Optional[OtpMessage]:
        """Fetch recent messages from the label, parse, and try to match."""
        try:
            stubs = self._gmail.list_messages(
                label_id=self._label_id,
                query=f"to:{job.email}",
                max_results=10,
            )
        except (GmailAPIError, Exception) as exc:
            log.error("Gmail list_messages failed: %s", exc)
            self._connected = False
            try:
                self._gmail.connect()
                self._connected = True
            except Exception:
                pass
            return None

        if not stubs:
            log.debug("No messages found in label %r for %s", self._label_id, job.email)
            return None

        candidates: list[OtpMessage] = []

        for stub in stubs:
            raw_msg_id = stub["id"]
            unique_key = f"{self._label_id}:{raw_msg_id}"
            if self._otp_repo.is_processed(unique_key):
                continue

            try:
                full = self._gmail.get_message(raw_msg_id)
            except Exception as exc:
                log.warning("Could not fetch message %s: %s", raw_msg_id, exc)
                continue

            headers = self._gmail.extract_headers(full)
            body = self._gmail.extract_body_text(full)

            if not body:
                log.debug("Empty body for message %s — skipping", raw_msg_id)
                continue

            otp_code, otp_type, link = extract_otp(body)

            internal_ms = int(full.get("internalDate", 0))
            received_at: Optional[datetime] = None
            if internal_ms:
                received_at = datetime.fromtimestamp(
                    internal_ms / 1000, tz=timezone.utc
                )

            to_header = headers.get("to", "")
            rcpt_match = re.search(r"[\w.+\-]+@[\w.\-]+", to_header)
            recipient = rcpt_match.group(0) if rcpt_match else job.email

            otp_msg = OtpMessage(
                gmail_message_id=unique_key,
                sender=headers.get("from"),
                subject=headers.get("subject"),
                recipient=recipient,
                received_at=received_at,
                otp_value=otp_code,
                otp_type=otp_type,
                link_value=link,
            )
            otp_msg = self._otp_repo.save(otp_msg)
            candidates.append(otp_msg)

        if not candidates:
            return None

        match = match_otp_message(job, candidates)
        if match:
            self._otp_repo.mark_processed(match.gmail_message_id, job.job_id)
            actual_msg_id = match.gmail_message_id.split(":", 1)[-1] if ":" in match.gmail_message_id else match.gmail_message_id
            try:
                self._gmail.mark_as_read(actual_msg_id)
            except Exception as exc:
                log.warning("Could not mark message as read: %s", exc)
            return match

        return None
