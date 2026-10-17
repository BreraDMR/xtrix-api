"""drop_dead_spending_columns
Revision ID: 0035_drop_spending
Revises: 0034_user_language
Create Date: 2026-10-17 20:30:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0035_drop_spending"
down_revision = "0034_user_language"
branch_labels = None
depends_on = None
def upgrade():
    # nothing ever wrote these; client spending is computed from completed visits (analytics.get_client_statistics)
    op.drop_index("ix_client_business_relations_spending", table_name="client_business_relations")
    op.drop_column("client_business_relations", "spending")
    op.drop_index("ix_clients_spending", table_name="clients")
    op.drop_column("clients", "spending")
def downgrade():
    op.add_column("clients", sa.Column("spending", sa.Float(), server_default="0"))
    op.create_index("ix_clients_spending", "clients", ["spending"])
    op.add_column("client_business_relations", sa.Column("spending", sa.Float(), server_default="0.0"))
    op.create_index("ix_client_business_relations_spending", "client_business_relations", ["spending"])
