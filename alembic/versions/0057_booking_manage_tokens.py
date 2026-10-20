"""Store only hashes of visit-scoped management links."""
from alembic import op
import sqlalchemy as sa

revision = "0057_booking_manage_tokens"
down_revision = "0056_questionnaire_attachments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "booking_manage_tokens",
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
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "actions",
            sa.String(64),
            nullable=False,
            server_default="confirm,reschedule,cancel",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_booking_manage_token_hash"),
    )
    op.create_index(
        "ix_booking_manage_tokens_business_id",
        "booking_manage_tokens",
        ["business_id"],
    )
    op.create_index(
        "ix_booking_manage_tokens_visit_id",
        "booking_manage_tokens",
        ["visit_id"],
    )
    op.create_index(
        "ix_booking_manage_tokens_expires_at",
        "booking_manage_tokens",
        ["expires_at"],
    )


def downgrade():
    op.drop_index("ix_booking_manage_tokens_expires_at", table_name="booking_manage_tokens")
    op.drop_index("ix_booking_manage_tokens_visit_id", table_name="booking_manage_tokens")
    op.drop_index("ix_booking_manage_tokens_business_id", table_name="booking_manage_tokens")
    op.drop_table("booking_manage_tokens")
