"""
OTP service — thin wrapper around OtpWatcher for use by other services.
Exists so OTP logic can be mocked independently in tests.
"""

from __future__ import annotations

from app.gmail.otp_watcher import OtpWatcher
from app.storage.models import Job, OtpMessage


class OtpService:
    def __init__(self, watcher: OtpWatcher | None = None) -> None:
        self._watcher = watcher or OtpWatcher()

    def wait_for_otp(self, job: Job) -> OtpMessage:
        return self._watcher.wait_for_otp(job)
