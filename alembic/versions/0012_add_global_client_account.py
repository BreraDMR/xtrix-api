"""add global client account
Revision ID: 0012_add_global_client_account
Revises: 0011_add_specialist_types
Create Date: 2024-12-22 12:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0012_add_global_client_account"
down_revision = "0011_add_specialist_types"
branch_labels = None
depends_on = None
def upgrade():
    op.alter_column("clients", "owner_id", nullable=True, existing_type=sa.Integer())
    op.create_table(
        "client_specialist_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("specialist_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_client_specialist_relations_client_id", "client_specialist_relations", ["client_id"])
    op.create_index("ix_client_specialist_relations_specialist_id", "client_specialist_relations", ["specialist_id"])
    op.create_index("ix_client_specialist_relations_created_at", "client_specialist_relations", ["created_at"])
    op.create_unique_constraint("uq_client_specialist", "client_specialist_relations", ["client_id", "specialist_id"])
def downgrade():
    op.drop_constraint("uq_client_specialist", "client_specialist_relations", type_="unique")
    op.drop_index("ix_client_specialist_relations_created_at", "client_specialist_relations")
    op.drop_index("ix_client_specialist_relations_specialist_id", "client_specialist_relations")
    op.drop_index("ix_client_specialist_relations_client_id", "client_specialist_relations")
    op.drop_table("client_specialist_relations")
    op.alter_column("clients", "owner_id", nullable=False, existing_type=sa.Integer())
