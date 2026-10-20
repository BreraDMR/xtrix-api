"""Add timezone, booking horizon and lead time to businesses."""
from alembic import op
import sqlalchemy as sa

revision = "0048_business_schedule_policy"
down_revision = "0047_onboarding_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("businesses", sa.Column(
        "timezone", sa.String(64), nullable=False, server_default="UTC"))
    op.add_column("businesses", sa.Column(
        "booking_horizon_days", sa.Integer(), nullable=False, server_default="30"))
    op.add_column("businesses", sa.Column(
        "booking_lead_time_minutes", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("businesses", "booking_lead_time_minutes")
    op.drop_column("businesses", "booking_horizon_days")
    op.drop_column("businesses", "timezone")
