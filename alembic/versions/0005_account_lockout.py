"""account lockout fields
Revision ID: 0005_account_lockout
Revises: 0004_refresh_tokens
Create Date: 2024-01-05 00:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0005_account_lockout"
down_revision = "0004_refresh_tokens"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer, default=0, nullable=False))
    op.add_column("users", sa.Column("locked_until", sa.DateTime, nullable=True))
    op.add_column("users", sa.Column("is_locked", sa.Boolean, default=False, nullable=False))
    op.create_index("ix_users_is_locked", "users", ["is_locked"])
def downgrade():
    op.drop_index("ix_users_is_locked", "users")
    op.drop_column("users", "is_locked")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
