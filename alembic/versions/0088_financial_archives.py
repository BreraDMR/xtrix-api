"""Persist currency-scoped monthly documents and retries without backfilling history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0088_financial_archives'
down_revision = '0087_package_coverage'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('financial_archives',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('UTC', now())")),
        sa.UniqueConstraint('business_id', 'year', 'month', name='uq_financial_archive_period'),
        sa.CheckConstraint('year BETWEEN 1 AND 9998 AND month BETWEEN 1 AND 12', name='ck_financial_archive_period'),
        sa.CheckConstraint('schema_version = 2', name='ck_financial_archive_version'),
        sa.CheckConstraint("""COALESCE(jsonb_typeof(payload) = 'object'
            AND payload->>'business_id' = business_id::text
            AND payload->>'schema_version' = schema_version::text
            AND payload#>>'{period,year}' = year::text
            AND payload#>>'{period,month}' = month::text, FALSE)""", name='ck_financial_archive_payload_scope'))
    op.execute("""CREATE FUNCTION financial_archive_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Financial archive snapshots are append-only' USING ERRCODE = '23514';
        END $$""")
    op.execute('CREATE TRIGGER financial_archive_immutable BEFORE UPDATE OR DELETE ON financial_archives '
               'FOR EACH ROW EXECUTE FUNCTION financial_archive_immutable()')
    op.create_table('financial_archive_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('business_id', 'year', 'month', name='uq_financial_archive_job_period'),
        sa.CheckConstraint('year BETWEEN 1 AND 9998 AND month BETWEEN 1 AND 12', name='ck_financial_archive_job_period'),
        sa.CheckConstraint("status IN ('pending', 'done', 'cancelled')", name='ck_financial_archive_job_status'),
        sa.CheckConstraint('attempts >= 0', name='ck_financial_archive_job_attempts'),
        sa.CheckConstraint("(status = 'pending' AND next_attempt_at IS NOT NULL) OR "
                           "(status IN ('done', 'cancelled') AND next_attempt_at IS NULL)",
                           name='ck_financial_archive_job_schedule'))
    op.create_index('ix_financial_archive_jobs_due', 'financial_archive_jobs', ['status', 'next_attempt_at'])


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM financial_archives) OR EXISTS (SELECT 1 FROM financial_archive_jobs) THEN
            RAISE EXCEPTION 'Financial archive data exists; export it and plan rollback before removing this schema';
        END IF;
    END $$""")
    op.drop_index('ix_financial_archive_jobs_due', table_name='financial_archive_jobs')
    op.drop_table('financial_archive_jobs')
    op.execute('DROP TRIGGER financial_archive_immutable ON financial_archives')
    op.execute('DROP FUNCTION financial_archive_immutable()')
    op.drop_table('financial_archives')
