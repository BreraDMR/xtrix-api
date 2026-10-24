"""Keep open-ended waitlist preferences distinct from a single day."""
import hashlib
from alembic import op
import sqlalchemy as sa

revision = "0078_waitlist_open_range"
down_revision = "0077_visit_item_slots"
branch_labels = None
depends_on = None


def _key(row, open_marker):
    end = open_marker if row.preferred_date_to is None else row.preferred_date_to.isoformat()
    raw = "|".join((str(row.business_id), row.email.strip().lower(), str(row.service_id),
                    str(row.specialist_id or 0), row.preferred_date_from.isoformat(), end,
                    row.time_preference))
    return hashlib.sha256(raw.encode()).hexdigest()


def _rewrite(upgrade):
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE waitlist_entries IN ACCESS EXCLUSIVE MODE"))
    rows = connection.execute(sa.text("SELECT * FROM waitlist_entries ORDER BY id")).all()
    keys = {}
    changes = []
    for row in rows:
        marker = "*" if upgrade else row.preferred_date_from.isoformat()
        key = _key(row, marker)
        if row.status == "active":
            scope = (row.business_id, key)
            if scope in keys:
                raise RuntimeError(f"Waitlist range keys conflict for entries {keys[scope]} and {row.id}; "
                                   "resolve their duplicate preferences before changing this revision")
            keys[scope] = row.id
        changes.append({"entry_id": row.id, "key": key})
    if changes:
        connection.execute(sa.text("UPDATE waitlist_entries SET dedupe_key=:key WHERE id=:entry_id"), changes)


def upgrade():
    _rewrite(True)


def downgrade():
    _rewrite(False)
