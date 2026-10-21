"""Campaign drafts, their frozen audiences, and the outbox link to queued messages."""
from alembic import op
import sqlalchemy as sa

revision = "0072_campaigns"
down_revision = "0071_marketing_consents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "segment_kind", sa.String(32), nullable=False, server_default="no_visits"
        ),
        sa.Column("inactive_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("audience_snapshot", sa.JSON(), nullable=True),
        sa.Column("audience_hash", sa.String(64), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "confirmed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_campaigns_id", "campaigns", ["id"])
    op.create_index("ix_campaigns_business_id", "campaigns", ["business_id"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])
    op.create_index("ix_campaigns_created_at", "campaigns", ["created_at"])
    op.create_index("ix_campaigns_business_status", "campaigns", ["business_id", "status"])

    op.add_column(
        "notification_outbox",
        sa.Column("campaign_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_outbox_campaign_id",
        "notification_outbox",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_notification_outbox_campaign_id",
        "notification_outbox",
        ["campaign_id"],
    )

    op.add_column(
        "marketing_consents",
        sa.Column("revoked_source", sa.String(32), nullable=True),
    )
    op.add_column(
        "marketing_consents",
        sa.Column("revoked_campaign_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_marketing_consents_revoked_campaign_id",
        "marketing_consents",
        "campaigns",
        ["revoked_campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_marketing_consents_revoked_campaign_id",
        "marketing_consents",
        ["revoked_campaign_id"],
    )


def downgrade():
    op.drop_index(
        "ix_marketing_consents_revoked_campaign_id", table_name="marketing_consents"
    )
    op.drop_constraint(
        "fk_marketing_consents_revoked_campaign_id",
        "marketing_consents",
        type_="foreignkey",
    )
    op.drop_column("marketing_consents", "revoked_campaign_id")
    op.drop_column("marketing_consents", "revoked_source")

    op.drop_index(
        "ix_notification_outbox_campaign_id", table_name="notification_outbox"
    )
    op.drop_constraint(
        "fk_notification_outbox_campaign_id",
        "notification_outbox",
        type_="foreignkey",
    )
    op.drop_column("notification_outbox", "campaign_id")

    op.drop_index("ix_campaigns_business_status", table_name="campaigns")
    op.drop_index("ix_campaigns_created_at", table_name="campaigns")
    op.drop_index("ix_campaigns_status", table_name="campaigns")
    op.drop_index("ix_campaigns_business_id", table_name="campaigns")
    op.drop_index("ix_campaigns_id", table_name="campaigns")
    op.drop_table("campaigns")
