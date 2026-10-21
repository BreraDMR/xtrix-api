"""Owner-scoped CRM import batches with per-row checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = "0074_import_batches"
down_revision = "0073_business_audit_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Integer(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=True),
        sa.Column("source_blob", sa.LargeBinary(), nullable=True),
        sa.Column("source_expires_at", sa.DateTime(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("column_signature", sa.String(64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=True),
        sa.Column("mapping_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_policy", sa.String(16), nullable=False, server_default="skip"),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
        sa.Column("preview_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("create_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("update_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skip_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checkpoint", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("previewed_at", sa.DateTime(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "(business_id IS NULL) <> (owner_user_id IS NULL)",
            name="ck_import_batches_scope",
        ),
    )
    op.create_index("ix_import_batches_id", "import_batches", ["id"])
    op.create_index("ix_import_batches_business_id", "import_batches", ["business_id"])
    op.create_index("ix_import_batches_owner_user_id", "import_batches", ["owner_user_id"])
    op.create_index("ix_import_batches_source_hash", "import_batches", ["source_hash"])
    op.create_index("ix_import_batches_status", "import_batches", ["status"])
    op.create_index("ix_import_batches_created_at", "import_batches", ["created_at"])
    op.create_index(
        "ix_import_batches_business_created",
        "import_batches",
        ["business_id", "created_at"],
    )
    op.create_index(
        "ix_import_batches_owner_created",
        "import_batches",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "uq_import_batches_business_source",
        "import_batches",
        ["business_id", "source_hash"],
        unique=True,
        postgresql_where=sa.text("business_id IS NOT NULL"),
    )
    op.create_index(
        "uq_import_batches_owner_source",
        "import_batches",
        ["owner_user_id", "source_hash"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )

    op.create_table(
        "import_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("import_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_key", sa.String(64), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("client_data", sa.JSON(), nullable=True),
        sa.Column("visit_data", sa.JSON(), nullable=True),
        sa.Column("action", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "visit_id",
            sa.Integer(),
            sa.ForeignKey("visits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("batch_id", "row_number", name="uq_import_rows_batch_number"),
        sa.UniqueConstraint("batch_id", "row_key", name="uq_import_rows_batch_key"),
    )
    op.create_index("ix_import_rows_id", "import_rows", ["id"])
    op.create_index("ix_import_rows_batch_id", "import_rows", ["batch_id"])
    op.create_index("ix_import_rows_status", "import_rows", ["status"])
    op.create_index(
        "ix_import_rows_batch_status", "import_rows", ["batch_id", "status"]
    )


def downgrade():
    op.drop_index("ix_import_rows_batch_status", table_name="import_rows")
    op.drop_index("ix_import_rows_status", table_name="import_rows")
    op.drop_index("ix_import_rows_batch_id", table_name="import_rows")
    op.drop_index("ix_import_rows_id", table_name="import_rows")
    op.drop_table("import_rows")

    op.drop_index("uq_import_batches_owner_source", table_name="import_batches")
    op.drop_index("uq_import_batches_business_source", table_name="import_batches")
    op.drop_index("ix_import_batches_owner_created", table_name="import_batches")
    op.drop_index("ix_import_batches_business_created", table_name="import_batches")
    op.drop_index("ix_import_batches_created_at", table_name="import_batches")
    op.drop_index("ix_import_batches_status", table_name="import_batches")
    op.drop_index("ix_import_batches_source_hash", table_name="import_batches")
    op.drop_index("ix_import_batches_owner_user_id", table_name="import_batches")
    op.drop_index("ix_import_batches_business_id", table_name="import_batches")
    op.drop_index("ix_import_batches_id", table_name="import_batches")
    op.drop_table("import_batches")
