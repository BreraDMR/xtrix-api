"""Store only hashes of visit-scoped questionnaire links."""
from alembic import op
import sqlalchemy as sa

revision = "0054_questionnaire_access_tokens"
down_revision = "0053_questionnaires"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "questionnaire_access_tokens",
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
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_questionnaire_access_token_hash"),
    )
    op.create_index(
        "ix_questionnaire_access_tokens_business_id",
        "questionnaire_access_tokens",
        ["business_id"],
    )
    op.create_index(
        "ix_questionnaire_access_tokens_visit_id",
        "questionnaire_access_tokens",
        ["visit_id"],
    )
    op.create_index(
        "ix_questionnaire_access_tokens_expires_at",
        "questionnaire_access_tokens",
        ["expires_at"],
    )


def downgrade():
    op.drop_index("ix_questionnaire_access_tokens_expires_at", table_name="questionnaire_access_tokens")
    op.drop_index("ix_questionnaire_access_tokens_visit_id", table_name="questionnaire_access_tokens")
    op.drop_index("ix_questionnaire_access_tokens_business_id", table_name="questionnaire_access_tokens")
    op.drop_table("questionnaire_access_tokens")
