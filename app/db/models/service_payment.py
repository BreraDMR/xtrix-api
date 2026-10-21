"""Guest prepayments for a visit, kept apart from the SaaS subscription checkout.

One attempt is one payable deposit for one booking. The ledger keeps every provider
event by its own id so a repeated webhook can never apply twice, and refunds carry a
guest-independent retry key of their own.
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


ATTEMPT_STATUSES = (
    "pending",
    "paid",
    "failed",
    "expired",
    "cancelled",
    "reconcile",
    "partially_refunded",
    "refunded",
)


class ServicePaymentAttempt(Base):
    __tablename__ = "service_payment_attempts"
    __table_args__ = (
        UniqueConstraint("business_id", "request_key", name="uq_service_payment_request"),
        UniqueConstraint("provider", "provider_ref", name="uq_service_payment_provider_ref"),
        UniqueConstraint("public_ref", name="uq_service_payment_public_ref"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visit_id = Column(
        Integer,
        ForeignKey("visits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(32), nullable=False)
    merchant = Column(String(64), nullable=False)
    request_key = Column(String(80), nullable=False)
    public_ref = Column(String(48), nullable=False)
    public_token_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    currency = Column(String(3), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    remaining_amount = Column(Numeric(10, 2), nullable=False)
    refunded_amount = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    provider_ref = Column(String(128), nullable=True, index=True)
    checkout_url = Column(String(512), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    visit = relationship("Visit", back_populates="payment_attempts", lazy="select")
    events = relationship(
        "ServicePaymentEvent",
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="ServicePaymentEvent.id",
    )
    refunds = relationship(
        "ServicePaymentRefund",
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="ServicePaymentRefund.id",
    )


class ServicePaymentEvent(Base):
    """Provider or internal event, unique per provider and event id."""

    __tablename__ = "service_payment_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_service_payment_event_provider",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(
        Integer,
        ForeignKey("service_payment_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(32), nullable=False)
    provider_event_id = Column(String(128), nullable=False)
    event_type = Column(String(40), nullable=False)
    status = Column(String(24), nullable=False)
    source = Column(String(20), nullable=False, default="webhook", server_default="webhook")
    payload_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

    attempt = relationship("ServicePaymentAttempt", back_populates="events", lazy="select")


class ServicePaymentRefund(Base):
    """One idempotent refund instruction; never a second payout for the same key."""

    __tablename__ = "service_payment_refunds"
    __table_args__ = (
        UniqueConstraint("attempt_id", "refund_key", name="uq_service_payment_refund"),
        UniqueConstraint("provider", "provider_refund_ref", name="uq_service_payment_refund_ref"),
    )

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(
        Integer,
        ForeignKey("service_payment_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(32), nullable=False)
    refund_key = Column(String(80), nullable=False)
    provider_refund_ref = Column(String(128), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    reason = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="succeeded", server_default="succeeded")
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

    attempt = relationship("ServicePaymentAttempt", back_populates="refunds", lazy="select")
