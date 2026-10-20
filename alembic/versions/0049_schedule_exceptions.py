"""Add per-date schedule exceptions."""
from alembic import op
import sqlalchemy as sa

revision = "0049_schedule_exceptions"
down_revision = "0048_business_schedule_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedule_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("specialist_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("exception_date", sa.Date(), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.UniqueConstraint("specialist_id", "exception_date", name="uq_schedule_exception_specialist_date"),
        sa.CheckConstraint(
            "(is_closed = true AND start_time IS NULL AND end_time IS NULL)"
            " OR (is_closed = false AND start_time IS NOT NULL AND end_time IS NOT NULL"
            " AND start_time < end_time)",
            name="ck_schedule_exception_window",
        ),
    )
    op.create_index("ix_schedule_exceptions_specialist_id", "schedule_exceptions", ["specialist_id"])
    op.create_index("ix_schedule_exceptions_exception_date", "schedule_exceptions", ["exception_date"])


def downgrade():
    op.drop_index("ix_schedule_exceptions_exception_date", table_name="schedule_exceptions")
    op.drop_index("ix_schedule_exceptions_specialist_id", table_name="schedule_exceptions")
    op.drop_table("schedule_exceptions")
