"""Explicit marketing permission per business and client.

A booking, a visit or a client card is not consent. Only an explicit opt-in creates the row;
the source and every revocation stay here so the client record itself is never overwritten.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base

CONSENT_STATUSES = ("subscribed", "unsubscribed")
CONSENT_CHANNELS = ("email",)


class MarketingConsent(Base):
    __tablename__ = "marketing_consents"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "client_id", "channel", name="uq_marketing_consent_client_channel"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id = Column(
        Integer,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = Column(String(20), nullable=False, default="email", server_default="email")
    status = Column(
        String(16), nullable=False, default="subscribed", server_default="subscribed", index=True
    )
    source = Column(String(32), nullable=False)
    source_ref = Column(String(64), nullable=True)
    revoked_source = Column(String(32), nullable=True)
    revoked_campaign_id = Column(
        Integer,
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subscribed_at = Column(DateTime, nullable=False, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    client = relationship("Client", lazy="select")
    business = relationship("Business", lazy="select")
    revoked_campaign = relationship("Campaign", lazy="select")
