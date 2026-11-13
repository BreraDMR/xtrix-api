"""Append-only record of explicit price/currency corrections on settled visits."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0090_visit_price_corrections'
down_revision = '0089_released_slot_jobs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('visit_price_corrections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('visit_id', sa.Integer(), sa.ForeignKey('visits.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('idempotency_key', sa.String(80), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('old_currency', sa.String(3), nullable=True),
        sa.Column('old_total', sa.Numeric(12, 2), nullable=True),
        sa.Column('old_items', postgresql.JSONB(), nullable=False),
        sa.Column('new_currency', sa.String(3), nullable=False),
        sa.Column('new_total', sa.Numeric(12, 2), nullable=False),
        sa.Column('new_items', postgresql.JSONB(), nullable=False),
        sa.Column('settlement', postgresql.JSONB(), nullable=False),
        sa.Column('prior_payment_action', sa.String(16), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('visit_id', 'idempotency_key', name='uq_visit_price_correction_key'),
        sa.CheckConstraint("prior_payment_action IN ('refund_due', 'retained')", name='ck_visit_price_correction_action'),
        sa.CheckConstraint('new_total >= 0 AND (old_total IS NULL OR old_total >= 0)', name='ck_visit_price_correction_totals'),
        sa.CheckConstraint("length(request_hash) = 64 AND length(btrim(reason)) >= 3", name='ck_visit_price_correction_text'))
    op.create_index('ix_visit_price_corrections_visit', 'visit_price_corrections', ['visit_id', 'id'])
    # history, not state: rows are written once and never edited or removed by the app
    op.execute('''CREATE FUNCTION visit_price_corrections_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'visit_price_corrections is append-only';
        END $$;
        CREATE TRIGGER visit_price_corrections_append_only BEFORE UPDATE OR DELETE ON visit_price_corrections
            FOR EACH ROW EXECUTE FUNCTION visit_price_corrections_append_only();''')


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM visit_price_corrections) THEN
            RAISE EXCEPTION 'Price corrections exist; export them before removing this schema';
        END IF;
    END $$""")
    op.execute('DROP TRIGGER visit_price_corrections_append_only ON visit_price_corrections')
    op.execute('DROP FUNCTION visit_price_corrections_append_only()')
    op.drop_index('ix_visit_price_corrections_visit', table_name='visit_price_corrections')
    op.drop_table('visit_price_corrections')
