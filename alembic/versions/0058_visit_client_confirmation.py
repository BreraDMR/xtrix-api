"""Record the guest's own confirmation of a planned booking."""
from alembic import op
import sqlalchemy as sa

revision = "0058_visit_client_confirmation"
down_revision = "0057_booking_manage_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "visits",
        sa.Column("client_confirmed_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("visits", "client_confirmed_at")
