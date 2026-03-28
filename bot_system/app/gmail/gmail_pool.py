"""
GmailPool — multi-account Gmail pool for OTP fetching.
Distributes OTP requests across multiple Gmail accounts to avoid
IMAP rate-limiting when processing many concurrent jobs.
Supports round-robin and least-loaded selection strategies.
"""
from __future__ import annotations

import threading
from typing import List, Optional

from app.core.config import config
from app.core.logger import get_logger
from app.gmail.gmail_client import GmailClient
from app.gmail.otp_watcher import OtpWatcher, OtpTimeout
from app.storage.models import Job, OtpMessage

log = get_logger(__name__)


class GmailPool:
    """
    Pool of GmailClient instances.
    Config: GMAIL_ACCOUNTS env var as JSON array:
        [{"user": "a@gmail.com", "app_password": "xxxx"},
         {"user": "b@gmail.com", "app_password": "yyyy"}]
    Falls back to single GMAIL_USER + GMAIL_APP_PASSWORD if not set.
    """

    def __init__(self) -> None:
        self._clients: List[GmailClient] = []
        self._usage: dict[int, int] = {}
        self._lock = threading.Lock()
        self._init_clients()

    def _init_clients(self) -> None:
        import json, os
        raw = os.environ.get("GMAIL_ACCOUNTS", "")
        if raw:
            try:
                accounts = json.loads(raw)
                for acc in accounts:
                    client = GmailClient(
                        user=acc.get("user"),
                        app_password=acc.get("app_password"),
                    )
                    self._clients.append(client)
                log.info("GmailPool: loaded %d accounts from GMAIL_ACCOUNTS", len(self._clients))
            except Exception as exc:
                log.warning("GmailPool: failed to parse GMAIL_ACCOUNTS: %s", exc)

        if not self._clients:
            self._clients.append(GmailClient())
            log.info("GmailPool: using single default Gmail account")

        for i in range(len(self._clients)):
            self._usage[i] = 0

    def _pick_client(self, email: str) -> tuple[int, GmailClient]:
        """Pick least-loaded client (load balanced)."""
        with self._lock:
            idx = min(self._usage, key=self._usage.get)
            self._usage[idx] += 1
            return idx, self._clients[idx]

    def _release_client(self, idx: int) -> None:
        with self._lock:
            self._usage[idx] = max(0, self._usage.get(idx, 0) - 1)

    def wait_for_otp(self, job: Job) -> OtpMessage:
        """Fetch OTP for job using the least-loaded Gmail account."""
        idx, client = self._pick_client(job.email)
        try:
            watcher = OtpWatcher(gmail=client)
            return watcher.wait_for_otp(job)
        finally:
            self._release_client(idx)

    @property
    def size(self) -> int:
        return len(self._clients)


gmail_pool = GmailPool()
