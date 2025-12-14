"""init schema

Revision ID: 0001_init
Revises:
Create Date: 2024-01-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String, nullable=False),
        sa.Column("phone", sa.String, nullable=True, unique=True),
        sa.Column("email", sa.String, nullable=True, unique=True),
        sa.Column("hashed_password", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False, server_default="specialist"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_users_phone", "users", ["phone"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("full_name", sa.String, nullable=False),
        sa.Column("birth_date", sa.Date, nullable=True),
        sa.Column("gender", sa.String, nullable=True),
        sa.Column("allergies", sa.Text, nullable=True),
        sa.Column("chronic_diseases", sa.Text, nullable=True),
        sa.Column("pregnancy", sa.Boolean, server_default=sa.text("false")),
        sa.Column("ontology", sa.JSON, nullable=True),
        sa.Column("contact_phone", sa.String, nullable=True),
        sa.Column("contact_email", sa.String, nullable=True),
        sa.Column("viral_diseases", sa.Text, nullable=True),
        sa.Column("inventory", sa.JSON, nullable=True),
        sa.Column("spending", sa.Float, server_default="0"),
        sa.Column("birthday_discount", sa.Boolean, server_default=sa.text("false")),
        sa.Column("personal_notes", sa.Text, nullable=True),
        sa.Column("location_lat", sa.Float, nullable=True),
        sa.Column("location_lon", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_clients_full_name", "clients", ["full_name"])
    op.create_index("ix_clients_phone", "clients", ["contact_phone"])

    op.create_table(
        "services",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("specialist_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true")),
    )
    op.create_index("ix_services_specialist", "services", ["specialist_id"])

    op.create_table(
        "visits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("specialist_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("services.id"), nullable=True),
        sa.Column("date_start", sa.DateTime, nullable=False),
        sa.Column("date_end", sa.DateTime, nullable=True),
        sa.Column("service_name", sa.String, nullable=True),
        sa.Column("note", sa.String, nullable=True),
        sa.Column("photo_url", sa.String, nullable=True),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("status", sa.String, server_default="planned"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_visits_client", "visits", ["client_id"])
    op.create_index("ix_visits_specialist", "visits", ["specialist_id"])
    op.create_index("ix_visits_date", "visits", ["date_start"])


def downgrade():
    op.drop_table("visits")
    op.drop_table("services")
    op.drop_table("clients")
    op.drop_table("users")











