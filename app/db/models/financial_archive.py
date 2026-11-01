"""Saved monthly documents and their retryable, non-financial work queue."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

from app.api.core.clock import utcnow
from app.db.base import Base


PAYLOAD_SCOPE_CHECK = """COALESCE(jsonb_typeof(payload) = 'object'
    AND payload->>'business_id' = business_id::text
    AND payload->>'schema_version' = schema_version::text
    AND payload#>>'{period,year}' = year::text
    AND payload#>>'{period,month}' = month::text, FALSE)"""


class FinancialArchive(Base):
    __tablename__ = 'financial_archives'
    __table_args__ = (
        UniqueConstraint('business_id', 'year', 'month', name='uq_financial_archive_period'),
        CheckConstraint('year BETWEEN 1 AND 9998 AND month BETWEEN 1 AND 12', name='ck_financial_archive_period'),
        CheckConstraint('schema_version = 2', name='ck_financial_archive_version'),
        CheckConstraint(PAYLOAD_SCOPE_CHECK, name='ck_financial_archive_payload_scope'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False)
    payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow,
                        server_default=text("timezone('UTC', now())"))


class FinancialArchiveJob(Base):
    __tablename__ = 'financial_archive_jobs'
    __table_args__ = (
        UniqueConstraint('business_id', 'year', 'month', name='uq_financial_archive_job_period'),
        CheckConstraint('year BETWEEN 1 AND 9998 AND month BETWEEN 1 AND 12', name='ck_financial_archive_job_period'),
        CheckConstraint("status IN ('pending', 'done', 'cancelled')", name='ck_financial_archive_job_status'),
        CheckConstraint('attempts >= 0', name='ck_financial_archive_job_attempts'),
        CheckConstraint("(status = 'pending' AND next_attempt_at IS NOT NULL) OR "
                        "(status IN ('done', 'cancelled') AND next_attempt_at IS NULL)",
                        name='ck_financial_archive_job_schedule'),
        Index('ix_financial_archive_jobs_due', 'status', 'next_attempt_at'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default='pending')
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
