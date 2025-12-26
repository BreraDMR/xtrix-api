"""create business model

Revision ID: 0013_create_business_model
Revises: 0012_add_global_client_account
Create Date: 2024-12-22 14:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_create_business_model"
down_revision = "0012_add_global_client_account"
branch_labels = None
depends_on = None


def upgrade():
    # Create businesses table
    op.create_table(
        "businesses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("business_type", sa.String(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_businesses_name", "businesses", ["name"])
    op.create_index("ix_businesses_business_type", "businesses", ["business_type"])
    op.create_index("ix_businesses_phone", "businesses", ["phone"])
    op.create_index("ix_businesses_email", "businesses", ["email"])
    op.create_index("ix_businesses_created_at", "businesses", ["created_at"])
    op.create_index("ix_businesses_deleted_at", "businesses", ["deleted_at"])
    
    # Add business_id to users table
    op.add_column("users", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_business_id", 
        "users", 
        "businesses", 
        ["business_id"], 
        ["id"],
        ondelete="SET NULL"
    )
    op.create_index("ix_users_business_id", "users", ["business_id"])
    
    # Add business_id to specialist_profiles table
    op.add_column("specialist_profiles", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_specialist_profiles_business_id",
        "specialist_profiles",
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="SET NULL"
    )
    op.create_index("ix_specialist_profiles_business_id", "specialist_profiles", ["business_id"])
    
    # Migrate existing data: create businesses from specialist_profiles
    # Для каждого уникального специалиста создаем бизнес на основе его профиля
    # Используем подзапрос с NOT EXISTS для избежания дубликатов
    op.execute("""
        INSERT INTO businesses (name, business_type, address, description, created_at)
        SELECT DISTINCT
            COALESCE(sp.business_name, u.full_name || '''s Business') as name,
            COALESCE(sp.business_type, 'individual') as business_type,
            MAX(sp.address) as address,
            MAX(sp.description) as description,
            NOW() as created_at
        FROM users u
        LEFT JOIN specialist_profiles sp ON u.id = sp.user_id
        WHERE (u.role = 'specialist' OR u.role = 'admin')
        AND NOT EXISTS (
            SELECT 1 FROM businesses b 
            WHERE b.name = COALESCE(sp.business_name, u.full_name || '''s Business')
            AND b.business_type = COALESCE(sp.business_type, 'individual')
        )
        GROUP BY COALESCE(sp.business_name, u.full_name || '''s Business'), COALESCE(sp.business_type, 'individual')
    """)
    
    # Обновляем business_id в users на основе созданных бизнесов
    # Используем подзапрос вместо JOIN, чтобы избежать ошибки ссылки на таблицу в UPDATE
    op.execute("""
        UPDATE users u
        SET business_id = (
            SELECT b.id
            FROM specialist_profiles sp
            JOIN businesses b ON (
                b.name = COALESCE(sp.business_name, u.full_name || '''s Business')
                AND b.business_type = COALESCE(sp.business_type, 'individual')
            )
            WHERE sp.user_id = u.id
            LIMIT 1
        )
        WHERE u.business_id IS NULL
        AND EXISTS (
            SELECT 1 FROM specialist_profiles sp2 WHERE sp2.user_id = u.id
        )
    """)
    
    # Обновляем business_id в specialist_profiles
    op.execute("""
        UPDATE specialist_profiles sp
        SET business_id = u.business_id
        FROM users u
        WHERE sp.user_id = u.id
        AND sp.business_id IS NULL
        AND u.business_id IS NOT NULL
    """)


def downgrade():
    # Remove business_id from specialist_profiles
    op.drop_index("ix_specialist_profiles_business_id", "specialist_profiles")
    op.drop_constraint("fk_specialist_profiles_business_id", "specialist_profiles", type_="foreignkey")
    op.drop_column("specialist_profiles", "business_id")
    
    # Remove business_id from users
    op.drop_index("ix_users_business_id", "users")
    op.drop_constraint("fk_users_business_id", "users", type_="foreignkey")
    op.drop_column("users", "business_id")
    
    # Drop businesses table
    op.drop_index("ix_businesses_deleted_at", "businesses")
    op.drop_index("ix_businesses_created_at", "businesses")
    op.drop_index("ix_businesses_email", "businesses")
    op.drop_index("ix_businesses_phone", "businesses")
    op.drop_index("ix_businesses_business_type", "businesses")
    op.drop_index("ix_businesses_name", "businesses")
    op.drop_table("businesses")

