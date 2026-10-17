"""add_users_tokens_valid_after
Revision ID: 0038_tokens_valid_after
Revises: 0036_notification_log
Create Date: 2026-10-18 01:30:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0038_tokens_valid_after"
down_revision = "0036_notification_log"
branch_labels = None
depends_on = None
def upgrade():
    # access tokens issued before this moment are refused - "log out everywhere" sets it
    op.add_column('users', sa.Column('tokens_valid_after', sa.DateTime(), nullable=True))
def downgrade():
    op.drop_column('users', 'tokens_valid_after')
