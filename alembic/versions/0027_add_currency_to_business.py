"""add currency to business

Revision ID: 0027_currency
Revises: 0026_loyalty
Create Date: 2025-01-12 14:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision = "0027_currency"
down_revision = "0026_loyalty"
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    
    if 'businesses' in table_names:
        columns = [col['name'] for col in inspector.get_columns('businesses')]
        if 'currency' not in columns:
            # Добавляем колонку currency с дефолтным значением EUR
            op.add_column('businesses', sa.Column('currency', sa.String(), nullable=False, server_default='EUR'))
            op.create_index('ix_businesses_currency', 'businesses', ['currency'])
            # Обновляем существующие записи, если currency NULL (на случай если server_default не сработал)
            conn.execute(text("UPDATE businesses SET currency = 'EUR' WHERE currency IS NULL"))


def downgrade():
    try:
        op.drop_index('ix_businesses_currency', 'businesses')
    except:
        pass
    try:
        op.drop_column('businesses', 'currency')
    except:
        pass
