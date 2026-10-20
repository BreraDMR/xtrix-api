"""Add business-scoped client notes and the tag/preference columns."""
from alembic import op
import sqlalchemy as sa

revision = "0064_client_profile"
down_revision = "0063_outbox_account_messages"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_client_notes_id", "client_notes", ["id"])
    op.create_index("ix_client_notes_client_id", "client_notes", ["client_id"])
    op.create_index("ix_client_notes_business_id", "client_notes", ["business_id"])
    op.create_index("ix_client_notes_author_id", "client_notes", ["author_id"])
    op.create_index("ix_client_notes_created_at", "client_notes", ["created_at"])

    op.add_column("client_business_relations", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("client_business_relations", sa.Column("preferences", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("client_business_relations", "preferences")
    op.drop_column("client_business_relations", "tags")

    op.drop_index("ix_client_notes_created_at", table_name="client_notes")
    op.drop_index("ix_client_notes_author_id", table_name="client_notes")
    op.drop_index("ix_client_notes_business_id", table_name="client_notes")
    op.drop_index("ix_client_notes_client_id", table_name="client_notes")
    op.drop_index("ix_client_notes_id", table_name="client_notes")
    op.drop_table("client_notes")
