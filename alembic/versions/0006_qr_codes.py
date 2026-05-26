"""add qr code fields to clients
Revision ID: 0006_qr_codes
Revises: 0005_account_lockout
Create Date: 2024-01-06 00:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0006_qr_codes"
down_revision = "0005_account_lockout"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("clients", sa.Column("qr_code", sa.String, nullable=True))
    op.add_column("clients", sa.Column("access_password", sa.String, nullable=True))
    op.create_index("ix_clients_qr_code", "clients", ["qr_code"], unique=True)
def downgrade():
    op.drop_index("ix_clients_qr_code", "clients")
    op.drop_column("clients", "access_password")
    op.drop_column("clients", "qr_code")
