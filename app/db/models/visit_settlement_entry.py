"""One explicit money fact on a visit: a charge, or a manual receipt/refund with its exact amount."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint

from app.db.base import Base

SETTLEMENT_ENTRY_KINDS = ('charge', 'manual_receipt', 'manual_refund')


class VisitSettlementEntry(Base):
    __tablename__ = 'visit_settlement_entries'
    __table_args__ = (
        UniqueConstraint('visit_id', 'idempotency_key', name='uq_visit_settlement_entry_key'),
        CheckConstraint("kind IN ('charge', 'manual_receipt', 'manual_refund')", name='ck_visit_settlement_entry_kind'),
        CheckConstraint('amount > 0', name='ck_visit_settlement_entry_amount'),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_visit_settlement_entry_currency'),
        CheckConstraint("length(request_hash) = 64 AND length(btrim(reason)) >= 3", name='ck_visit_settlement_entry_text'),
        Index('ix_visit_settlement_entries_visit', 'visit_id', 'id'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False)
    visit_id = Column(Integer, ForeignKey('visits.id', ondelete='RESTRICT'), nullable=False)
    actor_user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    kind = Column(String(16), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    reason = Column(String(500), nullable=False)
    reference = Column(String(120), nullable=True)
    idempotency_key = Column(String(80), nullable=False)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
