"""
Gmail API client.

Wraps the Google API Python client in a thin, typed layer so the rest of
the codebase never touches google-api-python-client directly.

Authentication uses OAuth2 credentials stored locally in a token file.
On first run the user must authorise the app via the browser flow.
"""

from __future__ import annotations

import base64
import os
from email import message_from_bytes
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import config
from app.core.logger import get_logger

log = get_logger(__name__)

# Read-only scope is all we need.
_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailAuthError(Exception):
    pass


class GmailAPIError(Exception):
    pass


class GmailClient:
    """Authenticated Gmail API client."""

    def __init__(
        self,
        credentials_file: str | None = None,
        token_file: str | None = None,
    ) -> None:
        self._credentials_file = credentials_file or config.GMAIL_CREDENTIALS_FILE
        self._token_file = token_file or config.GMAIL_TOKEN_FILE
        self._service: Any = None

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #

    def authenticate(self) -> None:
        """Load or refresh credentials and build the service object."""
        creds: Optional[Credentials] = None

        if os.path.exists(self._token_file):
            creds = Credentials.from_authorized_user_file(self._token_file, _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log.info("Refreshing Gmail token")
                creds.refresh(Request())
            else:
                if not os.path.exists(self._credentials_file):
                    raise GmailAuthError(
                        f"Gmail credentials file not found: {self._credentials_file}. "
                        "Download it from the Google Cloud Console (OAuth 2.0 client ID)."
                    )
                log.info("Starting Gmail OAuth flow — check your browser")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self._credentials_file, _SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(self._token_file, "w") as fh:
                fh.write(creds.to_json())
            log.info("Gmail token saved to %s", self._token_file)

        self._service = build("gmail", "v1", credentials=creds)
        log.info("Gmail API service ready")

    @property
    def service(self) -> Any:
        if self._service is None:
            self.authenticate()
        return self._service

    # ------------------------------------------------------------------ #
    # Label helpers
    # ------------------------------------------------------------------ #

    def get_label_id(self, label_name: str) -> Optional[str]:
        """Resolve a label name to its Gmail label ID."""
        try:
            result = self.service.users().labels().list(userId="me").execute()
            for lbl in result.get("labels", []):
                if lbl["name"].lower() == label_name.lower():
                    return lbl["id"]
        except HttpError as exc:
            raise GmailAPIError(f"Failed to list labels: {exc}") from exc
        return None

    # ------------------------------------------------------------------ #
    # Message listing
    # ------------------------------------------------------------------ #

    def list_messages(
        self,
        label_id: Optional[str] = None,
        query: Optional[str] = None,
        max_results: int = 20,
    ) -> List[Dict[str, str]]:
        """Return up to *max_results* message stubs matching *label_id* / *query*."""
        try:
            params: Dict[str, Any] = {
                "userId": "me",
                "maxResults": max_results,
            }
            if label_id:
                params["labelIds"] = [label_id]
            if query:
                params["q"] = query
            result = self.service.users().messages().list(**params).execute()
            return result.get("messages", [])
        except HttpError as exc:
            raise GmailAPIError(f"Failed to list messages: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Message fetching
    # ------------------------------------------------------------------ #

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """Fetch the full message payload for *message_id*."""
        try:
            return (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            raise GmailAPIError(f"Failed to get message {message_id}: {exc}") from exc

    def get_message_raw(self, message_id: str) -> bytes:
        """Fetch the raw RFC 2822 bytes of a message."""
        try:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
            return base64.urlsafe_b64decode(msg["raw"])
        except HttpError as exc:
            raise GmailAPIError(f"Failed to get raw message {message_id}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Header / body helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def extract_headers(message: Dict[str, Any]) -> Dict[str, str]:
        """Return a flat dict of header name → value (lower-cased names)."""
        headers: Dict[str, str] = {}
        for h in message.get("payload", {}).get("headers", []):
            headers[h["name"].lower()] = h["value"]
        return headers

    @staticmethod
    def extract_body_text(message: Dict[str, Any]) -> str:
        """Return the plain-text body of a message (best effort)."""
        payload = message.get("payload", {})

        def _decode(data: str) -> str:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

        def _find_text(part: Dict[str, Any]) -> Optional[str]:
            mime = part.get("mimeType", "")
            body = part.get("body", {})
            if mime == "text/plain" and body.get("data"):
                return _decode(body["data"])
            for sub in part.get("parts", []):
                result = _find_text(sub)
                if result:
                    return result
            return None

        text = _find_text(payload)
        if text:
            return text

        # Fallback: decode raw bytes with email lib.
        try:
            raw = GmailClient.extract_body_raw(message)
            parsed = message_from_bytes(raw)
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            pass
        return ""

    @staticmethod
    def extract_body_raw(message: Dict[str, Any]) -> bytes:
        """Return the raw body bytes decoded from the message snippet."""
        payload = message.get("payload", {})
        body = payload.get("body", {})
        data = body.get("data", "")
        return base64.urlsafe_b64decode(data + "==") if data else b""

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def mark_as_read(self, message_id: str) -> None:
        """Remove the UNREAD label from *message_id*."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        except HttpError as exc:
            log.warning("Could not mark message %s as read: %s", message_id, exc)

    def add_label(self, message_id: str, label_id: str) -> None:
        """Add *label_id* to *message_id*."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": [label_id]},
            ).execute()
        except HttpError as exc:
            log.warning("Could not add label to message %s: %s", message_id, exc)
