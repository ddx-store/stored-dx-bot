"""
Gmail IMAP client — uses App Password (no OAuth required).

Authenticates via imaplib with GMAIL_USER + GMAIL_APP_PASSWORD.
Searches a specific label/folder for OTP emails.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timezone
from email.header import decode_header
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import config
from app.core.logger import get_logger

log = get_logger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


class GmailAuthError(Exception):
    pass


class GmailAPIError(Exception):
    pass


def _decode_mime_words(s: str) -> str:
    """Decode encoded MIME header words."""
    parts = decode_header(s)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


class GmailClient:
    """IMAP-based Gmail client using App Password."""

    def __init__(
        self,
        user: str | None = None,
        app_password: str | None = None,
        label: str | None = None,
    ) -> None:
        self._user = user or config.GMAIL_USER
        self._password = app_password or config.GMAIL_APP_PASSWORD
        self._label = label or config.GMAIL_OTP_LABEL
        self._imap: Optional[imaplib.IMAP4_SSL] = None

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        """Open IMAP connection and login."""
        try:
            self._imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            self._imap.login(self._user, self._password)
            log.info("Gmail IMAP connected for %s", self._user)
        except imaplib.IMAP4.error as exc:
            raise GmailAuthError(
                f"Gmail IMAP login failed for {self._user}: {exc}. "
                "Make sure IMAP is enabled and the App Password is correct."
            ) from exc

    def disconnect(self) -> None:
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    def _ensure_connected(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            self.connect()
        # Re-check connection is alive (NOOP)
        try:
            self._imap.noop()
        except Exception:
            log.debug("IMAP connection lost — reconnecting")
            self._imap = None
            self.connect()
        return self._imap

    # ------------------------------------------------------------------ #
    # Public helpers used by OtpWatcher
    # ------------------------------------------------------------------ #

    def authenticate(self) -> None:
        """Alias for connect() — keeps the same interface as before."""
        self.connect()

    def get_label_id(self, label_name: str) -> Optional[str]:
        """For IMAP the 'label' is a mailbox folder name — just return it."""
        return label_name

    # ------------------------------------------------------------------ #
    # Message listing
    # ------------------------------------------------------------------ #

    def list_messages(
        self,
        label_id: Optional[str] = None,
        query: Optional[str] = None,
        max_results: int = 20,
    ) -> List[Dict[str, str]]:
        """
        Return a list of message stubs: [{"id": uid_string}, ...].
        *label_id* is the Gmail label / IMAP folder name.
        *query*    can be a 'to:email' string — parsed to IMAP SEARCH criteria.
        """
        imap = self._ensure_connected()
        folder = label_id or self._label or "INBOX"

        # Select folder (Gmail labels appear as folders in IMAP).
        # Try with and without [Gmail]/ prefix.
        for folder_name in [folder, f"[Gmail]/{folder}", f"{folder}"]:
            typ, _ = imap.select(f'"{folder_name}"', readonly=True)
            if typ == "OK":
                break
        else:
            log.warning("Could not select label folder %r — falling back to INBOX", folder)
            imap.select("INBOX", readonly=True)

        # Build IMAP search criteria.
        criteria = "ALL"
        if query:
            # Extract email address from "to:addr@example.com"
            m = re.search(r"to:(\S+)", query, re.IGNORECASE)
            if m:
                to_addr = m.group(1)
                criteria = f'TO "{to_addr}"'

        typ, data = imap.search(None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []

        uid_list = data[0].split()
        # Most recent first, limit results.
        uid_list = uid_list[-max_results:][::-1]
        return [{"id": uid.decode()} for uid in uid_list]

    # ------------------------------------------------------------------ #
    # Message fetching
    # ------------------------------------------------------------------ #

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """
        Fetch a message by IMAP sequence number and return a dict compatible
        with the existing code:
          {"payload": {"headers": [...], "parts": [...]}, "internalDate": ms}
        """
        imap = self._ensure_connected()
        typ, data = imap.fetch(message_id, "(RFC822)")
        if typ != "OK" or not data or data[0] is None:
            raise GmailAPIError(f"Failed to fetch message {message_id}")

        raw = data[0][1]
        if isinstance(raw, bytes):
            msg = email.message_from_bytes(raw)
        else:
            msg = email.message_from_string(str(raw))

        # Parse date → milliseconds since epoch.
        date_str = msg.get("Date", "")
        internal_ms = 0
        if date_str:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str)
                internal_ms = int(dt.timestamp() * 1000)
            except Exception:
                pass

        headers = [
            {"name": k, "value": str(v)}
            for k, v in msg.items()
        ]

        return {
            "payload": {
                "headers": headers,
                "raw_message": msg,
            },
            "internalDate": str(internal_ms),
        }

    # ------------------------------------------------------------------ #
    # Header / body extraction — same API as before
    # ------------------------------------------------------------------ #

    @staticmethod
    def extract_headers(message: Dict[str, Any]) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for h in message.get("payload", {}).get("headers", []):
            headers[h["name"].lower()] = _decode_mime_words(h["value"])
        return headers

    @staticmethod
    def extract_body_text(message: Dict[str, Any]) -> str:
        raw_msg = message.get("payload", {}).get("raw_message")
        if raw_msg is None:
            return ""

        def _get_text(msg) -> Optional[str]:
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            return payload.decode(charset, errors="replace")
                # Fallback: try HTML
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            text = payload.decode(charset, errors="replace")
                            # Strip basic HTML tags
                            return re.sub(r"<[^>]+>", " ", text)
                return None
            else:
                payload = raw_msg.get_payload(decode=True)
                if payload:
                    charset = raw_msg.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
                return None

        return _get_text(raw_msg) or ""

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def mark_as_read(self, message_id: str) -> None:
        """Mark an IMAP message as Seen."""
        imap = self._ensure_connected()
        try:
            imap.store(message_id, "+FLAGS", "\\Seen")
        except Exception as exc:
            log.warning("Could not mark message %s as read: %s", message_id, exc)

    def add_label(self, message_id: str, label_id: str) -> None:
        """IMAP copy to another folder (best-effort)."""
        pass
