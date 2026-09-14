"""Export jobs.

Master spec section 99: exports are tenant-scoped, authenticated, audited and
time-limited, and a large one is generated asynchronously. Its last line is the
one with teeth: *"never email raw customer exports automatically."* There is no
delivery channel in this module at all — an export produces a link the seller
fetches while signed in, and nothing else.

The download token is stored hashed and expires. A link that never expires is a
customer database that leaks the day someone forwards a chat message.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = ["ExportFormat", "ExportJob", "ExportKind", "ExportStatus"]


class ExportKind(StrEnum):
    """What section 99 lists, and nothing else."""

    ORDERS = "ORDERS"
    CUSTOMERS = "CUSTOMERS"
    PRODUCTS = "PRODUCTS"
    PAYOUTS = "PAYOUTS"
    RECONCILIATION = "RECONCILIATION"
    PROFIT_SUMMARY = "PROFIT_SUMMARY"


class ExportFormat(StrEnum):
    CSV = "CSV"


class ExportStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"
    #: Downloaded or aged out. The content is dropped; the record stays, so the
    #: audit question "who exported the customer list in March?" is answerable.
    EXPIRED = "EXPIRED"


class ExportJob(Base, TenantOwned, PrimaryKeyMixin):
    """One requested export."""

    __tablename__ = "export_jobs"
    __table_args__ = (
        sa.Index("ix_export_jobs_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_export_jobs_status", "status"),
        sa.UniqueConstraint("download_token_hash", name="uq_export_jobs_download_token_hash"),
        sa.CheckConstraint("row_count >= 0", name="row_count_non_negative"),
    )

    kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    export_format: Mapped[str] = mapped_column(
        sa.String(8), nullable=False, default=ExportFormat.CSV
    )
    status: Mapped[str] = mapped_column(sa.String(12), nullable=False, default=ExportStatus.PENDING)

    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    #: Filters the seller chose, so a support question about a strange figure
    #: can be answered from the same window the seller exported.
    parameters: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    byte_size: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    filename: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Where the file lives once R2 is provisioned. Until then the content sits
    #: in ``content`` — the same interim arrangement payout source files use,
    #: and moving it is a migration rather than a redesign.
    storage_key: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    content: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)

    #: Hashed, like a refresh token. A link is a bearer credential.
    download_token_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    download_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    def is_downloadable(self, *, at: datetime | None = None) -> bool:
        moment = at or utc_now()
        if self.status != ExportStatus.READY or (self.content is None and self.storage_key is None):
            return False
        return self.expires_at is None or self.expires_at > moment
