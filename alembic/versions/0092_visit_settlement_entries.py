"""Append-only explicit charges and manual receipts/refunds with an exact amount per visit."""
from alembic import op
import sqlalchemy as sa

revision = '0092_visit_settlement_entries'
down_revision = '0091_visitor_proof_mail_jobs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('visit_settlement_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('visit_id', sa.Integer(), sa.ForeignKey('visits.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('reference', sa.String(120), nullable=True),
        sa.Column('idempotency_key', sa.String(80), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('visit_id', 'idempotency_key', name='uq_visit_settlement_entry_key'),
        sa.CheckConstraint("kind IN ('charge', 'manual_receipt', 'manual_refund')", name='ck_visit_settlement_entry_kind'),
        sa.CheckConstraint('amount > 0', name='ck_visit_settlement_entry_amount'),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_visit_settlement_entry_currency'),
        sa.CheckConstraint("length(request_hash) = 64 AND length(btrim(reason)) >= 3",
                           name='ck_visit_settlement_entry_text'))
    op.create_index('ix_visit_settlement_entries_visit', 'visit_settlement_entries', ['visit_id', 'id'])
    # money facts: written once, never edited or removed - a mistake is fixed by another entry
    op.execute('''CREATE FUNCTION visit_settlement_entries_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'visit_settlement_entries is append-only';
        END $$;
        CREATE TRIGGER visit_settlement_entries_append_only BEFORE UPDATE OR DELETE ON visit_settlement_entries
            FOR EACH ROW EXECUTE FUNCTION visit_settlement_entries_append_only();''')


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM visit_settlement_entries) THEN
            RAISE EXCEPTION 'Settlement entries exist; export them before removing this schema';
        END IF;
    END $$""")
    op.execute('DROP TRIGGER visit_settlement_entries_append_only ON visit_settlement_entries')
    op.execute('DROP FUNCTION visit_settlement_entries_append_only()')
    op.drop_index('ix_visit_settlement_entries_visit', table_name='visit_settlement_entries')
    op.drop_table('visit_settlement_entries')
