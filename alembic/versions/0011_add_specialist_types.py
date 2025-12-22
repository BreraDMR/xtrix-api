"""add specialist types and profiles

Revision ID: 0011_add_specialist_types
Revises: 0010_password_reset_tokens
Create Date: 2024-12-18 12:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_add_specialist_types"
down_revision = "0010_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade():
    # Add specialist_type column to users table (nullable for backward compatibility)
    op.add_column("users", sa.Column("specialist_type", sa.String(), nullable=True))
    op.create_index("ix_users_specialist_type", "users", ["specialist_type"])
    
    # Create specialist_profiles table
    op.create_table(
        "specialist_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("specialist_type", sa.String(), nullable=True),
        sa.Column("specialist_category", sa.String(), nullable=True),
        sa.Column("specialization", sa.Text(), nullable=True),
        sa.Column("experience_years", sa.Integer(), nullable=True),
        sa.Column("education", sa.Text(), nullable=True),
        sa.Column("certifications", sa.Text(), nullable=True),
        sa.Column("business_name", sa.String(), nullable=True),
        sa.Column("business_type", sa.String(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("languages", sa.String(), nullable=True),
        sa.Column("is_verified", sa.String(), nullable=False, server_default="pending"),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_specialist_profiles_user_id", "specialist_profiles", ["user_id"], unique=True)
    op.create_index("ix_specialist_profiles_specialist_type", "specialist_profiles", ["specialist_type"])
    op.create_index("ix_specialist_profiles_specialist_category", "specialist_profiles", ["specialist_category"])


def downgrade():
    # Drop specialist_profiles table
    op.drop_index("ix_specialist_profiles_specialist_category", "specialist_profiles")
    op.drop_index("ix_specialist_profiles_specialist_type", "specialist_profiles")
    op.drop_index("ix_specialist_profiles_user_id", "specialist_profiles")
    op.drop_table("specialist_profiles")
    
    # Remove specialist_type column from users
    op.drop_index("ix_users_specialist_type", "users")
    op.drop_column("users", "specialist_type")



