"""add business_id to visits and services
Revision ID: 0015_add_business_id_to_visits_services
Revises: 0014_remove_owner_id_add_business_relations
Create Date: 2024-12-22 16:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0015_add_business_to_visits_svc"
down_revision = "0014_remove_owner_add_business"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("visits", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_visits_business_id",
        "visits",
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="CASCADE"
    )
    op.create_index("ix_visits_business_id", "visits", ["business_id"])
    op.create_index("ix_visits_business_client", "visits", ["business_id", "client_id"])
    op.create_index("ix_visits_business_specialist", "visits", ["business_id", "specialist_id"])
    op.execute("""
        UPDATE visits v
        SET business_id = u.business_id
        FROM users u
        WHERE v.specialist_id = u.id
        AND v.business_id IS NULL
        AND u.business_id IS NOT NULL
    """)
    op.execute("""
        UPDATE visits v
        SET business_id = cbr.business_id
        FROM client_business_relations cbr
        WHERE v.client_id = cbr.client_id
        AND v.business_id IS NULL
        AND cbr.business_id IS NOT NULL
    """)
    op.execute("""
        UPDATE visits v
        SET business_id = (
            SELECT cbr.business_id 
            FROM client_business_relations cbr 
            WHERE cbr.client_id = v.client_id 
            LIMIT 1
        )
        WHERE v.business_id IS NULL
    """)
    op.add_column("services", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_services_business_id",
        "services",
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="CASCADE"
    )
    op.create_index("ix_services_business_id", "services", ["business_id"])
    op.create_index("ix_services_business_specialist", "services", ["business_id", "specialist_id"])
    op.execute("""
        UPDATE services s
        SET business_id = u.business_id
        FROM users u
        WHERE s.specialist_id = u.id
        AND s.business_id IS NULL
        AND u.business_id IS NOT NULL
    """)
def downgrade():
    op.drop_index("ix_services_business_specialist", "services")
    op.drop_index("ix_visits_business_specialist", "visits")
    op.drop_index("ix_visits_business_client", "visits")
    op.drop_index("ix_services_business_id", "services")
    op.drop_constraint("fk_services_business_id", "services", type_="foreignkey")
    op.drop_column("services", "business_id")
    op.drop_index("ix_visits_business_id", "visits")
    op.drop_constraint("fk_visits_business_id", "visits", type_="foreignkey")
    op.drop_column("visits", "business_id")
