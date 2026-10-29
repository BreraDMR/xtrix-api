"""Add explicit visitor grants and hash-only claim receipts/invitations."""
from alembic import op
import sqlalchemy as sa

revision = "0081_visitor_access"
down_revision = "0080_visitor_auth_credentials"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "visitor_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("visit_id", sa.Integer(), sa.ForeignKey("visits.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("scope IN ('visit','client')", name="ck_visitor_grants_scope"),
        sa.CheckConstraint("(scope = 'visit' AND visit_id IS NOT NULL) OR (scope = 'client' AND visit_id IS NULL)",
                           name="ck_visitor_grants_target"),
    )
    for column in ("account_id", "business_id", "client_id", "visit_id"):
        op.create_index(f"ix_visitor_grants_{column}", "visitor_grants", [column])
    op.create_index("uq_visitor_grant_active_visit", "visitor_grants", ["business_id", "visit_id"],
                    unique=True, postgresql_where=sa.text("revoked_at IS NULL AND scope = 'visit'"))
    op.create_index("uq_visitor_grant_active_client", "visitor_grants", ["business_id", "client_id"],
                    unique=True, postgresql_where=sa.text("revoked_at IS NULL AND scope = 'client'"))
    op.create_table(
        "visitor_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("proof_hash", sa.String(64), nullable=False),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visit_id", sa.Integer(), sa.ForeignKey("visits.id", ondelete="CASCADE"), nullable=True),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("source_manage_token_id", sa.Integer(), sa.ForeignKey("booking_manage_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issued_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("grant_id", sa.Integer(), sa.ForeignKey("visitor_grants.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("proof_hash", name="uq_visitor_claims_proof_hash"),
        sa.CheckConstraint("proof_hash ~ '^[0-9a-f]{64}$'", name="ck_visitor_claims_proof_hash"),
        sa.CheckConstraint("kind IN ('manage_visit','client_invite')", name="ck_visitor_claims_kind"),
        sa.CheckConstraint("""
            (kind = 'manage_visit' AND visit_id IS NOT NULL AND recipient_email IS NULL
                AND issued_by_user_id IS NULL AND consumed_at IS NOT NULL)
         OR (kind = 'client_invite' AND visit_id IS NULL AND recipient_email IS NOT NULL
                AND source_manage_token_id IS NULL)
        """, name="ck_visitor_claims_scope"),
        sa.CheckConstraint("recipient_email IS NULL OR (length(recipient_email) > 0 AND recipient_email = btrim(recipient_email))",
                           name="ck_visitor_claims_recipient"),
        sa.CheckConstraint("expires_at > created_at", name="ck_visitor_claims_expiry"),
        sa.CheckConstraint("consumed_at IS NOT NULL OR (consumed_account_id IS NULL AND grant_id IS NULL)",
                           name="ck_visitor_claims_consumption"),
    )
    for column in ("business_id", "client_id", "visit_id", "source_manage_token_id", "issued_by_user_id",
                   "expires_at", "consumed_account_id", "grant_id"):
        op.create_index(f"ix_visitor_claims_{column}", "visitor_claims", [column])


def downgrade():
    op.drop_table("visitor_claims")
    op.drop_table("visitor_grants")
