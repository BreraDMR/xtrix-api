"""Add hashed visitor-portal magic links and client sessions."""
from alembic import op
import sqlalchemy as sa

revision = "0066_client_portal"
down_revision = "0065_subscription_offer_code"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_portal_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_client_portal_tokens_id", "client_portal_tokens", ["id"])
    op.create_index("ix_client_portal_tokens_business_id", "client_portal_tokens", ["business_id"])
    op.create_index("ix_client_portal_tokens_client_id", "client_portal_tokens", ["client_id"])
    op.create_index("ix_client_portal_tokens_expires_at", "client_portal_tokens", ["expires_at"])
    op.create_index(
        "ix_client_portal_tokens_token_hash",
        "client_portal_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "client_portal_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_client_portal_sessions_id", "client_portal_sessions", ["id"])
    op.create_index("ix_client_portal_sessions_business_id", "client_portal_sessions", ["business_id"])
    op.create_index("ix_client_portal_sessions_client_id", "client_portal_sessions", ["client_id"])
    op.create_index("ix_client_portal_sessions_expires_at", "client_portal_sessions", ["expires_at"])
    op.create_index(
        "ix_client_portal_sessions_token_hash",
        "client_portal_sessions",
        ["token_hash"],
        unique=True,
    )


def downgrade():
    op.drop_table("client_portal_sessions")
    op.drop_table("client_portal_tokens")
