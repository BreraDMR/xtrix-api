"""Only digests of visitor credentials are persisted; scopes stay explicit."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import relationship, validates

from app.api.core.clock import utcnow
from app.db.base import Base
from app.db.models.visitor_account import canonical_visitor_email

CHALLENGE_PURPOSES = ("register", "reset", "email_change", "google_login", "google_link")
CHALLENGE_SCOPE_CHECK = """
    (purpose = 'register' AND account_id IS NULL AND email_canonical IS NOT NULL AND origin IS NULL)
 OR (purpose IN ('reset','email_change') AND account_id IS NOT NULL AND email_canonical IS NOT NULL AND origin IS NULL)
 OR (purpose = 'google_login' AND account_id IS NULL AND email_canonical IS NULL AND origin IS NOT NULL)
 OR (purpose = 'google_link' AND account_id IS NOT NULL AND email_canonical IS NULL AND origin IS NOT NULL)
"""


class VisitorChallenge(Base):
    __tablename__ = "visitor_challenges"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_visitor_challenges_token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_visitor_challenges_token_hash"),
        CheckConstraint("purpose IN ('register','reset','email_change','google_login','google_link')",
                        name="ck_visitor_challenges_purpose"),
        CheckConstraint(CHALLENGE_SCOPE_CHECK, name="ck_visitor_challenges_scope"),
        CheckConstraint("email_canonical IS NULL OR (length(email_canonical) > 0 AND email_canonical = btrim(email_canonical))",
                        name="ck_visitor_challenges_email"),
        CheckConstraint("origin IS NULL OR (length(origin) > 0 AND origin = btrim(origin) AND origin !~ '[[:space:]]')",
                        name="ck_visitor_challenges_origin"),
        CheckConstraint("expires_at > created_at", name="ck_visitor_challenges_expiry"),
        Index("ix_visitor_challenges_account_id", "account_id"),
        Index("ix_visitor_challenges_expires_at", "expires_at"),
        Index("ix_visitor_challenges_email_purpose_created", "email_canonical", "purpose", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    purpose = Column(String(32), nullable=False)
    token_hash = Column(String(64), nullable=False)
    account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=True)
    email_canonical = Column(String(320), nullable=True)
    origin = Column(String(2048), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow,
                        server_default=text("timezone('utc', now())"))
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)

    account = relationship("VisitorAccount")

    @validates("email_canonical")
    def _canonical_email(self, _key, value):
        return None if value is None else canonical_visitor_email(value)


class VisitorSession(Base):
    __tablename__ = "visitor_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_visitor_sessions_token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_visitor_sessions_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_visitor_sessions_expiry"),
        Index("ix_visitor_sessions_account_id", "account_id"),
        Index("ix_visitor_sessions_expires_at", "expires_at"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow,
                        server_default=text("timezone('utc', now())"))
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    account = relationship("VisitorAccount")
