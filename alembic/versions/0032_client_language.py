"""add_client_language
Revision ID: 0032_client_language
Revises: 0031_service_spec
Create Date: 2026-10-17 18:00:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0032_client_language"
down_revision = "0031_service_spec"
branch_labels = None
depends_on = None
def upgrade():
    # language for messages sent to the client (reminders, greetings) - separate from the staff UI language.
    # existing clients get "ru": that's what birthday and follow-up messages were written in so far
    op.add_column('clients', sa.Column('language', sa.String(length=2), nullable=False, server_default='ru'))
def downgrade():
    op.drop_column('clients', 'language')
