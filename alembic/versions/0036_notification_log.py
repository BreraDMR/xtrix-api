"""add_notification_log
Revision ID: 0036_notification_log
Revises: 0035_drop_spending
Create Date: 2026-10-17 21:00:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0036_notification_log"
down_revision = "0035_drop_spending"
branch_labels = None
depends_on = None
def upgrade():
    # one row per message actually sent, so scheduled jobs can run as often as they like
    # without sending the same reminder/greeting twice
    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "key", name="uq_notification_log_kind_key"),
    )
def downgrade():
    op.drop_table("notification_log")
