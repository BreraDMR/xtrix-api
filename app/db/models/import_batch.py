"""Owner-scoped CRM import batches and their per-row checkpoint.

The journal of imported rows is deliberately separate from ``clients`` and ``visits``:
a batch can be previewed and abandoned without touching CRM data, and a retry can
resume from the last committed row without creating duplicates.
"""
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.api.core.clock import utcnow
from app.db.base import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(
            "(business_id IS NULL) <> (owner_user_id IS NULL)",
            name="ck_import_batches_scope",
        ),
        Index(
            "uq_import_batches_business_source",
            "business_id",
            "source_hash",
            unique=True,
            postgresql_where=text("business_id IS NOT NULL"),
        ),
        Index(
            "uq_import_batches_owner_source",
            "owner_user_id",
            "source_hash",
            unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        Index("ix_import_batches_business_created", "business_id", "created_at"),
        Index("ix_import_batches_owner_created", "owner_user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_hash = Column(String(64), nullable=False, index=True)
    source_filename = Column(String(255), nullable=False)
    content_type = Column(String(64), nullable=True)
    # The raw upload is private (never served by an endpoint) and is cleared as soon as
    # the batch is finished or the short retention window expires.
    source_blob = Column(LargeBinary, nullable=True)
    source_expires_at = Column(DateTime, nullable=True)
    headers = Column(JSON, nullable=False)
    column_signature = Column(String(64), nullable=False)
    mapping = Column(JSON, nullable=True)
    mapping_version = Column(Integer, nullable=False, default=0)
    duplicate_policy = Column(String(16), nullable=False, default="skip")
    status = Column(String(32), nullable=False, default="uploaded", index=True)
    preview_version = Column(Integer, nullable=False, default=0)
    row_count = Column(Integer, nullable=False, default=0)
    valid_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    create_count = Column(Integer, nullable=False, default=0)
    update_count = Column(Integer, nullable=False, default=0)
    skip_count = Column(Integer, nullable=False, default=0)
    created_count = Column(Integer, nullable=False, default=0)
    updated_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    last_checkpoint = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    previewed_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    rows = relationship(
        "ImportRow",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ImportRow.row_number",
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_import_rows_batch_number"),
        UniqueConstraint("batch_id", "row_key", name="uq_import_rows_batch_key"),
        Index("ix_import_rows_batch_status", "batch_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_number = Column(Integer, nullable=False)
    row_key = Column(String(64), nullable=False)
    raw = Column(JSON, nullable=False)
    client_data = Column(JSON, nullable=True)
    visit_data = Column(JSON, nullable=True)
    action = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    error_code = Column(String(64), nullable=True)
    error_detail = Column(Text, nullable=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    visit_id = Column(Integer, ForeignKey("visits.id", ondelete="SET NULL"), nullable=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    batch = relationship("ImportBatch", back_populates="rows")
