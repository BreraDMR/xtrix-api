"""Add known-currency cashback wallets without relabelling legacy balances."""
from alembic import op
import sqlalchemy as sa

revision = '0084_cashback_currency_wallets'
down_revision = '0083_manual_payment_marker'
branch_labels = None
depends_on = None


_LEDGER_CHECK = '''(cashback_currency IS NULL AND cashback_amount_exact IS NULL AND cashback_balance_after_exact IS NULL)
    OR (cashback_currency IS NOT NULL AND cashback_currency COLLATE "C" ~ '^[A-Z]{3}$'
        AND cashback_amount_exact IS NOT NULL AND cashback_balance_after_exact IS NOT NULL
        AND cashback_amount_exact BETWEEN -9999999999999999.99 AND 9999999999999999.99
        AND cashback_balance_after_exact BETWEEN 0 AND 9999999999999999.99)'''


def upgrade():
    # No existing balance or transaction has enough evidence for a currency backfill.
    op.create_table('client_cashback_balances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('balance', sa.Numeric(18, 2), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('business_id', 'client_id', 'currency', name='uq_client_cashback_currency'),
        sa.CheckConstraint('''currency COLLATE "C" ~ '^[A-Z]{3}$' ''', name='ck_cashback_wallet_currency'),
        sa.CheckConstraint('balance >= 0 AND balance <= 9999999999999999.99', name='ck_cashback_wallet_balance'))
    op.create_index('ix_client_cashback_balances_business_id', 'client_cashback_balances', ['business_id'])
    op.create_index('ix_client_cashback_balances_client_id', 'client_cashback_balances', ['client_id'])
    for name, type_ in [('cashback_currency', sa.String(3)), ('cashback_amount_exact', sa.Numeric(18, 2)),
                        ('cashback_balance_after_exact', sa.Numeric(18, 2))]:
        op.add_column('loyalty_transactions', sa.Column(name, type_, nullable=True))
    op.create_check_constraint('ck_loyalty_cashback_snapshot', 'loyalty_transactions', _LEDGER_CHECK)


def downgrade():
    # Never silently discard a newly recorded unit or a known wallet on rollback.
    op.execute('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM client_cashback_balances)
           OR EXISTS (SELECT 1 FROM loyalty_transactions WHERE cashback_currency IS NOT NULL) THEN
            RAISE EXCEPTION 'Cashback currency data exists; export it and plan the rollback before removing this schema';
        END IF;
    END $$''')
    op.drop_constraint('ck_loyalty_cashback_snapshot', 'loyalty_transactions', type_='check')
    for name in ('cashback_balance_after_exact', 'cashback_amount_exact', 'cashback_currency'):
        op.drop_column('loyalty_transactions', name)
    op.drop_table('client_cashback_balances')
