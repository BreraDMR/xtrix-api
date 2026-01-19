"""add_service_specialists_many_to_many

Revision ID: 0031_service_spec
Revises: 0030_salary_enh
Create Date: 2025-01-19 12:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0031_service_spec"
down_revision = "0030_salary_enh"
branch_labels = None
depends_on = None


def upgrade():
    # Создаем таблицу связи многие-ко-многим между services и specialists
    op.create_table(
        'service_specialists',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('service_id', sa.Integer(), nullable=False),
        sa.Column('specialist_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['specialist_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('service_id', 'specialist_id', name='uq_service_specialist')
    )
    op.create_index(op.f('ix_service_specialists_service_id'), 'service_specialists', ['service_id'], unique=False)
    op.create_index(op.f('ix_service_specialists_specialist_id'), 'service_specialists', ['specialist_id'], unique=False)
    
    # Миграция существующих данных: создаем записи в service_specialists из services.specialist_id
    op.execute("""
        INSERT INTO service_specialists (service_id, specialist_id, created_at)
        SELECT id, specialist_id, created_at
        FROM services
        WHERE specialist_id IS NOT NULL
        ON CONFLICT (service_id, specialist_id) DO NOTHING
    """)


def downgrade():
    op.drop_index(op.f('ix_service_specialists_specialist_id'), table_name='service_specialists')
    op.drop_index(op.f('ix_service_specialists_service_id'), table_name='service_specialists')
    op.drop_table('service_specialists')
