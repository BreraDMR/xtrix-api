"""add trial fields to subscriptions
Revision ID: 0021_add_trial_fields
Revises: 0020_add_user_moderation
Create Date: 2025-01-05 13:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0021_add_trial_fields"
down_revision = "0020_add_user_moderation"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'subscription_plans' in table_names:
        columns = [col['name'] for col in inspector.get_columns('subscription_plans')]
        if 'is_trial' not in columns:
            op.add_column('subscription_plans', sa.Column('is_trial', sa.Boolean(), nullable=False, server_default='false'))
        if 'trial_days' not in columns:
            op.add_column('subscription_plans', sa.Column('trial_days', sa.Integer(), nullable=True))
    if 'subscriptions' in table_names:
        columns = [col['name'] for col in inspector.get_columns('subscriptions')]
        if 'is_trial' not in columns:
            op.add_column('subscriptions', sa.Column('is_trial', sa.Boolean(), nullable=False, server_default='false'))
        if 'trial_start' not in columns:
            op.add_column('subscriptions', sa.Column('trial_start', sa.DateTime(), nullable=True))
        if 'trial_end' not in columns:
            op.add_column('subscriptions', sa.Column('trial_end', sa.DateTime(), nullable=True))
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'subscriptions' in table_names:
        columns = [col['name'] for col in inspector.get_columns('subscriptions')]
        if 'trial_end' in columns:
            op.drop_column('subscriptions', 'trial_end')
        if 'trial_start' in columns:
            op.drop_column('subscriptions', 'trial_start')
        if 'is_trial' in columns:
            op.drop_column('subscriptions', 'is_trial')
    if 'subscription_plans' in table_names:
        columns = [col['name'] for col in inspector.get_columns('subscription_plans')]
        if 'trial_days' in columns:
            op.drop_column('subscription_plans', 'trial_days')
        if 'is_trial' in columns:
            op.drop_column('subscription_plans', 'is_trial')
