"""add_payment_method_to_visits

Revision ID: 0029_add_payment_method_to_visits
Revises: 0028_create_user_preferences
Create Date: 2025-01-13 15:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0029_add_payment_method_to_visits"
down_revision = "0028_user_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('visits', sa.Column('payment_method', sa.String(), nullable=True))
    op.create_index(op.f('ix_visits_payment_method'), 'visits', ['payment_method'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_visits_payment_method'), table_name='visits')
    op.drop_column('visits', 'payment_method')
