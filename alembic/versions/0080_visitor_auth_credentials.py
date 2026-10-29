"""Add separate visitor challenges and opaque session digests."""
from alembic import op
import sqlalchemy as sa

revision = "0080_visitor_auth_credentials"
down_revision = "0079_visitor_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "visitor_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("email_canonical", sa.String(320), nullable=True),
        sa.Column("origin", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_visitor_challenges_token_hash"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_visitor_challenges_token_hash"),
        sa.CheckConstraint("purpose IN ('register','reset','email_change','google_login','google_link')",
                           name="ck_visitor_challenges_purpose"),
        sa.CheckConstraint("""
            (purpose = 'register' AND account_id IS NULL AND email_canonical IS NOT NULL AND origin IS NULL)
         OR (purpose IN ('reset','email_change') AND account_id IS NOT NULL AND email_canonical IS NOT NULL AND origin IS NULL)
         OR (purpose = 'google_login' AND account_id IS NULL AND email_canonical IS NULL AND origin IS NOT NULL)
         OR (purpose = 'google_link' AND account_id IS NOT NULL AND email_canonical IS NULL AND origin IS NOT NULL)
        """, name="ck_visitor_challenges_scope"),
        sa.CheckConstraint("email_canonical IS NULL OR (length(email_canonical) > 0 AND email_canonical = btrim(email_canonical))",
                           name="ck_visitor_challenges_email"),
        sa.CheckConstraint("origin IS NULL OR (length(origin) > 0 AND origin = btrim(origin) AND origin !~ '[[:space:]]')",
                           name="ck_visitor_challenges_origin"),
        sa.CheckConstraint("expires_at > created_at", name="ck_visitor_challenges_expiry"),
    )
    op.create_index("ix_visitor_challenges_account_id", "visitor_challenges", ["account_id"])
    op.create_index("ix_visitor_challenges_expires_at", "visitor_challenges", ["expires_at"])
    op.create_index("ix_visitor_challenges_email_purpose_created", "visitor_challenges", ["email_canonical", "purpose", "created_at"])
    op.create_table(
        "visitor_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_visitor_sessions_token_hash"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_visitor_sessions_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_visitor_sessions_expiry"),
    )
    op.create_index("ix_visitor_sessions_account_id", "visitor_sessions", ["account_id"])
    op.create_index("ix_visitor_sessions_expires_at", "visitor_sessions", ["expires_at"])


def downgrade():
    op.drop_index("ix_visitor_sessions_expires_at", table_name="visitor_sessions")
    op.drop_index("ix_visitor_sessions_account_id", table_name="visitor_sessions")
    op.drop_table("visitor_sessions")
    op.drop_index("ix_visitor_challenges_email_purpose_created", table_name="visitor_challenges")
    op.drop_index("ix_visitor_challenges_expires_at", table_name="visitor_challenges")
    op.drop_index("ix_visitor_challenges_account_id", table_name="visitor_challenges")
    op.drop_table("visitor_challenges")
