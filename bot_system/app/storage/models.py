"""
Pure-Python dataclasses representing the data model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.enums import JobStatus, JobType, OtpType
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
class CardInfo:
    number: str
    expiry_month: str
    expiry_year: str
    cvv: str
    holder_name: str
    billing_zip: str = ""
    billing_country: str = "US"


@dataclass
class PaymentJob:
    job_id: str
    site_url: str
    email: str
    password: str
    plan_name: str = ""
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    error_msg: Optional[str] = None
    final_result: Optional[str] = None
    chat_id: Optional[int] = None
    message_id: Optional[int] = None
    is_bulk: bool = False
    card_last4: str = ""


@dataclass
class AuditLog:
    event: str
    detail: Optional[str] = None
    job_id: Optional[str] = None
    created_at: datetime = field(default_factory=utcnow)
    id: Optional[int] = None


@dataclass
class SavedAccount:
    chat_id: int
    site_url: str
    email: str
    password: str = ""
    job_type: str = "registration"
    plan_name: str = ""
    detail: Optional[str] = None
    created_at: datetime = field(default_factory=utcnow)
    id: Optional[int] = None


@dataclass
class Proxy:
    proxy_url: str
    label: str = ""
    active: bool = True
    added_at: datetime = field(default_factory=utcnow)
    id: Optional[int] = None


@dataclass
class BulkSession:
    """In-memory only — tracks a bulk payment run (multiple cards, one account)."""
    bulk_id: str
    chat_id: int
    site_url: str
    email: str
    password: str
    plan_name: str = ""
    billing_country: str = "US"
    cards: list = field(default_factory=list)
    results: list = field(default_factory=list)
    total: int = 0
    done: int = 0
    success_count: int = 0
