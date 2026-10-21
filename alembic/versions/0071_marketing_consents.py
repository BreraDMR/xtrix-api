"""Explicit marketing consent with its source and revocation, separate from clients."""
from alembic import op
import sqlalchemy as sa

revision = "0071_marketing_consents"
down_revision = "0070_gift_certificates"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "marketing_consents",
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
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False, server_default="email"),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="subscribed"
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column("subscribed_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "business_id", "client_id", "channel", name="uq_marketing_consent_client_channel"
        ),
    )
    op.create_index("ix_marketing_consents_id", "marketing_consents", ["id"])
    op.create_index(
        "ix_marketing_consents_business_id", "marketing_consents", ["business_id"]
    )
    op.create_index(
        "ix_marketing_consents_client_id", "marketing_consents", ["client_id"]
    )
    op.create_index("ix_marketing_consents_status", "marketing_consents", ["status"])

    # Consents explicitly given before this table existed stay valid after the upgrade.
    op.execute(
        """
        INSERT INTO marketing_consents
            (business_id, client_id, channel, status, source, source_ref,
             subscribed_at, created_at, updated_at)
        SELECT DISTINCT ON (p.business_id, p.client_id)
            p.business_id, p.client_id, 'email', 'subscribed', 'public_booking',
            p.id::text, p.created_at, NOW(), NOW()
        FROM public_booking_requests p
        JOIN clients c ON c.id = p.client_id AND c.deleted_at IS NULL
        WHERE p.marketing_consent = true AND p.client_id IS NOT NULL
        ORDER BY p.business_id, p.client_id, p.created_at
        ON CONFLICT (business_id, client_id, channel) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO marketing_consents
            (business_id, client_id, channel, status, source, source_ref,
             subscribed_at, created_at, updated_at)
        SELECT DISTINCT ON (w.business_id, w.client_id)
            w.business_id, w.client_id, 'email', 'subscribed', 'waitlist',
            w.id::text, w.created_at, NOW(), NOW()
        FROM waitlist_entries w
        JOIN clients c ON c.id = w.client_id AND c.deleted_at IS NULL
        WHERE w.marketing_consent = true AND w.client_id IS NOT NULL
        ORDER BY w.business_id, w.client_id, w.created_at
        ON CONFLICT (business_id, client_id, channel) DO NOTHING
        """
    )


def downgrade():
    op.drop_index("ix_marketing_consents_status", table_name="marketing_consents")
    op.drop_index("ix_marketing_consents_client_id", table_name="marketing_consents")
    op.drop_index("ix_marketing_consents_business_id", table_name="marketing_consents")
    op.drop_index("ix_marketing_consents_id", table_name="marketing_consents")
    op.drop_table("marketing_consents")
