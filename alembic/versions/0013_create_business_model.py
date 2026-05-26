"""create business model
Revision ID: 0013_create_business_model
Revises: 0012_add_global_client_account
Create Date: 2024-12-22 14:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text
revision = "0013_create_business_model"
down_revision = "0012_add_global_client_account"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'businesses' not in table_names:
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
    if 'users' in table_names:
        users_columns = [col['name'] for col in inspector.get_columns('users')]
        if 'business_id' not in users_columns:
            op.add_column("users", sa.Column("business_id", sa.Integer(), nullable=True))
            op.create_index("ix_users_business_id", "users", ["business_id"])
    if 'specialist_profiles' in table_names:
        specialist_profiles_columns = [col['name'] for col in inspector.get_columns('specialist_profiles')]
        if 'business_id' not in specialist_profiles_columns:
            op.add_column("specialist_profiles", sa.Column("business_id", sa.Integer(), nullable=True))
            op.create_index("ix_specialist_profiles_business_id", "specialist_profiles", ["business_id"])
    conn = op.get_bind()
    inspector = inspect(conn)
    if 'users' in table_names:
        try:
            fks = inspector.get_foreign_keys('users')
            fk_names = [fk['name'] for fk in fks] if fks else []
            if 'fk_users_business_id' not in fk_names:
                op.create_foreign_key(
                    "fk_users_business_id", 
                    "users", 
                    "businesses", 
                    ["business_id"], 
                    ["id"],
                    ondelete="SET NULL"
                )
        except Exception as e:
            print(f"Warning: Could not create FK fk_users_business_id: {e}")
    if 'specialist_profiles' in table_names:
        try:
            fks = inspector.get_foreign_keys('specialist_profiles')
            fk_names = [fk['name'] for fk in fks] if fks else []
            if 'fk_specialist_profiles_business_id' not in fk_names:
                op.create_foreign_key(
                    "fk_specialist_profiles_business_id",
                    "specialist_profiles",
                    "businesses",
                    ["business_id"],
                    ["id"],
                    ondelete="SET NULL"
                )
        except Exception as e:
            print(f"Warning: Could not create FK fk_specialist_profiles_business_id: {e}")
    try:
        user_count = conn.execute(text("SELECT COUNT(*) FROM users WHERE role IN ('specialist', 'admin')")).scalar()
        if user_count and user_count > 0:
            conn.execute(text("""
                INSERT INTO businesses (name, business_type, created_at)
                SELECT DISTINCT
                    COALESCE(sp.business_name, u.full_name || '''s Business', 'Individual Business') as name,
                    COALESCE(sp.business_type, 'individual') as business_type,
                    NOW() as created_at
                FROM users u
                LEFT JOIN specialist_profiles sp ON u.id = sp.user_id
                WHERE (u.role = 'specialist' OR u.role = 'admin')
                AND NOT EXISTS (
                    SELECT 1 FROM businesses b 
                    WHERE b.name = COALESCE(sp.business_name, u.full_name || '''s Business', 'Individual Business')
                    AND b.business_type = COALESCE(sp.business_type, 'individual')
                )
            """))
            conn.execute(text("""
                UPDATE users u
                SET business_id = (
                    SELECT b.id
                    FROM businesses b
                    WHERE b.name = COALESCE(
                        (SELECT sp.business_name FROM specialist_profiles sp WHERE sp.user_id = u.id LIMIT 1),
                        u.full_name || '''s Business',
                        'Individual Business'
                    )
                    AND b.business_type = COALESCE(
                        (SELECT sp.business_type FROM specialist_profiles sp WHERE sp.user_id = u.id LIMIT 1),
                        'individual'
                    )
                    LIMIT 1
                )
                WHERE u.business_id IS NULL
                AND (u.role = 'specialist' OR u.role = 'admin')
            """))
            conn.execute(text("""
                UPDATE specialist_profiles sp
                SET business_id = u.business_id
                FROM users u
                WHERE sp.user_id = u.id
                AND sp.business_id IS NULL
                AND u.business_id IS NOT NULL
            """))
    except Exception as e:
        print(f"Warning: Data migration failed (non-critical, structure already created): {e}")
        import traceback
        traceback.print_exc()
def downgrade():
    try:
        op.drop_constraint("fk_specialist_profiles_business_id", "specialist_profiles", type_="foreignkey")
    except:
        pass
    try:
        op.drop_constraint("fk_users_business_id", "users", type_="foreignkey")
    except:
        pass
    try:
        op.drop_index("ix_specialist_profiles_business_id", "specialist_profiles")
    except:
        pass
    try:
        op.drop_index("ix_users_business_id", "users")
    except:
        pass
    try:
        op.drop_column("specialist_profiles", "business_id")
    except:
        pass
    try:
        op.drop_column("users", "business_id")
    except:
        pass
    try:
        op.drop_index("ix_businesses_deleted_at", "businesses")
        op.drop_index("ix_businesses_created_at", "businesses")
        op.drop_index("ix_businesses_email", "businesses")
        op.drop_index("ix_businesses_phone", "businesses")
        op.drop_index("ix_businesses_business_type", "businesses")
        op.drop_index("ix_businesses_name", "businesses")
        op.drop_table("businesses")
    except:
        pass
