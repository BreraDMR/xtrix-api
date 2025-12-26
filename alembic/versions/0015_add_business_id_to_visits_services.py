"""add business_id to visits and services

Revision ID: 0015_add_business_id_to_visits_services
Revises: 0014_remove_owner_id_add_business_relations
Create Date: 2024-12-22 16:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_add_business_id_to_visits_services"
down_revision = "0014_remove_owner_id_add_business_relations"
branch_labels = None
depends_on = None


def upgrade():
    # Add business_id to visits table
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
    
    # Add composite index for better query performance (business_id + client_id, business_id + specialist_id)
    op.create_index("ix_visits_business_client", "visits", ["business_id", "client_id"])
    op.create_index("ix_visits_business_specialist", "visits", ["business_id", "specialist_id"])
    
    # Migrate existing data: set business_id from specialist_id → User → business_id
    op.execute("""
        UPDATE visits v
        SET business_id = u.business_id
        FROM users u
        WHERE v.specialist_id = u.id
        AND v.business_id IS NULL
        AND u.business_id IS NOT NULL
    """)
    
    # For visits without business_id, try to get it from client_business_relations
    op.execute("""
        UPDATE visits v
        SET business_id = cbr.business_id
        FROM client_business_relations cbr
        WHERE v.client_id = cbr.client_id
        AND v.business_id IS NULL
        AND cbr.business_id IS NOT NULL
    """)
    
    # If still NULL, try to get from first available business relation
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
    
    # Add business_id to services table
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
    
    # Add composite index for better query performance
    op.create_index("ix_services_business_specialist", "services", ["business_id", "specialist_id"])
    
    # Migrate existing data: set business_id from specialist_id → User → business_id
    op.execute("""
        UPDATE services s
        SET business_id = u.business_id
        FROM users u
        WHERE s.specialist_id = u.id
        AND s.business_id IS NULL
        AND u.business_id IS NOT NULL
    """)
    
    # Note: business_id remains nullable for safety
    # Set NOT NULL only after ensuring all records have business_id
    # This can be done manually or in a follow-up migration after data verification


def downgrade():
    # Remove composite indexes
    op.drop_index("ix_services_business_specialist", "services")
    op.drop_index("ix_visits_business_specialist", "visits")
    op.drop_index("ix_visits_business_client", "visits")
    
    # Remove business_id from services
    op.drop_index("ix_services_business_id", "services")
    op.drop_constraint("fk_services_business_id", "services", type_="foreignkey")
    op.drop_column("services", "business_id")
    
    # Remove business_id from visits
    op.drop_index("ix_visits_business_id", "visits")
    op.drop_constraint("fk_visits_business_id", "visits", type_="foreignkey")
    op.drop_column("visits", "business_id")

