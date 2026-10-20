"""Public booking settings, one per business, with a unique published slug."""
from alembic import op
import sqlalchemy as sa

revision = "0051_public_booking_settings"
down_revision = "0050_service_buffers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "public_booking_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("business_id", name="uq_public_booking_business"),
        sa.UniqueConstraint("slug", name="uq_public_booking_slug"),
    )
    op.create_index("ix_public_booking_settings_business_id", "public_booking_settings", ["business_id"])
    op.create_index("ix_public_booking_settings_slug", "public_booking_settings", ["slug"])


def downgrade():
    op.drop_index("ix_public_booking_settings_slug", table_name="public_booking_settings")
    op.drop_index("ix_public_booking_settings_business_id", table_name="public_booking_settings")
    op.drop_table("public_booking_settings")
