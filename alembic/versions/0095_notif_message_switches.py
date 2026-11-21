"""Per-salon switches for birthday, thank-you and CRM visit messages.

Birthday greetings and thank-you notes are marketing, so they start off and need the client's
consent on top. Messages about a visit the staff changed in the CRM are transactional and
start on, like the booking messages guests already get.
"""
from alembic import op
import sqlalchemy as sa

revision = '0095_notif_message_switches'
down_revision = '0094_visitor_push_tokens'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('notification_settings', sa.Column(
        'birthday_greetings_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('notification_settings', sa.Column(
        'visit_followups_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('notification_settings', sa.Column(
        'crm_visit_messages_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('notification_settings', 'crm_visit_messages_enabled')
    op.drop_column('notification_settings', 'visit_followups_enabled')
    op.drop_column('notification_settings', 'birthday_greetings_enabled')
