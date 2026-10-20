"""Add preparation and cleanup buffers to services."""
from alembic import op
import sqlalchemy as sa

revision = "0050_service_buffers"
down_revision = "0049_schedule_exceptions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("services", sa.Column(
        "buffer_before_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("services", sa.Column(
        "buffer_after_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_service_buffers_non_negative",
        "services",
        "buffer_before_minutes >= 0 AND buffer_after_minutes >= 0",
    )


def downgrade():
    op.drop_constraint("ck_service_buffers_non_negative", "services", type_="check")
    op.drop_column("services", "buffer_after_minutes")
    op.drop_column("services", "buffer_before_minutes")
