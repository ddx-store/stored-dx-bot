"""
Pure-Python dataclasses representing the data model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.enums import JobStatus, OtpType
from app.core.utils import utcnow


@dataclass
class Job:
    job_id: str
    email: str
    site_url: str = ""
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    error_msg: Optional[str] = None
    otp_attempts: int = 0
    final_result: Optional[str] = None
    chat_id: Optional[int] = None
    message_id: Optional[int] = None


@dataclass
class OtpMessage:
    gmail_message_id: str
    recipient: str
    sender: Optional[str] = None
    subject: Optional[str] = None
    received_at: Optional[datetime] = None
    otp_value: Optional[str] = None
    otp_type: OtpType = OtpType.UNKNOWN
    link_value: Optional[str] = None
    processed: bool = False
    processed_at: Optional[datetime] = None
    matched: bool = False
    job_id: Optional[str] = None
    id: Optional[int] = None


@dataclass
class Result:
    job_id: str
    success: bool
    detail: Optional[str] = None
    created_at: datetime = field(default_factory=utcnow)
    id: Optional[int] = None


@dataclass
class AuditLog:
    event: str
    detail: Optional[str] = None
    job_id: Optional[str] = None
    created_at: datetime = field(default_factory=utcnow)
    id: Optional[int] = None
