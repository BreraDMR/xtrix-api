"""Keep private assistant conversations and request outcomes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0045_assistant_conversations'
down_revision = '0044_stripe_checkout'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'assistant_conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=True),
        sa.Column('client_request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('title', sa.String(160), nullable=False),
        sa.Column('next_ordinal', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('owner_user_id', 'client_request_id', name='uq_assistant_conversation_request'),
        sa.CheckConstraint('next_ordinal >= 1', name='ck_assistant_conversation_ordinal'),
        sa.CheckConstraint('length(title) BETWEEN 1 AND 160', name='ck_assistant_conversation_title'),
    )
    op.create_index('ix_assistant_conversation_owner_scope_updated', 'assistant_conversations',
                    ['owner_user_id', 'business_id', 'updated_at', 'id'])
    op.create_table(
        'assistant_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('assistant_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('error_status', sa.Integer(), nullable=True),
        sa.Column('error_payload', sa.JSON(none_as_null=True), nullable=True),
        sa.Column('error_retry_after', sa.Integer(), nullable=True),
        sa.Column('lease_until', sa.DateTime(), nullable=True),
        sa.Column('plan_id', sa.String(64), nullable=True, unique=True),
        sa.Column('plan_payload', sa.JSON(none_as_null=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('execution_started_at', sa.DateTime(), nullable=True),
        sa.Column('result_payload', sa.JSON(none_as_null=True), nullable=True),
        sa.UniqueConstraint('conversation_id', 'ordinal', name='uq_assistant_message_ordinal'),
        sa.UniqueConstraint('conversation_id', 'request_id', 'role', name='uq_assistant_message_request_role'),
        sa.CheckConstraint('ordinal >= 1', name='ck_assistant_message_ordinal'),
        sa.CheckConstraint("role IN ('user', 'assistant')", name='ck_assistant_message_role'),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed', 'expired')", name='ck_assistant_message_status'),
    )


def downgrade():
    op.drop_table('assistant_messages')
    op.drop_index('ix_assistant_conversation_owner_scope_updated', table_name='assistant_conversations')
    op.drop_table('assistant_conversations')
