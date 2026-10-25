"""Add independent verified visitor accounts and Google identities."""
from alembic import op
import sqlalchemy as sa

revision = "0079_visitor_accounts"
down_revision = "0078_waitlist_open_range"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "visitor_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_canonical", sa.String(320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.UniqueConstraint("email_canonical", name="uq_visitor_accounts_email_canonical"),
        sa.CheckConstraint("length(email_canonical) > 0 AND email_canonical = btrim(email_canonical)",
                           name="ck_visitor_accounts_email_nonempty_trimmed"),
        sa.CheckConstraint("hashed_password IS NULL OR length(hashed_password) > 0",
                           name="ck_visitor_accounts_password_nonempty"),
    )
    op.create_table(
        "visitor_google_identities",
        sa.Column("issuer", sa.String(255), primary_key=True),
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.CheckConstraint("issuer = 'https://accounts.google.com'", name="ck_visitor_google_identity_issuer"),
        sa.CheckConstraint("length(subject) > 0 AND subject = btrim(subject)",
                           name="ck_visitor_google_identity_subject"),
    )
    op.create_index("ix_visitor_google_identities_account_id", "visitor_google_identities", ["account_id"])


def downgrade():
    op.drop_index("ix_visitor_google_identities_account_id", table_name="visitor_google_identities")
    op.drop_table("visitor_google_identities")
    op.drop_table("visitor_accounts")
