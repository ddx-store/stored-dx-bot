"""
Unit tests for the job manager using an in-memory SQLite database.
Run: pytest tests/test_job_manager.py -v
"""

import sys
import os
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("SITE_API_BASE_URL", "https://example.com/api/v1")

import pytest
from app.core.enums import JobStatus
from app.storage.db import init_db, _SCHEMA
from app.storage.repositories import JobRepository, AuditRepository
from app.core.utils import new_job_id, utcnow
from app.storage.models import Job


@pytest.fixture
def conn():
    """In-memory SQLite connection with schema applied."""
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.commit()
    return c


def _manager(conn):
    """Create a JobRepository + AuditRepository pair sharing one connection."""
    return JobRepository(conn), AuditRepository(conn)


def test_create_and_get_job(conn):
    repo, audit = _manager(conn)
    job = Job(job_id=new_job_id(), email="test@example.com", created_at=utcnow(), updated_at=utcnow(), chat_id=42)
    repo.create(job)
    fetched = repo.get(job.job_id)
    assert fetched is not None
    assert fetched.email == "test@example.com"
    assert fetched.status == JobStatus.PENDING


def test_transition_status(conn):
    repo, _ = _manager(conn)
    job = Job(job_id=new_job_id(), email="a@b.com", created_at=utcnow(), updated_at=utcnow())
    repo.create(job)
    repo.update_status(job.job_id, JobStatus.CREATING_ACCOUNT)
    fetched = repo.get(job.job_id)
    assert fetched.status == JobStatus.CREATING_ACCOUNT


def test_fail_job(conn):
    repo, _ = _manager(conn)
    job = Job(job_id=new_job_id(), email="fail@test.com", created_at=utcnow(), updated_at=utcnow())
    repo.create(job)
    repo.update_status(job.job_id, JobStatus.FAILED, error_msg="Test failure reason")
    fetched = repo.get(job.job_id)
    assert fetched.status == JobStatus.FAILED
    assert "Test failure reason" in (fetched.error_msg or "")


def test_complete_job(conn):
    repo, _ = _manager(conn)
    job = Job(job_id=new_job_id(), email="ok@test.com", created_at=utcnow(), updated_at=utcnow())
    repo.create(job)
    repo.update_status(job.job_id, JobStatus.COMPLETED, final_result="done")
    fetched = repo.get(job.job_id)
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.final_result == "done"


def test_list_recent(conn):
    repo, _ = _manager(conn)
    for i in range(3):
        job = Job(job_id=new_job_id(), email=f"user{i}@test.com", created_at=utcnow(), updated_at=utcnow())
        repo.create(job)
    jobs = repo.list_recent(limit=5)
    assert len(jobs) == 3


def test_increment_otp_attempts(conn):
    repo, _ = _manager(conn)
    job = Job(job_id=new_job_id(), email="otp@test.com", created_at=utcnow(), updated_at=utcnow())
    repo.create(job)
    count = repo.increment_otp_attempts(job.job_id)
    assert count == 1
    count = repo.increment_otp_attempts(job.job_id)
    assert count == 2
