"""add_salary_adjustments_and_payments

Revision ID: 0030_salary_enh
Revises: 0029_payment_method
Create Date: 2025-01-18 10:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0030_salary_enh"
down_revision = "0029_payment_method"
branch_labels = None
depends_on = None


def upgrade():
    # Расширение salary_calculations
    op.add_column('salary_calculations', sa.Column('adjustments_total', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('salary_calculations', sa.Column('total_with_adjustments', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('salary_calculations', sa.Column('paid_amount', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('salary_calculations', sa.Column('payment_status', sa.String(), nullable=False, server_default='unpaid'))
    op.create_index(op.f('ix_salary_calculations_payment_status'), 'salary_calculations', ['payment_status'], unique=False)
    
    # Обновляем существующие записи
    op.execute("""
        UPDATE salary_calculations 
        SET total_with_adjustments = total_salary,
            payment_status = CASE 
                WHEN is_paid = true THEN 'paid'
                ELSE 'unpaid'
            END
    """)
    
    # Создание таблицы salary_adjustments
    op.create_table(
        'salary_adjustments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('salary_calculation_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['salary_calculation_id'], ['salary_calculations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_salary_adjustments_salary_calculation_id'), 'salary_adjustments', ['salary_calculation_id'], unique=False)
    op.create_index(op.f('ix_salary_adjustments_type'), 'salary_adjustments', ['type'], unique=False)
    
    # Создание таблицы salary_payments
    op.create_table(
        'salary_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('salary_calculation_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('payment_date', sa.DateTime(), nullable=False),
        sa.Column('payment_method', sa.String(), nullable=False, server_default='other'),
        sa.Column('status', sa.String(), nullable=False, server_default='completed'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['salary_calculation_id'], ['salary_calculations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_salary_payments_salary_calculation_id'), 'salary_payments', ['salary_calculation_id'], unique=False)
    op.create_index(op.f('ix_salary_payments_payment_date'), 'salary_payments', ['payment_date'], unique=False)
    op.create_index(op.f('ix_salary_payments_status'), 'salary_payments', ['status'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_salary_payments_status'), table_name='salary_payments')
    op.drop_index(op.f('ix_salary_payments_payment_date'), table_name='salary_payments')
    op.drop_index(op.f('ix_salary_payments_salary_calculation_id'), table_name='salary_payments')
    op.drop_table('salary_payments')
    
    op.drop_index(op.f('ix_salary_adjustments_type'), table_name='salary_adjustments')
    op.drop_index(op.f('ix_salary_adjustments_salary_calculation_id'), table_name='salary_adjustments')
    op.drop_table('salary_adjustments')
    
    op.drop_index(op.f('ix_salary_calculations_payment_status'), table_name='salary_calculations')
    op.drop_column('salary_calculations', 'payment_status')
    op.drop_column('salary_calculations', 'paid_amount')
    op.drop_column('salary_calculations', 'total_with_adjustments')
    op.drop_column('salary_calculations', 'adjustments_total')
