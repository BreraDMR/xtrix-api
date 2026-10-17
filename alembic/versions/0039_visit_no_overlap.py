"""visits_no_overlap
Revision ID: 0039_visit_no_overlap
Revises: 0036_notification_log
Create Date: 2026-10-18 01:30:00
"""
from alembic import op
import sqlalchemy as sa
# revision identifiers, used by Alembic.
revision = "0039_visit_no_overlap"
down_revision = "0036_notification_log"
branch_labels = None
depends_on = None

# The same rule as crud_visit.find_overlapping_visit, but enforced by Postgres, so two requests
# racing each other can't both book the slot. A visit with no end holds an hour; an end before
# the start counts as an empty slot instead of breaking the range.
def _slot(t=""):
    return (f"tsrange({t}date_start, GREATEST({t}date_start, "
            f"COALESCE({t}date_end, {t}date_start + interval '1 hour')), '[)')")


def _active(t=""):
    return f"{t}deleted_at IS NULL AND {t}status IS DISTINCT FROM 'cancelled'"


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    conn = op.get_bind()
    clashes = conn.execute(sa.text(f"""
        SELECT a.id, b.id FROM visits a JOIN visits b
          ON a.specialist_id = b.specialist_id AND a.id < b.id
         AND {_slot('a.')} && {_slot('b.')}
        WHERE {_active('a.')} AND {_active('b.')}
        LIMIT 20
    """)).fetchall()
    if clashes:
        # don't guess which booking wins - somebody has to cancel or move one of each pair first
        pairs = ", ".join(f"{x}/{y}" for x, y in clashes)
        raise RuntimeError(f"visits already double-booked (visit id pairs): {pairs}. Cancel or move one of each, then rerun.")
    op.execute(f"""
        ALTER TABLE visits ADD CONSTRAINT ex_visits_specialist_slot
        EXCLUDE USING gist (specialist_id WITH =, ({_slot()}) WITH &&)
        WHERE ({_active()})
    """)


def downgrade():
    op.execute("ALTER TABLE visits DROP CONSTRAINT IF EXISTS ex_visits_specialist_slot")
