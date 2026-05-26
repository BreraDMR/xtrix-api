"""add service category and validation
Revision ID: 0017_add_service_category
Revises: 0016_create_medical_profile
Create Date: 2024-12-27 12:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0017_add_service_category"
down_revision = "0016_create_medical_profile"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    if 'services' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('services')]
        if 'category' not in columns:
            op.add_column('services', sa.Column('category', sa.String(), nullable=True))
            op.create_index('ix_services_category', 'services', ['category'])
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    if 'services' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('services')]
        if 'category' in columns:
            op.drop_index('ix_services_category', 'services')
            op.drop_column('services', 'category')
