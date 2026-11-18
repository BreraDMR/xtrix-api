"""The business keeps its specialty (one of SpecialistType), so the front can shape the UI around it."""
from alembic import op
import sqlalchemy as sa

revision = '0093_business_specialist_type'
down_revision = '0092_visit_settlement_entries'
branch_labels = None
depends_on = None


def upgrade():
    # nullable and no backfill: old businesses just haven't picked one yet
    op.add_column('businesses', sa.Column('specialist_type', sa.String(64), nullable=True))


def downgrade():
    op.drop_column('businesses', 'specialist_type')
