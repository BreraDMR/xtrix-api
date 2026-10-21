"""Certificates and visit packages sold by one salon.

A money certificate and a visit package share the same ledger but never mix units:
money moves in Decimal, packages move in whole visits. The certificate row is a
snapshot of what was sold, and every balance is the sum of its ledger.
"""
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


OFFER_KINDS = ("money", "package")
ORDER_STATUSES = ("pending", "paid", "failed", "expired", "cancelled", "reconcile")
CERTIFICATE_STATUSES = ("active", "exhausted", "expired", "cancelled")
LEDGER_TYPES = ("issued", "redeemed", "reversed", "expired")


class GiftOffer(Base):
    __tablename__ = "gift_offers"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    kind = Column(String(16), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    face_value = Column(Numeric(10, 2), nullable=True)
    visits_count = Column(Integer, nullable=True)
    validity_days = Column(Integer, nullable=False)
    service_ids = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true", index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    business = relationship("Business", lazy="select")
    orders = relationship("GiftOrder", back_populates="offer", lazy="select")
    certificates = relationship("GiftCertificate", back_populates="offer", lazy="select")


class GiftOrder(Base):
    __tablename__ = "gift_orders"
    __table_args__ = (
        UniqueConstraint("business_id", "request_key", name="uq_gift_order_request"),
        UniqueConstraint("provider", "provider_ref", name="uq_gift_order_provider_ref"),
        UniqueConstraint("public_ref", name="uq_gift_order_public_ref"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offer_id = Column(
        Integer,
        ForeignKey("gift_offers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider = Column(String(32), nullable=False)
    merchant = Column(String(64), nullable=False)
    request_key = Column(String(80), nullable=False)
    request_hash = Column(String(64), nullable=False)
    public_ref = Column(String(48), nullable=False)
    public_token_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    provider_ref = Column(String(128), nullable=True, index=True)
    checkout_url = Column(String(512), nullable=True)
    buyer_name = Column(String(255), nullable=False)
    buyer_email = Column(String(255), nullable=False)
    recipient_name = Column(String(255), nullable=True)
    recipient_email = Column(String(255), nullable=True)
    message = Column(String(500), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    offer = relationship("GiftOffer", back_populates="orders", lazy="select")
    certificate = relationship(
        "GiftCertificate", back_populates="order", uselist=False, lazy="select"
    )
    events = relationship(
        "GiftPaymentEvent", back_populates="order", cascade="all, delete-orphan", lazy="select"
    )


class GiftCertificate(Base):
    __tablename__ = "gift_certificates"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_gift_certificate_order"),
        UniqueConstraint("code", name="uq_gift_certificate_code"),
        UniqueConstraint("code_hash", name="uq_gift_certificate_code_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offer_id = Column(
        Integer,
        ForeignKey("gift_offers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_id = Column(
        Integer,
        ForeignKey("gift_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code = Column(String(32), nullable=False)
    code_hash = Column(String(64), nullable=False)
    code_last4 = Column(String(4), nullable=False)
    kind = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False, default="active", index=True)
    issuer_name = Column(String(255), nullable=False)
    offer_name = Column(String(120), nullable=False)
    currency = Column(String(3), nullable=False)
    face_value = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    visits_total = Column(Integer, nullable=False, default=0, server_default="0")
    service_ids = Column(JSON, nullable=True)
    recipient_name = Column(String(255), nullable=False)
    recipient_email = Column(String(255), nullable=True)
    buyer_name = Column(String(255), nullable=False)
    buyer_email = Column(String(255), nullable=False)
    message = Column(String(500), nullable=True)
    issued_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    business = relationship("Business", lazy="select")
    offer = relationship("GiftOffer", back_populates="certificates", lazy="select")
    order = relationship("GiftOrder", back_populates="certificate", lazy="select")
    entries = relationship(
        "GiftLedgerEntry",
        back_populates="certificate",
        cascade="all, delete-orphan",
        order_by="GiftLedgerEntry.id",
        lazy="select",
    )


class GiftLedgerEntry(Base):
    __tablename__ = "gift_ledger_entries"
    __table_args__ = (
        UniqueConstraint("certificate_id", "request_key", name="uq_gift_ledger_request"),
        UniqueConstraint("certificate_id", "redeems_entry_id", name="uq_gift_ledger_reversal"),
    )

    id = Column(Integer, primary_key=True, index=True)
    certificate_id = Column(
        Integer,
        ForeignKey("gift_certificates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_type = Column(String(20), nullable=False, index=True)
    amount_delta = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    units_delta = Column(Integer, nullable=False, default=0, server_default="0")
    request_key = Column(String(100), nullable=False)
    visit_id = Column(
        Integer,
        ForeignKey("visits.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    redeems_entry_id = Column(
        Integer,
        ForeignKey("gift_ledger_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

    certificate = relationship(
        "GiftCertificate", back_populates="entries", lazy="select"
    )
    redeems_entry = relationship(
        "GiftLedgerEntry", remote_side=[id], lazy="select"
    )


class GiftPaymentEvent(Base):
    """Verified provider event, unique per provider and event id."""

    __tablename__ = "gift_payment_events"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_gift_payment_event_provider"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(
        Integer,
        ForeignKey("gift_orders.id", ondelete="CASCADE"),
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

    order = relationship("GiftOrder", back_populates="events", lazy="select")
