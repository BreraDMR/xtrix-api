"""ai_usage
Revision ID: 0042_ai_usage
Revises: 0041_service_description
Create Date: 2026-10-18 12:00:00
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0042_ai_usage"
down_revision = "0041_service_description"
branch_labels = None
depends_on = None


def upgrade():
    # a plain log: no foreign keys on purpose. Rows outlive users and businesses, and dropping an FK
    # would take an exclusive lock on users/businesses.
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="fast"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_estimate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(now() at time zone 'utc')")),
    )
    op.create_index("ix_ai_usage_id", "ai_usage", ["id"])
    op.create_index("ix_ai_usage_business_created", "ai_usage", ["business_id", "created_at"])
    op.create_index("ix_ai_usage_user_created", "ai_usage", ["user_id", "created_at"])


def downgrade():
    op.drop_index("ix_ai_usage_user_created", table_name="ai_usage")
    op.drop_index("ix_ai_usage_business_created", table_name="ai_usage")
    op.drop_index("ix_ai_usage_id", table_name="ai_usage")
    op.drop_table("ai_usage")
