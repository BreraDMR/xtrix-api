"""Keep the purchased gift terms before payment completes."""
from alembic import op
import sqlalchemy as sa

revision = "0076_gift_order_terms"
down_revision = "0075_google_login"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gift_orders", sa.Column("offer_snapshot", sa.JSON(), nullable=True))
    # Earlier orders only kept their price. Preserve the available terms on upgrade.
    op.execute("""
        UPDATE gift_orders AS orders SET offer_snapshot = json_build_object(
            'name', offers.name, 'kind', offers.kind, 'face_value', offers.face_value::text,
            'visits_count', offers.visits_count, 'service_ids', COALESCE(offers.service_ids, '[]'::json),
            'validity_days', offers.validity_days
        ) FROM gift_offers AS offers WHERE offers.id = orders.offer_id
    """)
    op.alter_column("gift_orders", "offer_snapshot", nullable=False)


def downgrade():
    op.drop_column("gift_orders", "offer_snapshot")
