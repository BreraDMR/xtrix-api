"""add user moderation
Revision ID: 0020_add_user_moderation
Revises: 0019_add_salary_calc
Create Date: 2025-01-05 12:00:00
"""
from alembic import op
import sqlalchemy as sa
revision = "0020_add_user_moderation"
down_revision = "0019_add_salary_calc"
branch_labels = None
depends_on = None
def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'users' not in table_names:
        return
    columns = [col['name'] for col in inspector.get_columns('users')]
    if 'moderation_status' not in columns:
        op.add_column('users', sa.Column('moderation_status', sa.String(), nullable=False, server_default='approved'))
        op.create_index('ix_users_moderation_status', 'users', ['moderation_status'])
        op.execute("UPDATE users SET moderation_status = 'approved' WHERE moderation_status IS NULL OR moderation_status = ''")
    if 'moderation_notes' not in columns:
        op.add_column('users', sa.Column('moderation_notes', sa.String(), nullable=True))
    if 'moderated_at' not in columns:
        op.add_column('users', sa.Column('moderated_at', sa.DateTime(), nullable=True))
        op.create_index('ix_users_moderated_at', 'users', ['moderated_at'])
    if 'moderated_by_id' not in columns:
        op.add_column('users', sa.Column('moderated_by_id', sa.Integer(), nullable=True))
        op.create_index('ix_users_moderated_by_id', 'users', ['moderated_by_id'])
        op.create_foreign_key('fk_users_moderated_by_id', 'users', 'users', ['moderated_by_id'], ['id'])
def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = inspector.get_table_names()
    if 'users' not in table_names:
        return
    columns = [col['name'] for col in inspector.get_columns('users')]
    if 'moderated_by_id' in columns:
        try:
            op.drop_constraint('fk_users_moderated_by_id', 'users', type_='foreignkey')
        except:
            pass
        op.drop_index('ix_users_moderated_by_id', 'users')
        op.drop_column('users', 'moderated_by_id')
    if 'moderated_at' in columns:
        op.drop_index('ix_users_moderated_at', 'users')
        op.drop_column('users', 'moderated_at')
    if 'moderation_notes' in columns:
        op.drop_column('users', 'moderation_notes')
    if 'moderation_status' in columns:
        op.drop_index('ix_users_moderation_status', 'users')
        op.drop_column('users', 'moderation_status')
