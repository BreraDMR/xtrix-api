"""Add waitlist entries and time-limited offers for freed slots."""
from alembic import op
import sqlalchemy as sa

revision = "0067_waitlist"
down_revision = "0066_client_portal"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "specialist_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("preferred_date_from", sa.Date(), nullable=False),
        sa.Column("preferred_date_to", sa.Date(), nullable=True),
        sa.Column("time_preference", sa.String(16), nullable=False, server_default="any"),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("processing_consent", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_waitlist_entries_id", "waitlist_entries", ["id"])
    op.create_index("ix_waitlist_entries_business_id", "waitlist_entries", ["business_id"])
    op.create_index("ix_waitlist_entries_client_id", "waitlist_entries", ["client_id"])
    op.create_index("ix_waitlist_entries_service_id", "waitlist_entries", ["service_id"])
    op.create_index("ix_waitlist_entries_specialist_id", "waitlist_entries", ["specialist_id"])
    op.create_index("ix_waitlist_entries_email", "waitlist_entries", ["email"])
    op.create_index(
        "ix_waitlist_entries_preferred_date_from", "waitlist_entries", ["preferred_date_from"]
    )
    op.create_index(
        "ix_waitlist_entries_preferred_date_to", "waitlist_entries", ["preferred_date_to"]
    )
    op.create_index("ix_waitlist_entries_status", "waitlist_entries", ["status"])
    op.create_index("ix_waitlist_entries_created_at", "waitlist_entries", ["created_at"])
    op.create_index(
        "ix_waitlist_entries_token_hash", "waitlist_entries", ["token_hash"], unique=True
    )
    op.create_index(
        "uq_waitlist_entries_active_dedupe",
        "waitlist_entries",
        ["business_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "waitlist_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("waitlist_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "specialist_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("start", sa.DateTime(), nullable=False),
        sa.Column("end", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_waitlist_offers_id", "waitlist_offers", ["id"])
    op.create_index("ix_waitlist_offers_business_id", "waitlist_offers", ["business_id"])
    op.create_index("ix_waitlist_offers_entry_id", "waitlist_offers", ["entry_id"])
    op.create_index("ix_waitlist_offers_service_id", "waitlist_offers", ["service_id"])
    op.create_index("ix_waitlist_offers_specialist_id", "waitlist_offers", ["specialist_id"])
    op.create_index("ix_waitlist_offers_visit_id", "waitlist_offers", ["visit_id"])
    op.create_index("ix_waitlist_offers_start", "waitlist_offers", ["start"])
    op.create_index("ix_waitlist_offers_status", "waitlist_offers", ["status"])
    op.create_index("ix_waitlist_offers_expires_at", "waitlist_offers", ["expires_at"])
    op.create_index("ix_waitlist_offers_created_at", "waitlist_offers", ["created_at"])
    op.create_index(
        "ix_waitlist_offers_token_hash", "waitlist_offers", ["token_hash"], unique=True
    )
    op.create_index(
        "uq_waitlist_offers_pending_entry",
        "waitlist_offers",
        ["entry_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_waitlist_offers_event_key", "waitlist_offers", ["event_key"], unique=True
    )
    op.create_index(
        "ix_waitlist_offers_business_status", "waitlist_offers", ["business_id", "status"]
    )


def downgrade():
    op.drop_table("waitlist_offers")
    op.drop_table("waitlist_entries")
