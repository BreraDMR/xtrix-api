"""Store a visit's price currency only when a writer has explicit evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0082_visit_currency_snapshot"
down_revision = "0081_visitor_access"
branch_labels = None
depends_on = None


def upgrade():
    # Existing amounts stay unknown; the salon's current currency is not their history.
    op.add_column("visits", sa.Column("currency_snapshot", sa.String(3), nullable=True))
    op.create_check_constraint("ck_visits_currency_snapshot", "visits",
        """currency_snapshot IS NULL OR currency_snapshot COLLATE "C" ~ '^[A-Z]{3}$'""")


def downgrade():
    op.drop_constraint("ck_visits_currency_snapshot", "visits", type_="check")
    op.drop_column("visits", "currency_snapshot")
