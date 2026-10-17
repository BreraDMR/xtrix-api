"""add_user_preferences_language
Revision ID: 0034_user_language
Revises: 0033_qr_lockout
Create Date: 2026-10-17 20:00:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0034_user_language"
down_revision = "0033_qr_lockout"
branch_labels = None
depends_on = None
def upgrade():
    # UI language of a staff user, so e-mails to owners (subscription, low stock) match it. Null = ru, as before
    op.add_column('user_preferences', sa.Column('language', sa.String(length=2), nullable=True))
def downgrade():
    op.drop_column('user_preferences', 'language')
