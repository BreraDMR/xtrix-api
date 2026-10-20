"""Queue client notifications with an idempotent event key."""
from alembic import op
import sqlalchemy as sa

revision = "0060_notification_outbox"
down_revision = "0059_visit_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("language", sa.String(2), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_notification_outbox_event_key"),
    )
    op.create_index("ix_notification_outbox_business_id", "notification_outbox", ["business_id"])
    op.create_index("ix_notification_outbox_visit_id", "notification_outbox", ["visit_id"])
    op.create_index(
        "ix_notification_outbox_status_scheduled_at",
        "notification_outbox",
        ["status", "scheduled_at"],
    )
    op.create_index("ix_notification_outbox_created_at", "notification_outbox", ["created_at"])


def downgrade():
    op.drop_index("ix_notification_outbox_created_at", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_status_scheduled_at", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_visit_id", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_business_id", table_name="notification_outbox")
    op.drop_table("notification_outbox")
