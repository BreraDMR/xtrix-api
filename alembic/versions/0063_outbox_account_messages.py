"""Let account-level outbox messages exist without a business and point at a user."""
from alembic import op
import sqlalchemy as sa

revision = "0063_outbox_account_messages"
down_revision = "0062_password_reset_token_hash"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "notification_outbox",
        "business_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column("notification_outbox", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_notification_outbox_user_id_users",
        "notification_outbox",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_notification_outbox_user_id", "notification_outbox", ["user_id"])


def downgrade():
    op.drop_index("ix_notification_outbox_user_id", table_name="notification_outbox")
    op.drop_constraint(
        "fk_notification_outbox_user_id_users", "notification_outbox", type_="foreignkey"
    )
    op.drop_column("notification_outbox", "user_id")
    # business-less rows cannot satisfy the old NOT NULL column
    op.execute("DELETE FROM notification_outbox WHERE business_id IS NULL")
    op.alter_column(
        "notification_outbox",
        "business_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
