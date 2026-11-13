"""One visitor proof mail waiting for (or done with) delivery. The link itself is encrypted."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint

from app.db.base import Base


class VisitorProofMailJob(Base):
    __tablename__ = 'visitor_proof_mail_jobs'
    __table_args__ = (
        UniqueConstraint('challenge_id', name='uq_visitor_proof_mail_challenge'),
        CheckConstraint("purpose IN ('register', 'reset', 'email_change')", name='ck_visitor_proof_mail_purpose'),
        CheckConstraint("status IN ('pending', 'sending', 'sent', 'captured', 'expired', 'cancelled', 'failed')",
                        name='ck_visitor_proof_mail_status'),
        CheckConstraint('attempts >= 0', name='ck_visitor_proof_mail_attempts'),
        CheckConstraint("(status IN ('pending', 'sending') AND key_id IS NOT NULL AND nonce IS NOT NULL "
                        "AND ciphertext IS NOT NULL) OR (status NOT IN ('pending', 'sending') AND key_id IS NULL "
                        "AND nonce IS NULL AND ciphertext IS NULL)", name='ck_visitor_proof_mail_secret'),
        CheckConstraint("(status = 'pending' AND next_attempt_at IS NOT NULL AND lease_ticket IS NULL) OR "
                        "(status = 'sending' AND lease_until IS NOT NULL AND lease_ticket IS NOT NULL) OR "
                        "(status NOT IN ('pending', 'sending') AND next_attempt_at IS NULL AND lease_ticket IS NULL)",
                        name='ck_visitor_proof_mail_schedule'),
        Index('ix_visitor_proof_mail_due', 'status', 'next_attempt_at'),
    )
    id = Column(Integer, primary_key=True)
    challenge_id = Column(Integer, ForeignKey('visitor_challenges.id', ondelete='SET NULL'), nullable=True)
    purpose = Column(String(16), nullable=False)
    locale = Column(String(5), nullable=False)
    origin = Column(String(255), nullable=False)
    key_id = Column(String(32), nullable=True)
    nonce = Column(LargeBinary, nullable=True)
    ciphertext = Column(LargeBinary, nullable=True)
    status = Column(String(16), nullable=False)
    attempts = Column(Integer, nullable=False)
    next_attempt_at = Column(DateTime, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    lease_ticket = Column(String(32), nullable=True)
    deadline = Column(DateTime, nullable=False)
    last_error = Column(String(40), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
