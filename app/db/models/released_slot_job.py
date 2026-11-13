"""Local matching work, saved atomically with a released booking segment."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from app.db.base import Base


class ReleasedSlotJob(Base):
    __tablename__ = 'released_slot_jobs'
    __table_args__ = (
        UniqueConstraint('event_key', 'segment', name='uq_released_slot_event_segment'),
        CheckConstraint('segment >= 0 AND attempts >= 0', name='ck_released_slot_counters'),
        CheckConstraint('service_id > 0 AND specialist_id > 0', name='ck_released_slot_target'),
        CheckConstraint("status IN ('pending', 'done', 'expired', 'cancelled')", name='ck_released_slot_status'),
        CheckConstraint("(status = 'pending' AND next_attempt_at IS NOT NULL) OR "
                        "(status <> 'pending' AND next_attempt_at IS NULL)", name='ck_released_slot_schedule'),
        Index('ix_released_slot_jobs_due', 'status', 'next_attempt_at'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False)
    event_key = Column(String(120), nullable=False)
    segment = Column(Integer, nullable=False)
    service_id = Column(Integer, nullable=False)
    specialist_id = Column(Integer, nullable=False)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False)
    attempts = Column(Integer, nullable=False)
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(String(40), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
