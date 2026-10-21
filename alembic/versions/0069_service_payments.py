"""Deposit policy for public booking plus the service payment ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0069_service_payments"
down_revision = "0068_visit_items"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "public_booking_settings",
        sa.Column("deposit_type", sa.String(16), nullable=False, server_default="none"),
    )
    op.add_column(
        "public_booking_settings",
        sa.Column("deposit_value", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "public_booking_settings",
        sa.Column("deposit_hold_minutes", sa.Integer(), nullable=False, server_default="30"),
    )

    op.create_table(
        "service_payment_attempts",
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
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("merchant", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("public_ref", sa.String(48), nullable=False),
        sa.Column("public_token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("remaining_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "refunded_amount",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("provider_ref", sa.String(128), nullable=True),
        sa.Column("checkout_url", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("business_id", "request_key", name="uq_service_payment_request"),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_service_payment_provider_ref"),
        sa.UniqueConstraint("public_ref", name="uq_service_payment_public_ref"),
    )
    op.create_index(
        "ix_service_payment_attempts_id", "service_payment_attempts", ["id"]
    )
    op.create_index(
        "ix_service_payment_attempts_business_id",
        "service_payment_attempts",
        ["business_id"],
    )
    op.create_index(
        "ix_service_payment_attempts_visit_id",
        "service_payment_attempts",
        ["visit_id"],
    )
    op.create_index(
        "ix_service_payment_attempts_status",
        "service_payment_attempts",
        ["status"],
    )
    op.create_index(
        "ix_service_payment_attempts_provider_ref",
        "service_payment_attempts",
        ["provider_ref"],
    )
    op.create_index(
        "ix_service_payment_attempts_expires_at",
        "service_payment_attempts",
        ["expires_at"],
    )
    op.create_index(
        "ix_service_payment_attempts_created_at",
        "service_payment_attempts",
        ["created_at"],
    )

    op.create_table(
        "service_payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Integer(),
            sa.ForeignKey("service_payment_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="webhook"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_service_payment_event_provider",
        ),
    )
    op.create_index("ix_service_payment_events_id", "service_payment_events", ["id"])
    op.create_index(
        "ix_service_payment_events_attempt_id",
        "service_payment_events",
        ["attempt_id"],
    )
    op.create_index(
        "ix_service_payment_events_business_id",
        "service_payment_events",
        ["business_id"],
    )
    op.create_index(
        "ix_service_payment_events_created_at",
        "service_payment_events",
        ["created_at"],
    )

    op.create_table(
        "service_payment_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Integer(),
            sa.ForeignKey("service_payment_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("refund_key", sa.String(80), nullable=False),
        sa.Column("provider_refund_ref", sa.String(128), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="succeeded"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("attempt_id", "refund_key", name="uq_service_payment_refund"),
        sa.UniqueConstraint(
            "provider",
            "provider_refund_ref",
            name="uq_service_payment_refund_ref",
        ),
    )
    op.create_index("ix_service_payment_refunds_id", "service_payment_refunds", ["id"])
    op.create_index(
        "ix_service_payment_refunds_attempt_id",
        "service_payment_refunds",
        ["attempt_id"],
    )
    op.create_index(
        "ix_service_payment_refunds_business_id",
        "service_payment_refunds",
        ["business_id"],
    )
    op.create_index(
        "ix_service_payment_refunds_created_at",
        "service_payment_refunds",
        ["created_at"],
    )


def downgrade():
    op.drop_table("service_payment_refunds")
    op.drop_table("service_payment_events")
    op.drop_table("service_payment_attempts")
    op.drop_column("public_booking_settings", "deposit_hold_minutes")
    op.drop_column("public_booking_settings", "deposit_value")
    op.drop_column("public_booking_settings", "deposit_type")
