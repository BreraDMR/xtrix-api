"""refresh tokens
Revision ID: 0004_refresh_tokens
Revises: 0003_schedule_blocks
Create Date: 2024-01-04 00:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0004_refresh_tokens"
down_revision = "0003_schedule_blocks"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String, nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("is_revoked", sa.Boolean, default=False, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_refresh_tokens_token", "refresh_tokens", ["token"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
def downgrade():
    op.drop_index("ix_refresh_tokens_expires_at", "refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", "refresh_tokens")
    op.drop_index("ix_refresh_tokens_token", "refresh_tokens")
    op.drop_table("refresh_tokens")
