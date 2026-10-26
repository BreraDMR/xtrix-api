"""Explicit visitor access, independent of mutable CRM contact and staff ownership."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import validates

from app.api.core.clock import utcnow
from app.db.base import Base
from app.db.models.visitor_account import canonical_visitor_email


class VisitorGrant(Base):
    __tablename__ = "visitor_grants"
    __table_args__ = (
        CheckConstraint("scope IN ('visit','client')", name="ck_visitor_grants_scope"),
        CheckConstraint("(scope = 'visit' AND visit_id IS NOT NULL) OR (scope = 'client' AND visit_id IS NULL)",
                        name="ck_visitor_grants_target"),
        Index("uq_visitor_grant_active_visit", "business_id", "visit_id", unique=True,
              postgresql_where=text("revoked_at IS NULL AND scope = 'visit'")),
        Index("uq_visitor_grant_active_client", "business_id", "client_id", unique=True,
              postgresql_where=text("revoked_at IS NULL AND scope = 'client'")),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    # These IDs are snapshots. Future resolvers must check the target still matches them.
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = Column(String(16), nullable=False)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=text("timezone('utc', now())"))
    revoked_at = Column(DateTime, nullable=True)


class VisitorClaim(Base):
    __tablename__ = "visitor_claims"
    __table_args__ = (
        UniqueConstraint("proof_hash", name="uq_visitor_claims_proof_hash"),
        CheckConstraint("proof_hash ~ '^[0-9a-f]{64}$'", name="ck_visitor_claims_proof_hash"),
        CheckConstraint("kind IN ('manage_visit','client_invite')", name="ck_visitor_claims_kind"),
        CheckConstraint("""
            (kind = 'manage_visit' AND visit_id IS NOT NULL AND recipient_email IS NULL
                AND issued_by_user_id IS NULL AND consumed_at IS NOT NULL)
         OR (kind = 'client_invite' AND visit_id IS NULL AND recipient_email IS NOT NULL
                AND source_manage_token_id IS NULL)
        """, name="ck_visitor_claims_scope"),
        CheckConstraint("recipient_email IS NULL OR (length(recipient_email) > 0 AND recipient_email = btrim(recipient_email))",
                        name="ck_visitor_claims_recipient"),
        CheckConstraint("expires_at > created_at", name="ck_visitor_claims_expiry"),
        CheckConstraint("consumed_at IS NOT NULL OR (consumed_account_id IS NULL AND grant_id IS NULL)",
                        name="ck_visitor_claims_consumption"),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(String(24), nullable=False)
    proof_hash = Column(String(64), nullable=False)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="CASCADE"), nullable=True, index=True)
    recipient_email = Column(String(320), nullable=True)
    source_manage_token_id = Column(Integer, ForeignKey("booking_manage_tokens.id", ondelete="SET NULL"), nullable=True, index=True)
    issued_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=text("timezone('utc', now())"))
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    # Deleting an owner or grant never clears consumed_at: the proof stays spent.
    consumed_at = Column(DateTime, nullable=True)
    consumed_account_id = Column(Integer, ForeignKey("visitor_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    grant_id = Column(Integer, ForeignKey("visitor_grants.id", ondelete="SET NULL"), nullable=True, index=True)

    @validates("recipient_email")
    def _canonical_recipient(self, _key, value):
        return None if value is None else canonical_visitor_email(value)
