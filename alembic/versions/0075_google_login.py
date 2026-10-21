"""Bind Google subjects to users and keep one-time login challenges."""
from alembic import op
import sqlalchemy as sa

revision = "0075_google_login"
down_revision = "0074_import_batches"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "google_identities",
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "google_login_nonces",
        sa.Column("nonce_hash", sa.String(64), primary_key=True),
        sa.Column("origin", sa.String(2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_google_login_nonces_expires_at", "google_login_nonces", ["expires_at"])


def downgrade():
    op.drop_index("ix_google_login_nonces_expires_at", table_name="google_login_nonces")
    op.drop_table("google_login_nonces")
    op.drop_table("google_identities")
