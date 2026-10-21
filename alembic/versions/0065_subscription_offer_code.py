"""Remember which public offer a Stripe subscription was bought from."""
from alembic import op
import sqlalchemy as sa

revision = "0065_subscription_offer_code"
down_revision = "0064_client_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscriptions", sa.Column("offer_code", sa.String(), nullable=True))


def downgrade():
    op.drop_column("subscriptions", "offer_code")
