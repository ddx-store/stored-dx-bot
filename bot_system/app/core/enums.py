"""
Shared enumerations used across the system.
"""

from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    CREATING_ACCOUNT = "creating_account"
    WAITING_FOR_OTP = "waiting_for_otp"
    VERIFYING_OTP = "verifying_otp"
    COMPLETED = "completed"
    FAILED = "failed"


class IntegrationMode(str, Enum):
    API = "api"
    PLAYWRIGHT = "playwright"


class OtpType(str, Enum):
    NUMERIC = "numeric"
    LINK = "link"
    UNKNOWN = "unknown"
