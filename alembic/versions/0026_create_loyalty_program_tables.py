"""create loyalty program tables
Revision ID: 0026_loyalty
Revises: 0025_expenses
Create Date: 2025-01-06 10:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0026_loyalty"
down_revision = "0025_expenses"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'loyalty_programs' not in table_names:
        op.create_table(
            'loyalty_programs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('business_id', sa.Integer(), nullable=False),
            sa.Column('program_type', sa.String(), nullable=False, server_default='points'),
            sa.Column('points_per_currency', sa.Float(), nullable=False, server_default='1.0'),
            sa.Column('points_name', sa.String(), nullable=True),
            sa.Column('currency_per_point', sa.Float(), nullable=True),
            sa.Column('cashback_percent', sa.Float(), nullable=True, server_default='0.0'),
            sa.Column('use_levels', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('points_expiry_days', sa.Integer(), nullable=True),
            sa.Column('birthday_bonus_enabled', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('birthday_bonus_points', sa.Float(), nullable=True),
            sa.Column('birthday_bonus_percent', sa.Float(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('max_bonus_payment_percent', sa.Float(), nullable=True, server_default='100.0'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('business_id')
        )
        op.create_index('ix_loyalty_programs_id', 'loyalty_programs', ['id'], unique=False)
        op.create_index('ix_loyalty_programs_business_id', 'loyalty_programs', ['business_id'], unique=False)
        op.create_index('ix_loyalty_programs_program_type', 'loyalty_programs', ['program_type'], unique=False)
        op.create_index('ix_loyalty_programs_is_active', 'loyalty_programs', ['is_active'], unique=False)
        op.create_index('ix_loyalty_programs_created_at', 'loyalty_programs', ['created_at'], unique=False)
    if 'loyalty_levels' not in table_names:
        op.create_table(
            'loyalty_levels',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('program_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('level_number', sa.Integer(), nullable=False),
            sa.Column('min_spending', sa.Float(), nullable=False),
            sa.Column('points_multiplier', sa.Float(), nullable=True, server_default='1.0'),
            sa.Column('cashback_multiplier', sa.Float(), nullable=True, server_default='1.0'),
            sa.Column('benefits', sa.Text(), nullable=True),
            sa.Column('color', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['program_id'], ['loyalty_programs.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_loyalty_levels_id', 'loyalty_levels', ['id'], unique=False)
        op.create_index('ix_loyalty_levels_program_id', 'loyalty_levels', ['program_id'], unique=False)
        op.create_index('ix_loyalty_levels_name', 'loyalty_levels', ['name'], unique=False)
        op.create_index('ix_loyalty_levels_level_number', 'loyalty_levels', ['level_number'], unique=False)
        op.create_index('ix_loyalty_levels_min_spending', 'loyalty_levels', ['min_spending'], unique=False)
        op.create_index('ix_loyalty_levels_created_at', 'loyalty_levels', ['created_at'], unique=False)
    if 'loyalty_promotions' not in table_names:
        op.create_table(
            'loyalty_promotions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('program_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('promotion_type', sa.String(), nullable=False),
            sa.Column('points_multiplier', sa.Float(), nullable=True),
            sa.Column('cashback_percent', sa.Float(), nullable=True),
            sa.Column('bonus_points', sa.Float(), nullable=True),
            sa.Column('discount_percent', sa.Float(), nullable=True),
            sa.Column('service_ids', sa.Text(), nullable=True),
            sa.Column('min_amount', sa.Float(), nullable=True),
            sa.Column('start_date', sa.Date(), nullable=False),
            sa.Column('end_date', sa.Date(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['program_id'], ['loyalty_programs.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_loyalty_promotions_id', 'loyalty_promotions', ['id'], unique=False)
        op.create_index('ix_loyalty_promotions_program_id', 'loyalty_promotions', ['program_id'], unique=False)
        op.create_index('ix_loyalty_promotions_name', 'loyalty_promotions', ['name'], unique=False)
        op.create_index('ix_loyalty_promotions_promotion_type', 'loyalty_promotions', ['promotion_type'], unique=False)
        op.create_index('ix_loyalty_promotions_start_date', 'loyalty_promotions', ['start_date'], unique=False)
        op.create_index('ix_loyalty_promotions_end_date', 'loyalty_promotions', ['end_date'], unique=False)
        op.create_index('ix_loyalty_promotions_client_id', 'loyalty_promotions', ['client_id'], unique=False)
        op.create_index('ix_loyalty_promotions_is_active', 'loyalty_promotions', ['is_active'], unique=False)
        op.create_index('ix_loyalty_promotions_created_at', 'loyalty_promotions', ['created_at'], unique=False)
    if 'loyalty_transactions' not in table_names:
        op.create_table(
            'loyalty_transactions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('business_id', sa.Integer(), nullable=False),
            sa.Column('program_id', sa.Integer(), nullable=True),
            sa.Column('transaction_type', sa.String(), nullable=False),
            sa.Column('points_amount', sa.Float(), nullable=False),
            sa.Column('cashback_amount', sa.Float(), nullable=True, server_default='0.0'),
            sa.Column('points_balance_after', sa.Float(), nullable=False),
            sa.Column('cashback_balance_after', sa.Float(), nullable=True, server_default='0.0'),
            sa.Column('visit_id', sa.Integer(), nullable=True),
            sa.Column('promotion_id', sa.Integer(), nullable=True),
            sa.Column('level_id', sa.Integer(), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('expiry_date', sa.Date(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['program_id'], ['loyalty_programs.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['promotion_id'], ['loyalty_promotions.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['level_id'], ['loyalty_levels.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_loyalty_transactions_id', 'loyalty_transactions', ['id'], unique=False)
        op.create_index('ix_loyalty_transactions_client_id', 'loyalty_transactions', ['client_id'], unique=False)
        op.create_index('ix_loyalty_transactions_business_id', 'loyalty_transactions', ['business_id'], unique=False)
        op.create_index('ix_loyalty_transactions_program_id', 'loyalty_transactions', ['program_id'], unique=False)
        op.create_index('ix_loyalty_transactions_transaction_type', 'loyalty_transactions', ['transaction_type'], unique=False)
        op.create_index('ix_loyalty_transactions_visit_id', 'loyalty_transactions', ['visit_id'], unique=False)
        op.create_index('ix_loyalty_transactions_promotion_id', 'loyalty_transactions', ['promotion_id'], unique=False)
        op.create_index('ix_loyalty_transactions_expiry_date', 'loyalty_transactions', ['expiry_date'], unique=False)
        op.create_index('ix_loyalty_transactions_created_at', 'loyalty_transactions', ['created_at'], unique=False)
    if 'client_business_relations' in table_names:
        columns = [col['name'] for col in inspector.get_columns('client_business_relations')]
        if 'loyalty_points' not in columns:
            op.add_column('client_business_relations', sa.Column('loyalty_points', sa.Float(), nullable=False, server_default='0.0'))
            op.create_index('ix_client_business_relations_loyalty_points', 'client_business_relations', ['loyalty_points'], unique=False)
        if 'loyalty_cashback' not in columns:
            op.add_column('client_business_relations', sa.Column('loyalty_cashback', sa.Float(), nullable=False, server_default='0.0'))
            op.create_index('ix_client_business_relations_loyalty_cashback', 'client_business_relations', ['loyalty_cashback'], unique=False)
        if 'loyalty_level_id' not in columns:
            op.add_column('client_business_relations', sa.Column('loyalty_level_id', sa.Integer(), nullable=True))
            op.create_foreign_key('fk_client_business_relations_loyalty_level_id', 'client_business_relations', 'loyalty_levels', ['loyalty_level_id'], ['id'], ondelete='SET NULL')
            op.create_index('ix_client_business_relations_loyalty_level_id', 'client_business_relations', ['loyalty_level_id'], unique=False)
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'client_business_relations' in table_names:
        columns = [col['name'] for col in inspector.get_columns('client_business_relations')]
        if 'loyalty_level_id' in columns:
            op.drop_index('ix_client_business_relations_loyalty_level_id', table_name='client_business_relations')
            op.drop_constraint('fk_client_business_relations_loyalty_level_id', 'client_business_relations', type_='foreignkey')
            op.drop_column('client_business_relations', 'loyalty_level_id')
        if 'loyalty_cashback' in columns:
            op.drop_index('ix_client_business_relations_loyalty_cashback', table_name='client_business_relations')
            op.drop_column('client_business_relations', 'loyalty_cashback')
        if 'loyalty_points' in columns:
            op.drop_index('ix_client_business_relations_loyalty_points', table_name='client_business_relations')
            op.drop_column('client_business_relations', 'loyalty_points')
    if 'loyalty_transactions' in table_names:
        op.drop_index('ix_loyalty_transactions_created_at', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_expiry_date', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_promotion_id', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_visit_id', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_transaction_type', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_program_id', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_business_id', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_client_id', table_name='loyalty_transactions')
        op.drop_index('ix_loyalty_transactions_id', table_name='loyalty_transactions')
        op.drop_table('loyalty_transactions')
    if 'loyalty_promotions' in table_names:
        op.drop_index('ix_loyalty_promotions_created_at', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_is_active', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_client_id', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_end_date', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_start_date', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_promotion_type', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_name', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_program_id', table_name='loyalty_promotions')
        op.drop_index('ix_loyalty_promotions_id', table_name='loyalty_promotions')
        op.drop_table('loyalty_promotions')
    if 'loyalty_levels' in table_names:
        op.drop_index('ix_loyalty_levels_created_at', table_name='loyalty_levels')
        op.drop_index('ix_loyalty_levels_min_spending', table_name='loyalty_levels')
        op.drop_index('ix_loyalty_levels_level_number', table_name='loyalty_levels')
        op.drop_index('ix_loyalty_levels_name', table_name='loyalty_levels')
        op.drop_index('ix_loyalty_levels_program_id', table_name='loyalty_levels')
        op.drop_index('ix_loyalty_levels_id', table_name='loyalty_levels')
        op.drop_table('loyalty_levels')
    if 'loyalty_programs' in table_names:
        op.drop_index('ix_loyalty_programs_created_at', table_name='loyalty_programs')
        op.drop_index('ix_loyalty_programs_is_active', table_name='loyalty_programs')
        op.drop_index('ix_loyalty_programs_program_type', table_name='loyalty_programs')
        op.drop_index('ix_loyalty_programs_business_id', table_name='loyalty_programs')
        op.drop_index('ix_loyalty_programs_id', table_name='loyalty_programs')
        op.drop_table('loyalty_programs')
