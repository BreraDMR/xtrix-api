"""Track private questionnaire uploads by a server-chosen object key."""
from alembic import op
import sqlalchemy as sa

revision = "0056_questionnaire_attachments"
down_revision = "0055_questionnaire_responses"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "questionnaire_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("questionnaire_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("questionnaire_access_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("storage_key", name="uq_questionnaire_attachment_key"),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 10485760",
            name="ck_questionnaire_attachment_size",
        ),
    )
    op.create_index(
        "ix_questionnaire_attachments_visit_id",
        "questionnaire_attachments",
        ["visit_id"],
    )


def downgrade():
    op.drop_index("ix_questionnaire_attachments_visit_id", table_name="questionnaire_attachments")
    op.drop_table("questionnaire_attachments")
