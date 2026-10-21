"""Add ordered services (items) to visits and backfill every existing visit."""
from alembic import op
import sqlalchemy as sa

revision = "0068_visit_items"
down_revision = "0067_waitlist"
branch_labels = None
depends_on = None

_DURATION = """CASE
    WHEN v.date_end IS NOT NULL AND v.date_end > v.date_start
    THEN GREATEST(1, ROUND(EXTRACT(EPOCH FROM (v.date_end - v.date_start)) / 60.0))::int
    ELSE GREATEST(COALESCE(s.duration_minutes, 60), 1)
END"""


def upgrade():
    op.create_table(
        "visit_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id"),
            nullable=True,
        ),
        sa.Column(
            "specialist_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("service_name", sa.String(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "buffer_before_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "buffer_after_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("date_start", sa.DateTime(), nullable=False),
        sa.Column("date_end", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("visit_id", "position", name="uq_visit_item_position"),
    )
    op.create_index("ix_visit_items_id", "visit_items", ["id"])
    op.create_index("ix_visit_items_visit_id", "visit_items", ["visit_id"])
    op.create_index("ix_visit_items_service_id", "visit_items", ["service_id"])
    op.create_index("ix_visit_items_specialist_id", "visit_items", ["specialist_id"])
    op.create_index("ix_visit_items_date_start", "visit_items", ["date_start"])
    op.create_index("ix_visit_items_date_end", "visit_items", ["date_end"])

    op.execute(
        f"""
        INSERT INTO visit_items (
            visit_id, position, service_id, specialist_id, service_name, price,
            duration_minutes, buffer_before_minutes, buffer_after_minutes,
            date_start, date_end
        )
        SELECT
            v.id,
            0,
            v.service_id,
            v.specialist_id,
            v.service_name,
            CASE WHEN v.price IS NOT NULL THEN ROUND(v.price::numeric, 2) END,
            {_DURATION},
            0,
            0,
            v.date_start,
            CASE
                WHEN v.date_end IS NOT NULL AND v.date_end > v.date_start THEN v.date_end
                ELSE v.date_start + ({_DURATION}) * interval '1 minute'
            END
        FROM visits v
        LEFT JOIN services s ON s.id = v.service_id
        """
    )


def downgrade():
    op.drop_index("ix_visit_items_date_end", table_name="visit_items")
    op.drop_index("ix_visit_items_date_start", table_name="visit_items")
    op.drop_index("ix_visit_items_specialist_id", table_name="visit_items")
    op.drop_index("ix_visit_items_service_id", table_name="visit_items")
    op.drop_index("ix_visit_items_visit_id", table_name="visit_items")
    op.drop_index("ix_visit_items_id", table_name="visit_items")
    op.drop_table("visit_items")
