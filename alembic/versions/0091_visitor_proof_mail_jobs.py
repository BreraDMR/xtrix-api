"""Durable, encrypted delivery work for visitor proof mails (register, reset, email change)."""
from alembic import op
import sqlalchemy as sa

revision = '0091_visitor_proof_mail_jobs'
down_revision = '0090_visit_price_corrections'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('visitor_proof_mail_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('challenge_id', sa.Integer(), sa.ForeignKey('visitor_challenges.id', ondelete='SET NULL'), nullable=True),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('locale', sa.String(5), nullable=False),
        sa.Column('origin', sa.String(255), nullable=False),
        sa.Column('key_id', sa.String(32), nullable=True),
        sa.Column('nonce', sa.LargeBinary(), nullable=True),
        sa.Column('ciphertext', sa.LargeBinary(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('lease_until', sa.DateTime(), nullable=True),
        sa.Column('lease_ticket', sa.String(32), nullable=True),
        sa.Column('deadline', sa.DateTime(), nullable=False),
        sa.Column('last_error', sa.String(40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('challenge_id', name='uq_visitor_proof_mail_challenge'),
        sa.CheckConstraint("purpose IN ('register', 'reset', 'email_change')", name='ck_visitor_proof_mail_purpose'),
        sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'captured', 'expired', 'cancelled', 'failed')",
                           name='ck_visitor_proof_mail_status'),
        sa.CheckConstraint('attempts >= 0', name='ck_visitor_proof_mail_attempts'),
        # the recoverable link exists only while the job can still be delivered
        sa.CheckConstraint("(status IN ('pending', 'sending') AND key_id IS NOT NULL AND nonce IS NOT NULL "
                           "AND ciphertext IS NOT NULL) OR (status NOT IN ('pending', 'sending') AND key_id IS NULL "
                           "AND nonce IS NULL AND ciphertext IS NULL)", name='ck_visitor_proof_mail_secret'),
        sa.CheckConstraint("(status = 'pending' AND next_attempt_at IS NOT NULL AND lease_ticket IS NULL) OR "
                           "(status = 'sending' AND lease_until IS NOT NULL AND lease_ticket IS NOT NULL) OR "
                           "(status NOT IN ('pending', 'sending') AND next_attempt_at IS NULL AND lease_ticket IS NULL)",
                           name='ck_visitor_proof_mail_schedule'))
    op.create_index('ix_visitor_proof_mail_due', 'visitor_proof_mail_jobs', ['status', 'next_attempt_at'])


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM visitor_proof_mail_jobs WHERE status IN ('pending', 'sending')) THEN
            RAISE EXCEPTION 'Visitor proof mails are still queued; let them finish before removing this schema';
        END IF;
    END $$""")
    op.drop_index('ix_visitor_proof_mail_due', table_name='visitor_proof_mail_jobs')
    op.drop_table('visitor_proof_mail_jobs')
