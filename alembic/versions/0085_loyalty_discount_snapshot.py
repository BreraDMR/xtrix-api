"""Keep the monetary result of a bonus payment instead of replaying current rates."""
from alembic import op
import sqlalchemy as sa

revision = '0085_loyalty_discount_snapshot'
down_revision = '0084_cashback_currency_wallets'
branch_labels = None
depends_on = None

_DISCOUNT_CHECK = '''(discount_currency IS NULL AND discount_amount_exact IS NULL)
    OR (discount_currency IS NOT NULL AND discount_currency COLLATE "C" ~ '^[A-Z]{3}$'
        AND discount_amount_exact IS NOT NULL AND discount_amount_exact > 0
        AND discount_amount_exact <= 9999999999999999.99 AND transaction_type = 'spent'
        AND points_amount <= 0 AND coalesce(cashback_amount_exact, 0) <= 0
        AND (points_amount < 0 OR coalesce(cashback_amount_exact, 0) < 0)
        AND discount_amount_exact >= -coalesce(cashback_amount_exact, 0)
        AND (cashback_currency IS NULL OR cashback_currency = discount_currency))'''


def upgrade():
    # Old point amounts do not prove their historical monetary conversion rate.
    op.add_column('loyalty_transactions', sa.Column('discount_currency', sa.String(3), nullable=True))
    op.add_column('loyalty_transactions', sa.Column('discount_amount_exact', sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint('ck_loyalty_discount_snapshot', 'loyalty_transactions', _DISCOUNT_CHECK)


def downgrade():
    op.execute('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM loyalty_transactions WHERE discount_currency IS NOT NULL
            OR discount_amount_exact IS NOT NULL) THEN
            RAISE EXCEPTION 'Bonus payment amounts exist; export them and plan rollback before removing this schema';
        END IF;
    END $$''')
    op.drop_constraint('ck_loyalty_discount_snapshot', 'loyalty_transactions', type_='check')
    op.drop_column('loyalty_transactions', 'discount_amount_exact')
    op.drop_column('loyalty_transactions', 'discount_currency')
