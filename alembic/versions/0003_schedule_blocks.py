"""work schedules and block slots

Revision ID: 0003_schedule_blocks
Revises: 0002_subscriptions
Create Date: 2024-01-03 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_schedule_blocks"
down_revision = "0002_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "work_schedules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("specialist_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("weekday", sa.Integer, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
    )
    op.create_index("ix_work_schedules_specialist", "work_schedules", ["specialist_id"])
    op.create_index("ix_work_schedules_weekday", "work_schedules", ["weekday"])

    op.create_table(
        "block_slots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("specialist_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("start", sa.DateTime, nullable=False),
        sa.Column("end", sa.DateTime, nullable=False),
        sa.Column("reason", sa.String, nullable=True),
    )
    op.create_index("ix_block_slots_specialist", "block_slots", ["specialist_id"])
    op.create_index("ix_block_slots_start", "block_slots", ["start"])


def downgrade():
    op.drop_table("block_slots")
    op.drop_table("work_schedules")











