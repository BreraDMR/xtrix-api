"""Visitor identity stays separate from CRM users and salon client records."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import relationship, validates

from app.api.core.clock import utcnow
from app.db.base import Base

GOOGLE_ISSUER = "https://accounts.google.com"


def canonical_visitor_email(value: str) -> str:
    """The same key must be used by future signup, login and email-change services."""
    canonical = value.strip().casefold()
    if not canonical or len(canonical) > 320:
        raise ValueError("invalid visitor email length")
    return canonical


class VisitorAccount(Base):
    __tablename__ = "visitor_accounts"
    __table_args__ = (
        UniqueConstraint("email_canonical", name="uq_visitor_accounts_email_canonical"),
        CheckConstraint("length(email_canonical) > 0 AND email_canonical = btrim(email_canonical)",
                        name="ck_visitor_accounts_email_nonempty_trimmed"),
        CheckConstraint("hashed_password IS NULL OR length(hashed_password) > 0",
                        name="ck_visitor_accounts_password_nonempty"),
    )

    id = Column(Integer, primary_key=True)
    email_canonical = Column(String(320), nullable=False)
    # Account creation happens after proof. Pending signup belongs in the later challenge table.
    email_verified_at = Column(DateTime, nullable=False)
    hashed_password = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow,
                        server_default=text("timezone('utc', now())"))

    google_identities = relationship("VisitorGoogleIdentity", back_populates="account",
                                     cascade="all, delete-orphan", passive_deletes=True)

    @validates("email_canonical")
    def _canonical_email(self, _key, value):
        return canonical_visitor_email(value)


class VisitorGoogleIdentity(Base):
    __tablename__ = "visitor_google_identities"
    __table_args__ = (
        CheckConstraint("issuer = 'https://accounts.google.com'", name="ck_visitor_google_identity_issuer"),
        CheckConstraint("length(subject) > 0 AND subject = btrim(subject)",
                        name="ck_visitor_google_identity_subject"),
        Index("ix_visitor_google_identities_account_id", "account_id"),
    )

    issuer = Column(String(255), primary_key=True, default=GOOGLE_ISSUER)
    subject = Column(String(255), primary_key=True)
    account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow,
                        server_default=text("timezone('utc', now())"))

    account = relationship("VisitorAccount", back_populates="google_identities")
