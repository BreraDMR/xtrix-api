"""One explicit price/currency correction of a visit that already had money or bonuses on it."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class VisitPriceCorrection(Base):
    __tablename__ = 'visit_price_corrections'
    __table_args__ = (
        UniqueConstraint('visit_id', 'idempotency_key', name='uq_visit_price_correction_key'),
        CheckConstraint("prior_payment_action IN ('refund_due', 'retained')", name='ck_visit_price_correction_action'),
        CheckConstraint('new_total >= 0 AND (old_total IS NULL OR old_total >= 0)', name='ck_visit_price_correction_totals'),
        CheckConstraint("length(request_hash) = 64 AND length(btrim(reason)) >= 3", name='ck_visit_price_correction_text'),
        Index('ix_visit_price_corrections_visit', 'visit_id', 'id'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False)
    visit_id = Column(Integer, ForeignKey('visits.id', ondelete='RESTRICT'), nullable=False)
    actor_user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    idempotency_key = Column(String(80), nullable=False)
    request_hash = Column(String(64), nullable=False)
    old_currency = Column(String(3), nullable=True)
    old_total = Column(Numeric(12, 2), nullable=True)
    old_items = Column(JSONB, nullable=False)
    new_currency = Column(String(3), nullable=False)
    new_total = Column(Numeric(12, 2), nullable=False)
    new_items = Column(JSONB, nullable=False)
    settlement = Column(JSONB, nullable=False)
    prior_payment_action = Column(String(16), nullable=False)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False)
