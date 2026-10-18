"""Keep the customer binding and unfinished checkout request across retries."""
from alembic import op
import sqlalchemy as sa

revision = "0044_stripe_checkout"
down_revision = "0043_assistant_name"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stripe_checkout_state",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("customer_id", sa.String(), nullable=True, unique=True),
        sa.Column("request_key", sa.String(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True, unique=True),
        sa.Column("attempt_started_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("stripe_checkout_state")
