"""add salary calculation
Revision ID: 0019_add_salary_calc
Revises: 0018_add_employee_mgmt
Create Date: 2024-12-27 15:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0019_add_salary_calc"
down_revision = "0018_add_employee_mgmt"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'salary_calculations' not in table_names:
        op.create_table(
            'salary_calculations',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('employee_id', sa.Integer(), nullable=False),
            sa.Column('period_start', sa.DateTime(), nullable=False),
            sa.Column('period_end', sa.DateTime(), nullable=False),
            sa.Column('total_visits', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('completed_visits', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cancelled_visits', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('total_revenue', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('commission_amount', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('base_salary', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('total_salary', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('is_paid', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('paid_at', sa.DateTime(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('calculated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('calculated_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_salary_calculations_employee_id', 'salary_calculations', ['employee_id'])
        op.create_index('ix_salary_calculations_period_start', 'salary_calculations', ['period_start'])
        op.create_index('ix_salary_calculations_period_end', 'salary_calculations', ['period_end'])
        op.create_index('ix_salary_calculations_is_paid', 'salary_calculations', ['is_paid'])
        op.create_index('ix_salary_calculations_paid_at', 'salary_calculations', ['paid_at'])
        op.create_index('ix_salary_calculations_calculated_at', 'salary_calculations', ['calculated_at'])
        op.create_foreign_key(
            'fk_salary_calculations_employee_id',
            'salary_calculations', 'employees',
            ['employee_id'], ['id'],
            ondelete='CASCADE'
        )
        op.create_foreign_key(
            'fk_salary_calculations_calculated_by_id',
            'salary_calculations', 'users',
            ['calculated_by_id'], ['id'],
            ondelete='SET NULL'
        )
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'salary_calculations' in table_names:
        op.drop_table('salary_calculations')
