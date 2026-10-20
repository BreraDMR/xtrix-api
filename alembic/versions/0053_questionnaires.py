"""Store questionnaire drafts and immutable published versions."""
from alembic import op
import sqlalchemy as sa

revision = "0053_questionnaires"
down_revision = "0052_public_booking_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "questionnaire_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("draft_fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("service_id", name="uq_questionnaire_template_service"),
    )
    op.create_index("ix_questionnaire_templates_business_id", "questionnaire_templates", ["business_id"])

    op.create_table(
        "questionnaire_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("questionnaire_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column(
            "published_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_questionnaire_version_number"),
        sa.CheckConstraint("version >= 1", name="ck_questionnaire_version_number"),
    )
    op.create_index(
        "ix_questionnaire_versions_published_by_id",
        "questionnaire_versions",
        ["published_by_id"],
    )


def downgrade():
    op.drop_index(
        "ix_questionnaire_versions_published_by_id",
        table_name="questionnaire_versions",
    )
    op.drop_table("questionnaire_versions")
    op.drop_index("ix_questionnaire_templates_business_id", table_name="questionnaire_templates")
    op.drop_table("questionnaire_templates")
