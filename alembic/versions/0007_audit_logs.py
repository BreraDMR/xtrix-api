"""add audit logs table

Revision ID: 0007_audit_logs
Revises: 0006_qr_codes
Create Date: 2024-01-07 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_audit_logs"
down_revision = "0006_qr_codes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.String, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("user_name", sa.String, nullable=True),
        sa.Column("changes", sa.JSON, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ip_address", sa.String, nullable=True),
        sa.Column("user_agent", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade():
    op.drop_index("ix_audit_logs_created_at", "audit_logs")
    op.drop_index("ix_audit_logs_user_id", "audit_logs")
    op.drop_index("ix_audit_logs_action", "audit_logs")
    op.drop_index("ix_audit_logs_entity_id", "audit_logs")
    op.drop_index("ix_audit_logs_entity_type", "audit_logs")
    op.drop_table("audit_logs")









