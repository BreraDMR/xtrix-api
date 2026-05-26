"""create products and product usage tables
Revision ID: 0024_products
Revises: 0023_transactions
Create Date: 2025-01-05 16:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0024_products"
down_revision = "0023_transactions"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'products' not in table_names:
        op.create_table(
            'products',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('business_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('unit', sa.String(), nullable=False, server_default='шт'),
            sa.Column('quantity', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('min_quantity', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('price_per_unit', sa.Float(), nullable=True),
            sa.Column('category', sa.String(), nullable=True),
            sa.Column('barcode', sa.String(), nullable=True),
            sa.Column('supplier', sa.String(), nullable=True),
            sa.Column('location', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_products_id', 'products', ['id'], unique=False)
        op.create_index('ix_products_business_id', 'products', ['business_id'], unique=False)
        op.create_index('ix_products_name', 'products', ['name'], unique=False)
        op.create_index('ix_products_quantity', 'products', ['quantity'], unique=False)
        op.create_index('ix_products_min_quantity', 'products', ['min_quantity'], unique=False)
        op.create_index('ix_products_category', 'products', ['category'], unique=False)
        op.create_index('ix_products_barcode', 'products', ['barcode'], unique=True)
        op.create_index('ix_products_is_active', 'products', ['is_active'], unique=False)
        op.create_index('ix_products_created_at', 'products', ['created_at'], unique=False)
        op.create_index('ix_products_updated_at', 'products', ['updated_at'], unique=False)
    if 'product_usages' not in table_names:
        op.create_table(
            'product_usages',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('product_id', sa.Integer(), nullable=False),
            sa.Column('visit_id', sa.Integer(), nullable=True),
            sa.Column('business_id', sa.Integer(), nullable=False),
            sa.Column('quantity', sa.Float(), nullable=False),
            sa.Column('used_by_id', sa.Integer(), nullable=True),
            sa.Column('client_id', sa.Integer(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('usage_date', sa.DateTime(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['used_by_id'], ['users.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_product_usages_id', 'product_usages', ['id'], unique=False)
        op.create_index('ix_product_usages_product_id', 'product_usages', ['product_id'], unique=False)
        op.create_index('ix_product_usages_visit_id', 'product_usages', ['visit_id'], unique=False)
        op.create_index('ix_product_usages_business_id', 'product_usages', ['business_id'], unique=False)
        op.create_index('ix_product_usages_quantity', 'product_usages', ['quantity'], unique=False)
        op.create_index('ix_product_usages_used_by_id', 'product_usages', ['used_by_id'], unique=False)
        op.create_index('ix_product_usages_client_id', 'product_usages', ['client_id'], unique=False)
        op.create_index('ix_product_usages_usage_date', 'product_usages', ['usage_date'], unique=False)
        op.create_index('ix_product_usages_created_at', 'product_usages', ['created_at'], unique=False)
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'product_usages' in table_names:
        op.drop_index('ix_product_usages_created_at', table_name='product_usages')
        op.drop_index('ix_product_usages_usage_date', table_name='product_usages')
        op.drop_index('ix_product_usages_client_id', table_name='product_usages')
        op.drop_index('ix_product_usages_used_by_id', table_name='product_usages')
        op.drop_index('ix_product_usages_quantity', table_name='product_usages')
        op.drop_index('ix_product_usages_business_id', table_name='product_usages')
        op.drop_index('ix_product_usages_visit_id', table_name='product_usages')
        op.drop_index('ix_product_usages_product_id', table_name='product_usages')
        op.drop_index('ix_product_usages_id', table_name='product_usages')
        op.drop_table('product_usages')
    if 'products' in table_names:
        op.drop_index('ix_products_updated_at', table_name='products')
        op.drop_index('ix_products_created_at', table_name='products')
        op.drop_index('ix_products_is_active', table_name='products')
        op.drop_index('ix_products_barcode', table_name='products')
        op.drop_index('ix_products_category', table_name='products')
        op.drop_index('ix_products_min_quantity', table_name='products')
        op.drop_index('ix_products_quantity', table_name='products')
        op.drop_index('ix_products_name', table_name='products')
        op.drop_index('ix_products_business_id', table_name='products')
        op.drop_index('ix_products_id', table_name='products')
        op.drop_table('products')
