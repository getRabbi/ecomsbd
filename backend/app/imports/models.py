"""Import batch and row models.

Master spec section 98. Every import records its source SHA256, filename, row
counts and the raw parsed row, so a seller who asks "why did this order come out
wrong?" can be shown the exact line it came from.

Section 98's last line is the one that shapes validation: **"do not silently
coerce invalid money/phone values."** A row with an unparseable phone is a
failure the seller sees and fixes, never a row quietly saved with a blank phone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = [
    "ImportBatch",
    "ImportRow",
    "ImportRowStatus",
    "ImportStatus",
    "ImportTemplate",
]


class ImportTemplate(StrEnum):
    """What is being imported."""

    ORDERS = "ORDERS"
    PRODUCTS = "PRODUCTS"
    #: Reserved: the payout/statement template is Phase D, and reuses this
    #: pipeline rather than getting its own.
    PAYOUT_STATEMENT = "PAYOUT_STATEMENT"


class ImportStatus(StrEnum):
    """Pipeline position (section 98)."""

    UPLOADED = "UPLOADED"
    MAPPED = "MAPPED"
    #: A dry run finished; counts are known and nothing was written.
    VALIDATED = "VALIDATED"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ImportStatus.COMMITTED,
            ImportStatus.FAILED,
            ImportStatus.CANCELLED,
        )


class ImportRowStatus(StrEnum):
    """Outcome for a single line."""

    PENDING = "PENDING"
    #: Parsed cleanly and would be created.
    READY = "READY"
    #: Will be created, but something is worth the seller's attention.
    WARNING = "WARNING"
    #: Matches a record this import already created, or an existing one.
    DUPLICATE = "DUPLICATE"
    #: Cannot be created. Never coerced into something importable.
    INVALID = "INVALID"
    CREATED = "CREATED"
    SKIPPED = "SKIPPED"


class ImportBatch(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One uploaded file and its progress through the pipeline."""

    __tablename__ = "imports"
    __table_args__ = (
        sa.Index("ix_imports_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_imports_tenant_status", "tenant_id", "status"),
        # Re-importing the identical file is caught here rather than by
        # comparing rows (section 98: "idempotent duplicate handling").
        sa.Index("ix_imports_tenant_sha", "tenant_id", "template", "source_sha256"),
        sa.CheckConstraint("row_count >= 0", name="row_count_non_negative"),
    )

    template: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=ImportStatus.UPLOADED, index=True
    )

    original_filename: Mapped[str] = mapped_column(sa.String(400), nullable=False)
    #: Hash of the uploaded bytes. Identifies a re-upload of the same file.
    source_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_type: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    byte_size: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Cloudflare R2 key once object storage is provisioned. Until then the
    #: parsed rows below are the retained evidence.
    storage_key: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Seller's column mapping: ``{"phone": "Customer Mobile", …}``.
    column_mapping: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    detected_headers: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    ready_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    dry_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    rows: Mapped[list[ImportRow]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def import_status(self) -> ImportStatus:
        return ImportStatus(self.status)

    @property
    def can_commit(self) -> bool:
        """Only a validated batch with something to create may be committed."""
        return (
            self.import_status is ImportStatus.VALIDATED
            and (self.ready_count + self.warning_count) > 0
        )


class ImportRow(Base, TenantOwned, PrimaryKeyMixin):
    """One line of the file, with its raw values retained."""

    __tablename__ = "import_rows"
    __table_args__ = (
        sa.UniqueConstraint("import_id", "row_number", name="uq_import_rows_import_id_row_number"),
        sa.Index("ix_import_rows_import_status", "import_id", "status"),
        # Row-level idempotency: the same logical record appearing twice, in one
        # file or across re-imports, is recognised without comparing every field.
        sa.Index("ix_import_rows_fingerprint", "tenant_id", "fingerprint"),
    )

    import_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("imports.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 1-based line number as the seller sees it in their spreadsheet.
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: Exactly what the file contained, before any parsing. The evidence a
    #: support conversation needs.
    raw: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: Values after parsing and normalisation. Absent for an invalid row.
    parsed: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=ImportRowStatus.PENDING
    )
    #: Per-field problems: ``[{"field": "phone", "message": "…"}]``.
    errors: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    #: Stable hash of the row's identifying fields.
    fingerprint: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    #: What this row created, once committed.
    created_entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")

    @property
    def row_status(self) -> ImportRowStatus:
        return ImportRowStatus(self.status)

    @property
    def is_importable(self) -> bool:
        return self.row_status in (ImportRowStatus.READY, ImportRowStatus.WARNING)
