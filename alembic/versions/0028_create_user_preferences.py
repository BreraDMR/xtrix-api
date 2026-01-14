"""create user preferences

Revision ID: 0028_user_preferences
Revises: 0027_currency
Create Date: 2025-01-12 15:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision = "0028_user_preferences"
down_revision = "0027_currency"
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    
    if 'user_preferences' not in table_names:
        op.create_table(
            'user_preferences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('timezone', sa.String(), nullable=True, server_default='UTC'),
            sa.Column('date_format', sa.String(), nullable=True, server_default='DD/MM/YYYY'),
            sa.Column('time_format', sa.String(), nullable=True, server_default='24h'),
            sa.Column('email_notifications', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('push_notifications', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('notification_email', sa.String(), nullable=True),
            sa.Column('theme', sa.String(), nullable=False, server_default='light'),
            sa.Column('auto_save_enabled', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('auto_save_interval', sa.Integer(), nullable=False, server_default='30'),
            sa.Column('export_format', sa.String(), nullable=True, server_default='csv'),
            sa.Column('additional_settings', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id')
        )
        op.create_index('ix_user_preferences_id', 'user_preferences', ['id'], unique=False)
        op.create_index('ix_user_preferences_user_id', 'user_preferences', ['user_id'], unique=True)


def downgrade():
    try:
        op.drop_index('ix_user_preferences_user_id', 'user_preferences')
        op.drop_index('ix_user_preferences_id', 'user_preferences')
    except:
        pass
    try:
        op.drop_table('user_preferences')
    except:
        pass
