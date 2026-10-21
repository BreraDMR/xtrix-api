"""Business-scoped action journal, separate from the legacy platform audit log."""
from alembic import op
import sqlalchemy as sa

revision = "0073_business_audit_events"
down_revision = "0072_campaigns"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(business_id IS NULL) <> (owner_user_id IS NULL)",
            name="ck_business_audit_events_scope",
        ),
    )
    op.create_index("ix_business_audit_events_id", "business_audit_events", ["id"])
    op.create_index(
        "ix_business_audit_events_business_id",
        "business_audit_events",
        ["business_id"],
    )
    op.create_index(
        "ix_business_audit_events_owner_user_id",
        "business_audit_events",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_business_audit_events_actor_user_id",
        "business_audit_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_business_audit_events_action", "business_audit_events", ["action"]
    )
    op.create_index(
        "ix_business_audit_events_entity_type",
        "business_audit_events",
        ["entity_type"],
    )
    op.create_index(
        "ix_business_audit_events_entity_id", "business_audit_events", ["entity_id"]
    )
    op.create_index(
        "ix_business_audit_events_request_id",
        "business_audit_events",
        ["request_id"],
    )
    op.create_index(
        "ix_business_audit_events_created_at",
        "business_audit_events",
        ["created_at"],
    )
    op.create_index(
        "ix_business_audit_events_business_created",
        "business_audit_events",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_business_audit_events_owner_created",
        "business_audit_events",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_business_audit_events_business_action",
        "business_audit_events",
        ["business_id", "action"],
    )
    op.create_index(
        "ix_business_audit_events_business_entity",
        "business_audit_events",
        ["business_id", "entity_type", "entity_id"],
    )


def downgrade():
    op.drop_index(
        "ix_business_audit_events_business_entity", table_name="business_audit_events"
    )
    op.drop_index(
        "ix_business_audit_events_business_action", table_name="business_audit_events"
    )
    op.drop_index(
        "ix_business_audit_events_owner_created", table_name="business_audit_events"
    )
    op.drop_index(
        "ix_business_audit_events_business_created", table_name="business_audit_events"
    )
    op.drop_index("ix_business_audit_events_created_at", table_name="business_audit_events")
    op.drop_index("ix_business_audit_events_request_id", table_name="business_audit_events")
    op.drop_index("ix_business_audit_events_entity_id", table_name="business_audit_events")
    op.drop_index("ix_business_audit_events_entity_type", table_name="business_audit_events")
    op.drop_index("ix_business_audit_events_action", table_name="business_audit_events")
    op.drop_index(
        "ix_business_audit_events_actor_user_id", table_name="business_audit_events"
    )
    op.drop_index(
        "ix_business_audit_events_owner_user_id", table_name="business_audit_events"
    )
    op.drop_index(
        "ix_business_audit_events_business_id", table_name="business_audit_events"
    )
    op.drop_index("ix_business_audit_events_id", table_name="business_audit_events")
    op.drop_table("business_audit_events")
