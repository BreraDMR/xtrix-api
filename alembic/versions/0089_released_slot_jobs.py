"""Save retryable local matching work without backfilling old releases."""
from alembic import op
import sqlalchemy as sa

revision = '0089_released_slot_jobs'
down_revision = '0088_financial_archives'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('released_slot_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_key', sa.String(120), nullable=False),
        sa.Column('segment', sa.Integer(), nullable=False),
        sa.Column('service_id', sa.Integer(), nullable=False),
        sa.Column('specialist_id', sa.Integer(), nullable=False),
        sa.Column('start', sa.DateTime(), nullable=False),
        sa.Column('end', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('event_key', 'segment', name='uq_released_slot_event_segment'),
        sa.CheckConstraint('segment >= 0 AND attempts >= 0', name='ck_released_slot_counters'),
        sa.CheckConstraint('service_id > 0 AND specialist_id > 0', name='ck_released_slot_target'),
        sa.CheckConstraint("status IN ('pending', 'done', 'expired', 'cancelled')", name='ck_released_slot_status'),
        sa.CheckConstraint("(status = 'pending' AND next_attempt_at IS NOT NULL) OR "
                           "(status <> 'pending' AND next_attempt_at IS NULL)", name='ck_released_slot_schedule'))
    op.create_index('ix_released_slot_jobs_due', 'released_slot_jobs', ['status', 'next_attempt_at'])


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM released_slot_jobs) THEN
            RAISE EXCEPTION 'Released-slot jobs exist; drain or export them before removing this schema';
        END IF;
    END $$""")
    op.drop_index('ix_released_slot_jobs_due', table_name='released_slot_jobs')
    op.drop_table('released_slot_jobs')
