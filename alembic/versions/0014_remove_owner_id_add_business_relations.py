"""remove owner_id add business relations

Revision ID: 0014_remove_owner_id_add_business_relations
Revises: 0013_create_business_model
Create Date: 2024-12-22 15:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0014_remove_owner_id_add_business_relations"
down_revision = "0013_create_business_model"
branch_labels = None
depends_on = None


def upgrade():
    # Create client_business_relations table
    op.create_table(
        "client_business_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spending", sa.Float(), server_default="0.0"),
        sa.Column("birthday_discount", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("personal_notes", sa.Text(), nullable=True),
        sa.Column("inventory", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_client_business_relations_client_id", "client_business_relations", ["client_id"])
    op.create_index("ix_client_business_relations_business_id", "client_business_relations", ["business_id"])
    op.create_index("ix_client_business_relations_spending", "client_business_relations", ["spending"])
    op.create_index("ix_client_business_relations_birthday_discount", "client_business_relations", ["birthday_discount"])
    op.create_index("ix_client_business_relations_created_at", "client_business_relations", ["created_at"])
    op.create_unique_constraint("uq_client_business", "client_business_relations", ["client_id", "business_id"])
    
    # Add business_id to client_specialist_relations
    op.add_column("client_specialist_relations", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_client_specialist_relations_business_id",
        "client_specialist_relations",
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="CASCADE"
    )
    op.create_index("ix_client_specialist_relations_business_id", "client_specialist_relations", ["business_id"])
    
    # Migrate existing data: create ClientBusinessRelation from Client.owner_id
    # Для каждого клиента с owner_id находим business_id через User и создаем связь
    # Убираем DISTINCT, так как PostgreSQL не может использовать JSON тип в DISTINCT
    # NOT EXISTS уже предотвращает дубликаты благодаря уникальному ограничению
    op.execute("""
        INSERT INTO client_business_relations (client_id, business_id, spending, birthday_discount, personal_notes, inventory, created_at)
        SELECT
            c.id as client_id,
            u.business_id,
            COALESCE(c.spending, 0.0) as spending,
            COALESCE(c.birthday_discount, false) as birthday_discount,
            c.personal_notes,
            c.inventory,
            c.created_at
        FROM clients c
        JOIN users u ON c.owner_id = u.id
        WHERE c.owner_id IS NOT NULL
        AND u.business_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM client_business_relations cbr 
            WHERE cbr.client_id = c.id AND cbr.business_id = u.business_id
        )
    """)
    
    # Обновляем business_id в client_specialist_relations на основе существующих связей
    op.execute("""
        UPDATE client_specialist_relations csr
        SET business_id = u.business_id
        FROM users u
        WHERE csr.specialist_id = u.id
        AND csr.business_id IS NULL
        AND u.business_id IS NOT NULL
    """)
    
    # Если есть связи без business_id, создаем их на основе client_business_relations
    op.execute("""
        UPDATE client_specialist_relations csr
        SET business_id = cbr.business_id
        FROM client_business_relations cbr
        WHERE csr.client_id = cbr.client_id
        AND csr.business_id IS NULL
    """)
    
    # Теперь делаем business_id NOT NULL в client_specialist_relations
    # Сначала устанавливаем значения для NULL записей (берем первый доступный бизнес)
    op.execute("""
        UPDATE client_specialist_relations csr
        SET business_id = (
            SELECT u.business_id 
            FROM users u 
            WHERE u.id = csr.specialist_id 
            AND u.business_id IS NOT NULL 
            LIMIT 1
        )
        WHERE csr.business_id IS NULL
    """)
    
    # Теперь можно сделать NOT NULL (если все записи имеют business_id)
    # Но оставим nullable для безопасности, если есть записи без business_id
    
    # Drop old unique constraint and create new one with business_id
    op.drop_constraint("uq_client_specialist", "client_specialist_relations", type_="unique")
    op.create_unique_constraint(
        "uq_client_specialist_business",
        "client_specialist_relations",
        ["client_id", "specialist_id", "business_id"]
    )
    
    # Удаляем бизнес-специфичные столбцы из clients (данные перенесены в client_business_relations)
    # Но оставляем их пока для обратной совместимости, просто делаем nullable
    # Полное удаление будет в следующей миграции после проверки
    op.alter_column("clients", "spending", nullable=True)
    op.alter_column("clients", "birthday_discount", nullable=True)
    op.alter_column("clients", "personal_notes", nullable=True)
    op.alter_column("clients", "inventory", nullable=True)
    
    # Удаляем медицинские поля из clients (будут перенесены в MedicalProfile в следующей миграции)
    # Пока оставляем, но делаем nullable
    op.alter_column("clients", "allergies", nullable=True)
    op.alter_column("clients", "chronic_diseases", nullable=True)
    op.alter_column("clients", "pregnancy", nullable=True)
    op.alter_column("clients", "ontology", nullable=True)
    op.alter_column("clients", "viral_diseases", nullable=True)


def downgrade():
    # Restore columns (if needed, but data might be lost)
    op.alter_column("clients", "spending", nullable=False, server_default="0.0")
    op.alter_column("clients", "birthday_discount", nullable=False, server_default=sa.text("false"))
    op.alter_column("clients", "personal_notes", nullable=True)
    op.alter_column("clients", "inventory", nullable=True)
    op.alter_column("clients", "allergies", nullable=True)
    op.alter_column("clients", "chronic_diseases", nullable=True)
    op.alter_column("clients", "pregnancy", nullable=True)
    op.alter_column("clients", "ontology", nullable=True)
    op.alter_column("clients", "viral_diseases", nullable=True)
    
    # Restore old unique constraint
    op.drop_constraint("uq_client_specialist_business", "client_specialist_relations", type_="unique")
    op.create_unique_constraint(
        "uq_client_specialist",
        "client_specialist_relations",
        ["client_id", "specialist_id"]
    )
    
    # Remove business_id from client_specialist_relations
    op.drop_index("ix_client_specialist_relations_business_id", "client_specialist_relations")
    op.drop_constraint("fk_client_specialist_relations_business_id", "client_specialist_relations", type_="foreignkey")
    op.drop_column("client_specialist_relations", "business_id")
    
    # Drop client_business_relations table
    op.drop_constraint("uq_client_business", "client_business_relations", type_="unique")
    op.drop_index("ix_client_business_relations_created_at", "client_business_relations")
    op.drop_index("ix_client_business_relations_birthday_discount", "client_business_relations")
    op.drop_index("ix_client_business_relations_spending", "client_business_relations")
    op.drop_index("ix_client_business_relations_business_id", "client_business_relations")
    op.drop_index("ix_client_business_relations_client_id", "client_business_relations")
    op.drop_table("client_business_relations")

