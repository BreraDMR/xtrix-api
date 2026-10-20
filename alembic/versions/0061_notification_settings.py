"""Store the notification channel, reminder time and switch per business."""
from alembic import op
import sqlalchemy as sa

revision = "0061_notification_settings"
down_revision = "0060_notification_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
        sa.Column("reminder_time", sa.String(5), nullable=False, server_default="10:00"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_notification_settings_business_id",
        "notification_settings",
        ["business_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_notification_settings_business_id", table_name="notification_settings")
    op.drop_table("notification_settings")
