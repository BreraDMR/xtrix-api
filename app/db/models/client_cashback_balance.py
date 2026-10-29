"""Known currency wallets; the legacy untyped balance stays separate."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from app.api.core.clock import utcnow
from app.db.base import Base


class ClientCashbackBalance(Base):
    __tablename__ = 'client_cashback_balances'
    __table_args__ = (
        UniqueConstraint('business_id', 'client_id', 'currency', name='uq_client_cashback_currency'),
        CheckConstraint('''currency COLLATE "C" ~ '^[A-Z]{3}$' ''', name='ck_cashback_wallet_currency'),
        CheckConstraint('balance >= 0 AND balance <= 9999999999999999.99', name='ck_cashback_wallet_balance'),
    )
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey('clients.id', ondelete='CASCADE'), nullable=False, index=True)
    currency = Column(String(3), nullable=False)
    balance = Column(Numeric(18, 2), nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
