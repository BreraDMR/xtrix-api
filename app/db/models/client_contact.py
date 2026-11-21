"""Every phone number and email a client can be reached at.

`clients.contact_phone` / `contact_email` stay as the primary values so older code and API
consumers keep working; app/api/core/client_contacts.py keeps the two in sync.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base

MAX_PHONES = 5
MAX_EMAILS = 3
CONTACT_KINDS = ("phone", "email")


class ClientContact(Base):
    __tablename__ = "client_contacts"
    __table_args__ = (
        UniqueConstraint("client_id", "kind", "value", name="uq_client_contacts_value"),
        CheckConstraint("kind IN ('phone','email')", name="ck_client_contacts_kind"),
        CheckConstraint("length(value) > 0", name="ck_client_contacts_value"),
        Index(
            "uq_client_contacts_primary",
            "client_id",
            "kind",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_client_contacts_lookup", "kind", "value"),
    )

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(8), nullable=False)
    # phones are stored as +digits, emails lowercased - lookups compare these as-is
    value = Column(String(255), nullable=False)
    label = Column(String(50), nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    notify = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=text("timezone('utc', now())"))

    client = relationship("Client", back_populates="contacts")
