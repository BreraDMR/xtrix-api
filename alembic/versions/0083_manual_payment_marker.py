"""Remember a recorded manual payment after later status or method edits."""
from alembic import op
import sqlalchemy as sa

revision = '0083_manual_payment_marker'
down_revision = '0082_visit_currency_snapshot'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('visits', sa.Column('manual_payment_recorded_at', sa.DateTime(), nullable=True))
    # This is when evidence is recorded, not a guessed historical payment date.
    op.execute('''UPDATE visits SET manual_payment_recorded_at=timezone('UTC',transaction_timestamp())
        WHERE status='completed' AND price>0 AND price<'Infinity'::double precision
          AND payment_method IN ('cash','card','other')''')
    op.execute('''CREATE FUNCTION visits_record_manual_payment() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='UPDATE' AND OLD.manual_payment_recorded_at IS NOT NULL THEN
                NEW.manual_payment_recorded_at := OLD.manual_payment_recorded_at;
            ELSIF NEW.status='completed' AND NEW.price>0 AND NEW.price<'Infinity'::double precision
                  AND NEW.payment_method IN ('cash','card','other') THEN
                NEW.manual_payment_recorded_at := timezone('UTC',clock_timestamp());
            ELSE
                NEW.manual_payment_recorded_at := NULL;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER visits_record_manual_payment BEFORE INSERT OR UPDATE ON visits
            FOR EACH ROW EXECUTE FUNCTION visits_record_manual_payment();''')


def downgrade():
    # Dropping this evidence is destructive; a real rollback needs a backup.
    op.execute('DROP TRIGGER visits_record_manual_payment ON visits')
    op.execute('DROP FUNCTION visits_record_manual_payment()')
    op.drop_column('visits', 'manual_payment_recorded_at')
