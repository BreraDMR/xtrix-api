"""Add explicit payroll currency snapshots without guessing historical units."""
from alembic import op
import sqlalchemy as sa

revision = '0086_payroll_currency_snapshot'
down_revision = '0085_loyalty_discount_snapshot'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('salary_periods',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('base_salary_currency', sa.String(3), nullable=True),
        sa.Column('base_salary_exact', sa.Numeric(18, 2), nullable=False),
        sa.Column('commission_percentage', sa.Numeric(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('business_id', 'employee_id', 'period_start', 'period_end', name='uq_salary_period_scope'),
        sa.CheckConstraint('period_end > period_start', name='ck_salary_period_dates'),
        sa.CheckConstraint('base_salary_exact BETWEEN 0 AND 9999999999999999.99 AND ((base_salary_currency IS NULL AND base_salary_exact = 0) OR (base_salary_currency IS NOT NULL AND base_salary_currency COLLATE "C" ~ \'^[A-Z]{3}$\'))', name='ck_salary_period_base'),
        sa.CheckConstraint('commission_percentage BETWEEN 0 AND 100', name='ck_salary_period_commission'),
    )
    # Historical floats and expense defaults cannot prove a payroll currency.
    op.add_column('employees', sa.Column('base_salary_currency', sa.String(3), nullable=True))
    op.add_column('employees', sa.Column('base_salary_exact', sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint('ck_employee_salary_snapshot', 'employees', '(base_salary_currency IS NULL AND base_salary_exact IS NULL) OR (base_salary_currency IS NOT NULL AND base_salary_currency COLLATE "C" ~ \'^[A-Z]{3}$\'  AND base_salary_exact IS NOT NULL AND base_salary_exact BETWEEN 0 AND 9999999999999999.99)')
    op.add_column('salary_calculations', sa.Column('salary_period_id', sa.Integer(), sa.ForeignKey('salary_periods.id', ondelete='RESTRICT'), nullable=True))
    op.add_column('salary_calculations', sa.Column('currency', sa.String(3), nullable=True))
    op.create_unique_constraint('uq_salary_calculation_period_currency', 'salary_calculations', ['salary_period_id', 'currency'])
    op.add_column('salary_calculations', sa.Column('business_id_snapshot', sa.Integer(), sa.ForeignKey('businesses.id', ondelete='RESTRICT'), nullable=True))
    op.add_column('salary_calculations', sa.Column('total_revenue_exact', sa.Numeric(18, 2), nullable=True))
    op.add_column('salary_calculations', sa.Column('commission_amount_exact', sa.Numeric(18, 2), nullable=True))
    op.add_column('salary_calculations', sa.Column('base_salary_exact', sa.Numeric(18, 2), nullable=True))
    op.add_column('salary_calculations', sa.Column('total_salary_exact', sa.Numeric(18, 2), nullable=True))
    op.add_column('salary_calculations', sa.Column('adjustments_total_exact', sa.Numeric(18, 2), nullable=True))
    op.add_column('salary_calculations', sa.Column('total_with_adjustments_exact', sa.Numeric(18, 2), nullable=True))
    op.add_column('salary_calculations', sa.Column('paid_amount_exact', sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint('ck_salary_calculation_snapshot', 'salary_calculations', '(currency IS NULL AND total_revenue_exact IS NULL AND commission_amount_exact IS NULL AND base_salary_exact IS NULL AND total_salary_exact IS NULL AND adjustments_total_exact IS NULL AND total_with_adjustments_exact IS NULL AND paid_amount_exact IS NULL AND business_id_snapshot IS NULL AND salary_period_id IS NULL) OR (currency IS NOT NULL AND currency COLLATE "C" ~ \'^[A-Z]{3}$\'  AND total_revenue_exact IS NOT NULL AND total_revenue_exact BETWEEN 0 AND 9999999999999999.99 AND commission_amount_exact IS NOT NULL AND commission_amount_exact BETWEEN 0 AND 9999999999999999.99 AND base_salary_exact IS NOT NULL AND base_salary_exact BETWEEN 0 AND 9999999999999999.99 AND total_salary_exact IS NOT NULL AND total_salary_exact BETWEEN 0 AND 9999999999999999.99 AND adjustments_total_exact IS NOT NULL AND adjustments_total_exact BETWEEN -9999999999999999.99 AND 9999999999999999.99 AND total_with_adjustments_exact IS NOT NULL AND total_with_adjustments_exact BETWEEN 0 AND 9999999999999999.99 AND paid_amount_exact IS NOT NULL AND paid_amount_exact BETWEEN 0 AND 9999999999999999.99 AND business_id_snapshot IS NOT NULL AND salary_period_id IS NOT NULL AND total_salary_exact = commission_amount_exact + base_salary_exact AND total_with_adjustments_exact = total_salary_exact + adjustments_total_exact AND paid_amount_exact <= total_with_adjustments_exact)')
    op.add_column('salary_adjustments', sa.Column('currency', sa.String(3), nullable=True))
    op.add_column('salary_adjustments', sa.Column('amount_exact', sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint('ck_salary_adjustment_snapshot', 'salary_adjustments', '(currency IS NULL AND amount_exact IS NULL) OR (currency IS NOT NULL AND currency COLLATE "C" ~ \'^[A-Z]{3}$\'  AND amount_exact IS NOT NULL AND amount_exact BETWEEN -9999999999999999.99 AND 9999999999999999.99)')
    op.add_column('salary_payments', sa.Column('currency', sa.String(3), nullable=True))
    op.add_column('salary_payments', sa.Column('amount_exact', sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint('ck_salary_payment_snapshot', 'salary_payments', '(currency IS NULL AND amount_exact IS NULL) OR (currency IS NOT NULL AND currency COLLATE "C" ~ \'^[A-Z]{3}$\'  AND amount_exact IS NOT NULL AND amount_exact BETWEEN 0 AND 9999999999999999.99)')
    op.add_column('expenses', sa.Column('salary_payment_id', sa.Integer(), sa.ForeignKey('salary_payments.id', ondelete='RESTRICT'), nullable=True))
    op.create_unique_constraint('uq_expense_salary_payment', 'expenses', ['salary_payment_id'])


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM salary_periods) OR EXISTS (SELECT 1 FROM employees WHERE base_salary_currency IS NOT NULL OR base_salary_exact IS NOT NULL) OR EXISTS (SELECT 1 FROM salary_calculations WHERE salary_period_id IS NOT NULL OR currency IS NOT NULL OR business_id_snapshot IS NOT NULL OR total_revenue_exact IS NOT NULL OR commission_amount_exact IS NOT NULL OR base_salary_exact IS NOT NULL OR total_salary_exact IS NOT NULL OR adjustments_total_exact IS NOT NULL OR total_with_adjustments_exact IS NOT NULL OR paid_amount_exact IS NOT NULL) OR EXISTS (SELECT 1 FROM salary_adjustments WHERE currency IS NOT NULL OR amount_exact IS NOT NULL) OR EXISTS (SELECT 1 FROM salary_payments WHERE currency IS NOT NULL OR amount_exact IS NOT NULL) OR EXISTS (SELECT 1 FROM expenses WHERE salary_payment_id IS NOT NULL) THEN RAISE EXCEPTION 'Payroll currency data exists; export it and plan rollback before removing this schema'; END IF; END $$")
    op.drop_constraint('uq_expense_salary_payment', 'expenses', type_='unique')
    op.drop_column('expenses', 'salary_payment_id')
    op.drop_constraint('ck_salary_payment_snapshot', 'salary_payments', type_='check')
    op.drop_column('salary_payments', 'amount_exact')
    op.drop_column('salary_payments', 'currency')
    op.drop_constraint('ck_salary_adjustment_snapshot', 'salary_adjustments', type_='check')
    op.drop_column('salary_adjustments', 'amount_exact')
    op.drop_column('salary_adjustments', 'currency')
    op.drop_constraint('ck_salary_calculation_snapshot', 'salary_calculations', type_='check')
    op.drop_column('salary_calculations', 'paid_amount_exact')
    op.drop_column('salary_calculations', 'total_with_adjustments_exact')
    op.drop_column('salary_calculations', 'adjustments_total_exact')
    op.drop_column('salary_calculations', 'total_salary_exact')
    op.drop_column('salary_calculations', 'base_salary_exact')
    op.drop_column('salary_calculations', 'commission_amount_exact')
    op.drop_column('salary_calculations', 'total_revenue_exact')
    op.drop_column('salary_calculations', 'business_id_snapshot')
    op.drop_constraint('uq_salary_calculation_period_currency', 'salary_calculations', type_='unique')
    op.drop_column('salary_calculations', 'salary_period_id')
    op.drop_column('salary_calculations', 'currency')
    op.drop_table('salary_periods')
    op.drop_constraint('ck_employee_salary_snapshot', 'employees', type_='check')
    op.drop_column('employees', 'base_salary_exact')
    op.drop_column('employees', 'base_salary_currency')
