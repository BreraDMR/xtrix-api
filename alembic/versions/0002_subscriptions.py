"""subscriptions
Revision ID: 0002_subscriptions
Revises: 0001_init
Create Date: 2024-01-02 00:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0002_subscriptions"
down_revision = "0001_init"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String, unique=True, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("period_days", sa.Integer, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("currency", sa.String, server_default="usd"),
        sa.Column("features", sa.String, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("subscription_plans.id"), nullable=False),
        sa.Column("status", sa.String, server_default="active"),
        sa.Column("provider", sa.String, nullable=True),
        sa.Column("external_id", sa.String, nullable=True),
        sa.Column("current_period_end", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_subscriptions_user", "subscriptions", ["user_id"])
def downgrade():
    op.drop_table("subscriptions")
    op.drop_table("subscription_plans")
