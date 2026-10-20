"""Record guest-driven visit lifecycle events once."""
from alembic import op
import sqlalchemy as sa

revision = "0059_visit_events"
down_revision = "0058_visit_client_confirmation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "visit_events",
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
            sa.ForeignKey("visits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(20), nullable=False, server_default="client"),
        sa.Column("event_key", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_visit_event_key"),
    )
    op.create_index("ix_visit_events_business_id", "visit_events", ["business_id"])
    op.create_index("ix_visit_events_visit_id", "visit_events", ["visit_id"])
    op.create_index("ix_visit_events_event_type", "visit_events", ["event_type"])
    op.create_index("ix_visit_events_created_at", "visit_events", ["created_at"])


def downgrade():
    op.drop_index("ix_visit_events_created_at", table_name="visit_events")
    op.drop_index("ix_visit_events_event_type", table_name="visit_events")
    op.drop_index("ix_visit_events_visit_id", table_name="visit_events")
    op.drop_index("ix_visit_events_business_id", table_name="visit_events")
    op.drop_table("visit_events")
