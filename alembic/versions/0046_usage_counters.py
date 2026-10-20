"""Add usage_counters table for period-based CRM and AI product metering."""
from alembic import op
import sqlalchemy as sa

revision = '0046_usage_counters'
down_revision = '0045_assistant_conversations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'usage_counters',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('product', sa.String(32), nullable=False),
        sa.Column('period', sa.String(32), nullable=False),
        sa.Column('unit', sa.String(32), nullable=False),
        sa.Column('count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('money_amount', sa.Numeric(12, 2), server_default='0.00', nullable=False),
        sa.Column('policy_version', sa.String(32), server_default='2026-10-v1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('business_id', 'product', 'period', 'unit', name='uq_usage_counter_scope'),
    )
    op.create_index(
        'ix_usage_counter_lookup',
        'usage_counters',
        ['business_id', 'product', 'period'],
    )
    op.create_index('ix_usage_counters_business_id', 'usage_counters', ['business_id'])
    op.create_index('ix_usage_counters_user_id', 'usage_counters', ['user_id'])


def downgrade():
    op.drop_index('ix_usage_counters_user_id', table_name='usage_counters')
    op.drop_index('ix_usage_counters_business_id', table_name='usage_counters')
    op.drop_index('ix_usage_counter_lookup', table_name='usage_counters')
    op.drop_table('usage_counters')
