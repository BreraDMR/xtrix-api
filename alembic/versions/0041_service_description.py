"""service_description
Revision ID: 0041_service_description
Revises: 0040_data_codes
Create Date: 2026-10-18 08:30:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0041_service_description"
down_revision = "0040_data_codes"
branch_labels = None
depends_on = None
def upgrade():
    # the service form always had a description field, it just had nowhere to go
    op.add_column('services', sa.Column('description', sa.Text(), nullable=True))
def downgrade():
    op.drop_column('services', 'description')
