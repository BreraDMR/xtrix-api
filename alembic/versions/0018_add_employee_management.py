"""add employee management
Revision ID: 0018_add_employee_mgmt
Revises: 0017_add_service_category
Create Date: 2024-12-27 14:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0018_add_employee_mgmt"
down_revision = "0017_add_service_category"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'users' in table_names:
        columns = [col['name'] for col in inspector.get_columns('users')]
        if 'employer_id' not in columns:
            op.add_column('users', sa.Column('employer_id', sa.Integer(), nullable=True))
            op.create_index('ix_users_employer_id', 'users', ['employer_id'])
            op.create_foreign_key(
                'fk_users_employer_id',
                'users', 'users',
                ['employer_id'], ['id'],
                ondelete='SET NULL'
            )
        if 'hire_date' not in columns:
            op.add_column('users', sa.Column('hire_date', sa.DateTime(), nullable=True))
            op.create_index('ix_users_hire_date', 'users', ['hire_date'])
    if 'employees' not in table_names:
        op.create_table(
            'employees',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('business_owner_id', sa.Integer(), nullable=False),
            sa.Column('employee_user_id', sa.Integer(), nullable=False),
            sa.Column('position', sa.String(), nullable=True),
            sa.Column('permissions', postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column('commission_percentage', sa.Float(), nullable=True),
            sa.Column('base_salary', sa.Float(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('hired_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('fired_at', sa.DateTime(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_employees_business_owner_id', 'employees', ['business_owner_id'])
        op.create_index('ix_employees_employee_user_id', 'employees', ['employee_user_id'])
        op.create_index('ix_employees_is_active', 'employees', ['is_active'])
        op.create_index('ix_employees_hired_at', 'employees', ['hired_at'])
        op.create_index('ix_employees_fired_at', 'employees', ['fired_at'])
        op.create_foreign_key(
            'fk_employees_business_owner_id',
            'employees', 'users',
            ['business_owner_id'], ['id'],
            ondelete='CASCADE'
        )
        op.create_foreign_key(
            'fk_employees_employee_user_id',
            'employees', 'users',
            ['employee_user_id'], ['id'],
            ondelete='CASCADE'
        )
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'employees' in table_names:
        op.drop_table('employees')
    if 'users' in table_names:
        columns = [col['name'] for col in inspector.get_columns('users')]
        if 'hire_date' in columns:
            op.drop_index('ix_users_hire_date', table_name='users')
            op.drop_column('users', 'hire_date')
        if 'employer_id' in columns:
            op.drop_constraint('fk_users_employer_id', 'users', type_='foreignkey')
            op.drop_index('ix_users_employer_id', table_name='users')
            op.drop_column('users', 'employer_id')
