"""Gift offers, orders, certificates and their immutable ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0070_gift_certificates"
down_revision = "0069_service_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gift_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("face_value", sa.Numeric(10, 2), nullable=True),
        sa.Column("visits_count", sa.Integer(), nullable=True),
        sa.Column("validity_days", sa.Integer(), nullable=False),
        sa.Column("service_ids", sa.JSON(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gift_offers_id", "gift_offers", ["id"])
    op.create_index("ix_gift_offers_business_id", "gift_offers", ["business_id"])
    op.create_index("ix_gift_offers_is_active", "gift_offers", ["is_active"])
    op.create_index("ix_gift_offers_created_at", "gift_offers", ["created_at"])

    op.create_table(
        "gift_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "offer_id",
            sa.Integer(),
            sa.ForeignKey("gift_offers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("merchant", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("public_ref", sa.String(48), nullable=False),
        sa.Column("public_token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_ref", sa.String(128), nullable=True),
        sa.Column("checkout_url", sa.String(512), nullable=True),
        sa.Column("buyer_name", sa.String(255), nullable=False),
        sa.Column("buyer_email", sa.String(255), nullable=False),
        sa.Column("recipient_name", sa.String(255), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("business_id", "request_key", name="uq_gift_order_request"),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_gift_order_provider_ref"),
        sa.UniqueConstraint("public_ref", name="uq_gift_order_public_ref"),
    )
    op.create_index("ix_gift_orders_id", "gift_orders", ["id"])
    op.create_index("ix_gift_orders_business_id", "gift_orders", ["business_id"])
    op.create_index("ix_gift_orders_offer_id", "gift_orders", ["offer_id"])
    op.create_index("ix_gift_orders_status", "gift_orders", ["status"])
    op.create_index("ix_gift_orders_provider_ref", "gift_orders", ["provider_ref"])
    op.create_index("ix_gift_orders_expires_at", "gift_orders", ["expires_at"])
    op.create_index("ix_gift_orders_created_at", "gift_orders", ["created_at"])

    op.create_table(
        "gift_certificates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "offer_id",
            sa.Integer(),
            sa.ForeignKey("gift_offers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("gift_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("code_last4", sa.String(4), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("issuer_name", sa.String(255), nullable=False),
        sa.Column("offer_name", sa.String(120), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "face_value", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "visits_total", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("service_ids", sa.JSON(), nullable=True),
        sa.Column("recipient_name", sa.String(255), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=True),
        sa.Column("buyer_name", sa.String(255), nullable=False),
        sa.Column("buyer_email", sa.String(255), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("order_id", name="uq_gift_certificate_order"),
        sa.UniqueConstraint("code", name="uq_gift_certificate_code"),
        sa.UniqueConstraint("code_hash", name="uq_gift_certificate_code_hash"),
    )
    op.create_index("ix_gift_certificates_id", "gift_certificates", ["id"])
    op.create_index(
        "ix_gift_certificates_business_id", "gift_certificates", ["business_id"]
    )
    op.create_index("ix_gift_certificates_offer_id", "gift_certificates", ["offer_id"])
    op.create_index("ix_gift_certificates_order_id", "gift_certificates", ["order_id"])
    op.create_index("ix_gift_certificates_status", "gift_certificates", ["status"])
    op.create_index(
        "ix_gift_certificates_issued_at", "gift_certificates", ["issued_at"]
    )
    op.create_index(
        "ix_gift_certificates_expires_at", "gift_certificates", ["expires_at"]
    )
    op.create_index(
        "ix_gift_certificates_created_at", "gift_certificates", ["created_at"]
    )

    op.create_table(
        "gift_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "certificate_id",
            sa.Integer(),
            sa.ForeignKey("gift_certificates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column(
            "amount_delta", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column("units_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "redeems_entry_id",
            sa.Integer(),
            sa.ForeignKey("gift_ledger_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "certificate_id", "request_key", name="uq_gift_ledger_request"
        ),
        sa.UniqueConstraint(
            "certificate_id", "redeems_entry_id", name="uq_gift_ledger_reversal"
        ),
    )
    op.create_index("ix_gift_ledger_entries_id", "gift_ledger_entries", ["id"])
    op.create_index(
        "ix_gift_ledger_entries_certificate_id",
        "gift_ledger_entries",
        ["certificate_id"],
    )
    op.create_index(
        "ix_gift_ledger_entries_business_id",
        "gift_ledger_entries",
        ["business_id"],
    )
    op.create_index(
        "ix_gift_ledger_entries_entry_type", "gift_ledger_entries", ["entry_type"]
    )
    op.create_index(
        "ix_gift_ledger_entries_visit_id", "gift_ledger_entries", ["visit_id"]
    )
    op.create_index(
        "ix_gift_ledger_entries_created_at", "gift_ledger_entries", ["created_at"]
    )

    op.create_table(
        "gift_payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("gift_orders.id", ondelete="CASCADE"),
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
        sa.Column(
            "source", sa.String(20), nullable=False, server_default="webhook"
        ),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_gift_payment_event_provider"
        ),
    )
    op.create_index("ix_gift_payment_events_id", "gift_payment_events", ["id"])
    op.create_index(
        "ix_gift_payment_events_order_id", "gift_payment_events", ["order_id"]
    )
    op.create_index(
        "ix_gift_payment_events_business_id", "gift_payment_events", ["business_id"]
    )
    op.create_index(
        "ix_gift_payment_events_created_at", "gift_payment_events", ["created_at"]
    )


def downgrade():
    op.drop_table("gift_payment_events")
    op.drop_table("gift_ledger_entries")
    op.drop_table("gift_certificates")
    op.drop_table("gift_orders")
    op.drop_table("gift_offers")
