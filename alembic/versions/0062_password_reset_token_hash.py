"""Store password reset tokens as SHA-256 digests and drop the old raw token/code.

Rows written before this migration contain raw tokens and 6-digit codes, which is unsafe.
They are deleted while the table is still unreachable from any endpoint, so no legacy
credential can survive the switch. Downgrade restores the old column shape (without data).
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_password_reset_token_hash"
down_revision = "0061_notification_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "password_reset_tokens",
        sa.Column("token_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
    op.execute("DELETE FROM password_reset_tokens")
    op.alter_column("password_reset_tokens", "token_hash", nullable=False)

    op.drop_index("ix_password_reset_tokens_token", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_code", table_name="password_reset_tokens")
    op.drop_column("password_reset_tokens", "code")
    op.drop_column("password_reset_tokens", "token")


def downgrade():
    op.add_column("password_reset_tokens", sa.Column("token", sa.String(), nullable=True))
    op.add_column("password_reset_tokens", sa.Column("code", sa.String(), nullable=True))
    op.create_index(
        "ix_password_reset_tokens_token", "password_reset_tokens", ["token"], unique=True
    )
    op.create_index("ix_password_reset_tokens_code", "password_reset_tokens", ["code"])
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_column("password_reset_tokens", "token_hash")
