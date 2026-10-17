"""add_qr_access_lockout
Revision ID: 0033_qr_lockout
Revises: 0032_client_language
Create Date: 2026-10-17 19:00:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0033_qr_lockout"
down_revision = "0032_client_language"
branch_labels = None
depends_on = None
def upgrade():
    # the QR PIN is only 4 digits, so wrong guesses are counted per client and lock the QR for a while
    op.add_column('clients', sa.Column('qr_failed_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('clients', sa.Column('qr_locked_until', sa.DateTime(), nullable=True))
def downgrade():
    op.drop_column('clients', 'qr_locked_until')
    op.drop_column('clients', 'qr_failed_attempts')
