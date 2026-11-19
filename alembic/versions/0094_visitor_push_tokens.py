"""Phones that get visit reminders through Expo Push, one row per device token."""
from alembic import op
import sqlalchemy as sa

revision = '0094_visitor_push_tokens'
down_revision = '0093_business_specialist_type'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('visitor_push_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('visitor_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token', sa.String(255), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('language', sa.String(2), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.UniqueConstraint('token', name='uq_visitor_push_tokens_token'),
        sa.CheckConstraint("platform IN ('ios','android')", name='ck_visitor_push_tokens_platform'),
        sa.CheckConstraint('length(token) > 0', name='ck_visitor_push_tokens_token'))
    op.create_index('ix_visitor_push_tokens_session_id', 'visitor_push_tokens', ['session_id'])


def downgrade():
    # queued phone reminders point at these tokens; without the table they could never be checked
    op.execute("UPDATE notification_outbox SET status = 'cancelled', updated_at = timezone('utc', now()) "
               "WHERE channel = 'push' AND status IN ('pending', 'processing')")
    op.drop_index('ix_visitor_push_tokens_session_id', table_name='visitor_push_tokens')
    op.drop_table('visitor_push_tokens')
