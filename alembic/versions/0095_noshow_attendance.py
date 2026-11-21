"""No-shows and late cancels on visits, the per-salon no-show rule, and barcodes per business.

- visits.late_cancel: a cancel that happened inside the salon's cancellation window
- businesses: the window in hours plus the "N no-shows in M months" rule (off by default)
- client_business_relations.online_booking_blocked_at: set by the rule or by staff
- products.barcode was unique across all salons; two salons selling the same item must both
  be able to scan it, so it becomes unique per business instead
"""
from alembic import op
import sqlalchemy as sa

revision = '0095_noshow_attendance'
down_revision = '0094_visitor_push_tokens'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('visits', sa.Column('late_cancel', sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column('businesses', sa.Column('late_cancel_window_hours', sa.Integer(), nullable=False,
                                          server_default='24'))
    op.add_column('businesses', sa.Column('noshow_rule_enabled', sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
    op.add_column('businesses', sa.Column('noshow_rule_threshold', sa.Integer(), nullable=False,
                                          server_default='3'))
    op.add_column('businesses', sa.Column('noshow_rule_period_months', sa.Integer(), nullable=False,
                                          server_default='6'))
    op.add_column('businesses', sa.Column('noshow_rule_tag', sa.String(40), nullable=False,
                                          server_default='no-show'))
    op.add_column('businesses', sa.Column('noshow_rule_block_online', sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
    op.create_check_constraint('ck_businesses_late_cancel_window', 'businesses',
                               'late_cancel_window_hours BETWEEN 0 AND 336')
    op.create_check_constraint('ck_businesses_noshow_rule_limits', 'businesses',
                               'noshow_rule_threshold BETWEEN 1 AND 50 '
                               'AND noshow_rule_period_months BETWEEN 1 AND 36 '
                               'AND length(btrim(noshow_rule_tag)) > 0')

    op.add_column('client_business_relations', sa.Column('online_booking_blocked_at', sa.DateTime(),
                                                         nullable=True))

    # blank barcodes mean "no barcode"; they would collide under the per-business key
    op.execute("UPDATE products SET barcode = NULL WHERE barcode IS NOT NULL AND btrim(barcode) = ''")
    op.drop_index('ix_products_barcode', table_name='products')
    op.create_index('ix_products_barcode', 'products', ['barcode'])
    op.create_unique_constraint('uq_products_business_barcode', 'products', ['business_id', 'barcode'])


def downgrade():
    op.drop_constraint('uq_products_business_barcode', 'products', type_='unique')
    op.drop_index('ix_products_barcode', table_name='products')
    # going back to one global barcode list fails loudly if two salons now share a code;
    # that's on purpose, silently dropping one of them would lose stock data
    op.create_index('ix_products_barcode', 'products', ['barcode'], unique=True)

    op.drop_column('client_business_relations', 'online_booking_blocked_at')

    op.drop_constraint('ck_businesses_noshow_rule_limits', 'businesses', type_='check')
    op.drop_constraint('ck_businesses_late_cancel_window', 'businesses', type_='check')
    for column in ('noshow_rule_block_online', 'noshow_rule_tag', 'noshow_rule_period_months',
                   'noshow_rule_threshold', 'noshow_rule_enabled', 'late_cancel_window_hours'):
        op.drop_column('businesses', column)

    # a no-show has no older equivalent; the visit simply took its slot, like a planned one
    op.execute("UPDATE visits SET status = 'planned' WHERE status = 'no_show'")
    op.drop_column('visits', 'late_cancel')
