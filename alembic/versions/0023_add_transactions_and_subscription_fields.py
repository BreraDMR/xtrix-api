"""add transactions and subscription cancellation fields
Revision ID: 0023_transactions
Revises: 0022_default_plans
Create Date: 2025-01-05 15:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0023_transactions"
down_revision = "0022_default_plans"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'transactions' not in table_names:
        op.create_table(
            'transactions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('subscription_id', sa.Integer(), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('currency', sa.String(), nullable=False, server_default='eur'),
            sa.Column('payment_method', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('transaction_date', sa.DateTime(), nullable=False),
            sa.Column('external_transaction_id', sa.String(), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('provider', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_transactions_id', 'transactions', ['id'], unique=False)
        op.create_index('ix_transactions_subscription_id', 'transactions', ['subscription_id'], unique=False)
        op.create_index('ix_transactions_status', 'transactions', ['status'], unique=False)
        op.create_index('ix_transactions_transaction_date', 'transactions', ['transaction_date'], unique=False)
        op.create_index('ix_transactions_external_transaction_id', 'transactions', ['external_transaction_id'], unique=False)
        op.create_index('ix_transactions_created_at', 'transactions', ['created_at'], unique=False)
    if 'subscriptions' in table_names:
        columns = [col['name'] for col in inspector.get_columns('subscriptions')]
        if 'cancel_at_period_end' not in columns:
            op.add_column('subscriptions', sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False, server_default='false'))
        if 'cancelled_at' not in columns:
            op.add_column('subscriptions', sa.Column('cancelled_at', sa.DateTime(), nullable=True))
            op.create_index('ix_subscriptions_cancelled_at', 'subscriptions', ['cancelled_at'], unique=False)
        if 'reactivated_at' not in columns:
            op.add_column('subscriptions', sa.Column('reactivated_at', sa.DateTime(), nullable=True))
        if 'last_transaction_id' not in columns:
            op.add_column('subscriptions', sa.Column('last_transaction_id', sa.Integer(), nullable=True))
            op.create_foreign_key('fk_subscriptions_last_transaction_id', 'subscriptions', 'transactions', ['last_transaction_id'], ['id'])
        indexes = [idx['name'] for idx in inspector.get_indexes('subscriptions')]
        if 'ix_subscriptions_status' not in indexes:
            op.create_index('ix_subscriptions_status', 'subscriptions', ['status'], unique=False)
        if 'ix_subscriptions_current_period_end' not in indexes:
            op.create_index('ix_subscriptions_current_period_end', 'subscriptions', ['current_period_end'], unique=False)
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'subscriptions' in table_names:
        columns = [col['name'] for col in inspector.get_columns('subscriptions')]
        if 'last_transaction_id' in columns:
            op.drop_constraint('fk_subscriptions_last_transaction_id', 'subscriptions', type_='foreignkey')
            op.drop_column('subscriptions', 'last_transaction_id')
        if 'reactivated_at' in columns:
            op.drop_column('subscriptions', 'reactivated_at')
        if 'cancelled_at' in columns:
            op.drop_index('ix_subscriptions_cancelled_at', table_name='subscriptions')
            op.drop_column('subscriptions', 'cancelled_at')
        if 'cancel_at_period_end' in columns:
            op.drop_column('subscriptions', 'cancel_at_period_end')
    if 'transactions' in table_names:
        op.drop_index('ix_transactions_created_at', table_name='transactions')
        op.drop_index('ix_transactions_external_transaction_id', table_name='transactions')
        op.drop_index('ix_transactions_transaction_date', table_name='transactions')
        op.drop_index('ix_transactions_status', table_name='transactions')
        op.drop_index('ix_transactions_subscription_id', table_name='transactions')
        op.drop_index('ix_transactions_id', table_name='transactions')
        op.drop_table('transactions')
