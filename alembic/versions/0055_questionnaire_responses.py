"""Store one validated answer set per visit and questionnaire version."""
from alembic import op
import sqlalchemy as sa

revision = "0055_questionnaire_responses"
down_revision = "0054_questionnaire_access_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "questionnaire_responses",
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
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "access_token_id",
            sa.Integer(),
            sa.ForeignKey("questionnaire_access_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("visit_id", name="uq_questionnaire_response_visit"),
    )
    op.create_index(
        "ix_questionnaire_responses_business_id",
        "questionnaire_responses",
        ["business_id"],
    )
    op.create_index(
        "ix_questionnaire_responses_client_id",
        "questionnaire_responses",
        ["client_id"],
    )


def downgrade():
    op.drop_index("ix_questionnaire_responses_client_id", table_name="questionnaire_responses")
    op.drop_index("ix_questionnaire_responses_business_id", table_name="questionnaire_responses")
    op.drop_table("questionnaire_responses")
