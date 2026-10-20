"""Add first-run onboarding preferences to user_preferences."""
from alembic import op
import sqlalchemy as sa

revision = "0047_onboarding_preferences"
down_revision = "0046_usage_counters"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_preferences", sa.Column(
        "onboarding_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("user_preferences", sa.Column(
        "onboarding_tour_version", sa.Integer(), nullable=True))
    op.add_column("user_preferences", sa.Column(
        "onboarding_tour_completed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_preferences", sa.Column(
        "onboarding_tour_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_preferences", sa.Column(
        "onboarding_welcome_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_preferences", sa.Column(
        "onboarding_checklist_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_preferences", sa.Column(
        "onboarding_dismissed_hints", sa.JSON(), nullable=True))
    # existing accounts already know the product; the first-run tour is for new ones
    op.execute(
        "UPDATE user_preferences SET "
        "onboarding_tour_completed = TRUE, onboarding_tour_version = 1, "
        "onboarding_welcome_dismissed = TRUE, onboarding_checklist_dismissed = TRUE"
    )


def downgrade():
    op.drop_column("user_preferences", "onboarding_dismissed_hints")
    op.drop_column("user_preferences", "onboarding_checklist_dismissed")
    op.drop_column("user_preferences", "onboarding_welcome_dismissed")
    op.drop_column("user_preferences", "onboarding_tour_dismissed")
    op.drop_column("user_preferences", "onboarding_tour_completed")
    op.drop_column("user_preferences", "onboarding_tour_version")
    op.drop_column("user_preferences", "onboarding_version")
