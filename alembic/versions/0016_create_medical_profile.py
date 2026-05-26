"""create medical profile
Revision ID: 0016_create_medical_profile
Revises: 0015_add_business_id_to_visits_services
Create Date: 2024-12-22 17:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0016_create_medical_profile"
down_revision = "0015_add_business_to_visits_svc"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table(
        "medical_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("allergies", sa.Text(), nullable=True),
        sa.Column("chronic_diseases", sa.Text(), nullable=True),
        sa.Column("pregnancy", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("viral_diseases", sa.Text(), nullable=True),
        sa.Column("ontology", sa.JSON(), nullable=True),
        sa.Column("blood_type", sa.Text(), nullable=True),
        sa.Column("medications", sa.JSON(), nullable=True),
        sa.Column("medical_history", sa.Text(), nullable=True),
        sa.Column("emergency_contact", sa.JSON(), nullable=True),
        sa.Column("insurance_info", sa.JSON(), nullable=True),
        sa.Column("doctor_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_medical_profiles_client_id", "medical_profiles", ["client_id"], unique=True)
    op.create_index("ix_medical_profiles_pregnancy", "medical_profiles", ["pregnancy"])
    op.create_index("ix_medical_profiles_created_at", "medical_profiles", ["created_at"])
    op.create_index("ix_medical_profiles_updated_at", "medical_profiles", ["updated_at"])
    op.create_index("ix_medical_profiles_deleted_at", "medical_profiles", ["deleted_at"])
    op.execute("""
        INSERT INTO medical_profiles (
            client_id, allergies, chronic_diseases, pregnancy, viral_diseases, ontology, created_at, updated_at
        )
        SELECT 
            id as client_id,
            allergies,
            chronic_diseases,
            COALESCE(pregnancy, false) as pregnancy,
            viral_diseases,
            ontology,
            created_at,
            NOW() as updated_at
        FROM clients
        WHERE (
            allergies IS NOT NULL 
            OR chronic_diseases IS NOT NULL 
            OR pregnancy = true
            OR viral_diseases IS NOT NULL
            OR ontology IS NOT NULL
        )
        AND NOT EXISTS (
            SELECT 1 FROM medical_profiles mp WHERE mp.client_id = clients.id
        )
    """)
    op.alter_column("clients", "allergies", nullable=True)
    op.alter_column("clients", "chronic_diseases", nullable=True)
    op.alter_column("clients", "pregnancy", nullable=True)
    op.alter_column("clients", "ontology", nullable=True)
    op.alter_column("clients", "viral_diseases", nullable=True)
def downgrade():
    op.alter_column("clients", "allergies", nullable=True)
    op.alter_column("clients", "chronic_diseases", nullable=True)
    op.alter_column("clients", "pregnancy", nullable=True)
    op.alter_column("clients", "ontology", nullable=True)
    op.alter_column("clients", "viral_diseases", nullable=True)
    op.drop_index("ix_medical_profiles_deleted_at", "medical_profiles")
    op.drop_index("ix_medical_profiles_updated_at", "medical_profiles")
    op.drop_index("ix_medical_profiles_created_at", "medical_profiles")
    op.drop_index("ix_medical_profiles_pregnancy", "medical_profiles")
    op.drop_index("ix_medical_profiles_client_id", "medical_profiles")
    op.drop_table("medical_profiles")
