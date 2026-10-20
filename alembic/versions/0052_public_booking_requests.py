"""Remember public booking retry keys and the visit each one produced."""
from alembic import op
import sqlalchemy as sa

revision = "0052_public_booking_requests"
down_revision = "0051_public_booking_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "public_booking_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processing_consent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("business_id", "request_id", name="uq_public_booking_request"),
    )
    op.create_index("ix_public_booking_requests_business_id", "public_booking_requests", ["business_id"])
    op.create_index("ix_public_booking_requests_client_id", "public_booking_requests", ["client_id"])
    op.create_index("ix_public_booking_requests_visit_id", "public_booking_requests", ["visit_id"])
    op.create_index("ix_public_booking_requests_created_at", "public_booking_requests", ["created_at"])


def downgrade():
    op.drop_index("ix_public_booking_requests_created_at", table_name="public_booking_requests")
    op.drop_index("ix_public_booking_requests_visit_id", table_name="public_booking_requests")
    op.drop_index("ix_public_booking_requests_client_id", table_name="public_booking_requests")
    op.drop_index("ix_public_booking_requests_business_id", table_name="public_booking_requests")
    op.drop_table("public_booking_requests")
