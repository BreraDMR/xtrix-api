"""add indexes soft delete and archiving

Revision ID: 0008_optimization_soft_delete
Revises: 0007_audit_logs
Create Date: 2024-01-08 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_optimization_soft_delete"
down_revision = "0007_audit_logs"
branch_labels = None
depends_on = None


def upgrade():
    # Add soft delete and archiving columns
    op.add_column("clients", sa.Column("deleted_at", sa.DateTime, nullable=True))
    op.add_column("clients", sa.Column("archived_at", sa.DateTime, nullable=True))
    op.add_column("visits", sa.Column("deleted_at", sa.DateTime, nullable=True))
    op.add_column("visits", sa.Column("archived_at", sa.DateTime, nullable=True))
    op.add_column("services", sa.Column("deleted_at", sa.DateTime, nullable=True))
    op.add_column("services", sa.Column("archived_at", sa.DateTime, nullable=True))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime, nullable=True))
    op.add_column("users", sa.Column("archived_at", sa.DateTime, nullable=True))
    
    # Add created_at to services if not exists
    try:
        op.add_column("services", sa.Column("created_at", sa.DateTime, nullable=True, server_default=sa.func.now()))
    except:
        pass  # Column might already exist
    
    # Create indexes for performance
    # Clients indexes
    op.create_index("ix_clients_owner_id", "clients", ["owner_id"])
    op.create_index("ix_clients_birth_date", "clients", ["birth_date"])
    op.create_index("ix_clients_gender", "clients", ["gender"])
    op.create_index("ix_clients_pregnancy", "clients", ["pregnancy"])
    op.create_index("ix_clients_spending", "clients", ["spending"])
    op.create_index("ix_clients_birthday_discount", "clients", ["birthday_discount"])
    op.create_index("ix_clients_created_at", "clients", ["created_at"])
    op.create_index("ix_clients_deleted_at", "clients", ["deleted_at"])
    op.create_index("ix_clients_archived_at", "clients", ["archived_at"])
    op.create_index("ix_clients_contact_email", "clients", ["contact_email"])
    
    # Composite indexes for common queries
    op.create_index("ix_clients_owner_deleted", "clients", ["owner_id", "deleted_at"])
    op.create_index("ix_clients_owner_archived", "clients", ["owner_id", "archived_at"])
    
    # Visits indexes
    op.create_index("ix_visits_date_end", "visits", ["date_end"])
    op.create_index("ix_visits_price", "visits", ["price"])
    op.create_index("ix_visits_status", "visits", ["status"])
    op.create_index("ix_visits_created_at", "visits", ["created_at"])
    op.create_index("ix_visits_deleted_at", "visits", ["deleted_at"])
    op.create_index("ix_visits_archived_at", "visits", ["archived_at"])
    
    # Composite indexes for visits
    op.create_index("ix_visits_client_date", "visits", ["client_id", "date_start"])
    op.create_index("ix_visits_specialist_date", "visits", ["specialist_id", "date_start"])
    op.create_index("ix_visits_status_date", "visits", ["status", "date_start"])
    op.create_index("ix_visits_client_deleted", "visits", ["client_id", "deleted_at"])
    
    # Services indexes
    op.create_index("ix_services_name", "services", ["name"])
    op.create_index("ix_services_price", "services", ["price"])
    op.create_index("ix_services_is_active", "services", ["is_active"])
    op.create_index("ix_services_created_at", "services", ["created_at"])
    op.create_index("ix_services_deleted_at", "services", ["deleted_at"])
    op.create_index("ix_services_archived_at", "services", ["archived_at"])
    op.create_index("ix_services_specialist_active", "services", ["specialist_id", "is_active"])
    
    # Users indexes
    # Note: ix_users_is_locked was already created in migration 0005_account_lockout
    op.create_index("ix_users_full_name", "users", ["full_name"])
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_users_locked_until", "users", ["locked_until"])
    # Skip ix_users_is_locked - already exists from migration 0005
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])
    op.create_index("ix_users_archived_at", "users", ["archived_at"])


def downgrade():
    # Drop indexes
    op.drop_index("ix_users_archived_at", "users")
    op.drop_index("ix_users_deleted_at", "users")
    # Note: ix_users_is_locked was created in migration 0005, so don't drop it here
    op.drop_index("ix_users_locked_until", "users")
    op.drop_index("ix_users_created_at", "users")
    op.drop_index("ix_users_role", "users")
    op.drop_index("ix_users_full_name", "users")
    
    op.drop_index("ix_services_specialist_active", "services")
    op.drop_index("ix_services_archived_at", "services")
    op.drop_index("ix_services_deleted_at", "services")
    op.drop_index("ix_services_created_at", "services")
    op.drop_index("ix_services_is_active", "services")
    op.drop_index("ix_services_price", "services")
    op.drop_index("ix_services_name", "services")
    
    op.drop_index("ix_visits_client_deleted", "visits")
    op.drop_index("ix_visits_status_date", "visits")
    op.drop_index("ix_visits_specialist_date", "visits")
    op.drop_index("ix_visits_client_date", "visits")
    op.drop_index("ix_visits_archived_at", "visits")
    op.drop_index("ix_visits_deleted_at", "visits")
    op.drop_index("ix_visits_created_at", "visits")
    op.drop_index("ix_visits_status", "visits")
    op.drop_index("ix_visits_price", "visits")
    op.drop_index("ix_visits_date_end", "visits")
    
    op.drop_index("ix_clients_owner_archived", "clients")
    op.drop_index("ix_clients_owner_deleted", "clients")
    op.drop_index("ix_clients_contact_email", "clients")
    op.drop_index("ix_clients_archived_at", "clients")
    op.drop_index("ix_clients_deleted_at", "clients")
    op.drop_index("ix_clients_created_at", "clients")
    op.drop_index("ix_clients_birthday_discount", "clients")
    op.drop_index("ix_clients_spending", "clients")
    op.drop_index("ix_clients_pregnancy", "clients")
    op.drop_index("ix_clients_gender", "clients")
    op.drop_index("ix_clients_birth_date", "clients")
    op.drop_index("ix_clients_owner_id", "clients")
    
    # Drop columns
    op.drop_column("users", "archived_at")
    op.drop_column("users", "deleted_at")
    op.drop_column("services", "archived_at")
    op.drop_column("services", "deleted_at")
    op.drop_column("services", "created_at")
    op.drop_column("visits", "archived_at")
    op.drop_column("visits", "deleted_at")
    op.drop_column("clients", "archived_at")
    op.drop_column("clients", "deleted_at")


