"""create expenses and expense categories tables

Revision ID: 0025_expenses
Revises: 0024_products
Create Date: 2025-01-05 17:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_expenses"
down_revision = "0024_products"
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    
    # Создаем таблицу expense_categories
    if 'expense_categories' not in table_names:
        op.create_table(
            'expense_categories',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('business_id', sa.Integer(), nullable=True),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('type', sa.String(), nullable=False, server_default='variable'),
            sa.Column('color', sa.String(), nullable=True),
            sa.Column('is_system', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_expense_categories_id', 'expense_categories', ['id'], unique=False)
        op.create_index('ix_expense_categories_business_id', 'expense_categories', ['business_id'], unique=False)
        op.create_index('ix_expense_categories_name', 'expense_categories', ['name'], unique=False)
        op.create_index('ix_expense_categories_type', 'expense_categories', ['type'], unique=False)
        op.create_index('ix_expense_categories_is_system', 'expense_categories', ['is_system'], unique=False)
        op.create_index('ix_expense_categories_is_active', 'expense_categories', ['is_active'], unique=False)
        op.create_index('ix_expense_categories_created_at', 'expense_categories', ['created_at'], unique=False)
        
        # Создаем системные категории по умолчанию
        op.execute("""
            INSERT INTO expense_categories (name, type, color, is_system, is_active, created_at)
            VALUES
                ('Аренда', 'fixed', '#EF4444', true, true, NOW()),
                ('Зарплаты', 'variable', '#F59E0B', true, true, NOW()),
                ('Закупка товаров', 'variable', '#10B981', true, true, NOW()),
                ('Маркетинг', 'variable', '#3B82F6', true, true, NOW()),
                ('Коммунальные услуги', 'fixed', '#8B5CF6', true, true, NOW()),
                ('Сервисы / Подписки', 'fixed', '#EC4899', true, true, NOW()),
                ('Прочее', 'variable', '#6B7280', true, true, NOW())
        """)
    
    # Создаем таблицу expenses
    if 'expenses' not in table_names:
        op.create_table(
            'expenses',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('business_id', sa.Integer(), nullable=False),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('currency', sa.String(), nullable=False, server_default='EUR'),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('expense_date', sa.DateTime(), nullable=False),
            sa.Column('payment_method', sa.String(), nullable=False, server_default='other'),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['category_id'], ['expense_categories.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_expenses_id', 'expenses', ['id'], unique=False)
        op.create_index('ix_expenses_business_id', 'expenses', ['business_id'], unique=False)
        op.create_index('ix_expenses_category_id', 'expenses', ['category_id'], unique=False)
        op.create_index('ix_expenses_amount', 'expenses', ['amount'], unique=False)
        op.create_index('ix_expenses_currency', 'expenses', ['currency'], unique=False)
        op.create_index('ix_expenses_expense_date', 'expenses', ['expense_date'], unique=False)
        op.create_index('ix_expenses_payment_method', 'expenses', ['payment_method'], unique=False)
        op.create_index('ix_expenses_created_by_id', 'expenses', ['created_by_id'], unique=False)
        op.create_index('ix_expenses_created_at', 'expenses', ['created_at'], unique=False)
        op.create_index('ix_expenses_updated_at', 'expenses', ['updated_at'], unique=False)


def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    
    # Удаляем таблицу expenses
    if 'expenses' in table_names:
        op.drop_index('ix_expenses_updated_at', table_name='expenses')
        op.drop_index('ix_expenses_created_at', table_name='expenses')
        op.drop_index('ix_expenses_created_by_id', table_name='expenses')
        op.drop_index('ix_expenses_payment_method', table_name='expenses')
        op.drop_index('ix_expenses_expense_date', table_name='expenses')
        op.drop_index('ix_expenses_currency', table_name='expenses')
        op.drop_index('ix_expenses_amount', table_name='expenses')
        op.drop_index('ix_expenses_category_id', table_name='expenses')
        op.drop_index('ix_expenses_business_id', table_name='expenses')
        op.drop_index('ix_expenses_id', table_name='expenses')
        op.drop_table('expenses')
    
    # Удаляем таблицу expense_categories
    if 'expense_categories' in table_names:
        op.drop_index('ix_expense_categories_created_at', table_name='expense_categories')
        op.drop_index('ix_expense_categories_is_active', table_name='expense_categories')
        op.drop_index('ix_expense_categories_is_system', table_name='expense_categories')
        op.drop_index('ix_expense_categories_type', table_name='expense_categories')
        op.drop_index('ix_expense_categories_name', table_name='expense_categories')
        op.drop_index('ix_expense_categories_business_id', table_name='expense_categories')
        op.drop_index('ix_expense_categories_id', table_name='expense_categories')
        op.drop_table('expense_categories')

