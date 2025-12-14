"""add 2fa fields

Revision ID: 0009_add_2fa
Revises: 0008_optimization_soft_delete
Create Date: 2024-01-09 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_add_2fa"
down_revision = "0008_optimization_soft_delete"
branch_labels = None
depends_on = None


def upgrade():
    # Add 2FA fields to users table
    op.add_column("users", sa.Column("totp_secret", sa.String, nullable=True))
    op.add_column("users", sa.Column("is_2fa_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("users", "is_2fa_enabled")
    op.drop_column("users", "totp_secret")





