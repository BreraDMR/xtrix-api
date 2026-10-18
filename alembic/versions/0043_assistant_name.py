"""assistant_name
Revision ID: 0043_assistant_name
Revises: 0042_ai_usage
Create Date: 2026-10-18 12:30:00
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0043_assistant_name"
down_revision = "0042_ai_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("businesses", sa.Column("assistant_name", sa.String(30), nullable=True))
    op.add_column("businesses", sa.Column("assistant_gender", sa.String(10), nullable=False, server_default="female"))
    op.create_check_constraint("ck_businesses_assistant_gender", "businesses",
                               "assistant_gender IN ('female', 'male', 'neuter')")


def downgrade():
    op.drop_constraint("ck_businesses_assistant_gender", "businesses", type_="check")
    op.drop_column("businesses", "assistant_gender")
    op.drop_column("businesses", "assistant_name")
