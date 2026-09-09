"""Sync mutation ledger.

Master spec sections 37 and 38.

A device that is offline queues mutations locally and sends them when it
reconnects. The network being unreliable is the normal case here, not the
exception: a seller on a moving bus will send the same batch twice, and the
second send must not create a second order.

This table is how that is guaranteed. Every mutation carries a client-generated
UUID, and the server records the outcome against it. A replay finds the existing
row and returns the original result instead of re-executing — the same shape as
the HTTP idempotency store (section 77), but keyed to a device's queue rather
than to a request.

The record is kept after success, not deleted, because "did my order actually
save?" is a question a seller asks days later.
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

__all__ = [
    "SYNCABLE_ENTITIES",
    "MutationOperation",
    "MutationStatus",
    "SyncEntity",
    "SyncMutation",
]


class SyncEntity(StrEnum):
    """Entities a client may create or edit offline.

    Deliberately short. Master spec section 37 allows offline creation and
    editing of unbooked orders, products, customers and notes — and nothing
    else. Courier booking, risk lookup, payout import and subscription checkout
    require the network and are absent here on purpose: section 62.17 forbids
    queueing an external courier action as though it had succeeded.
    """

    ORDER = "ORDER"
    PRODUCT = "PRODUCT"
    CUSTOMER = "CUSTOMER"
    STOCK_ADJUSTMENT = "STOCK_ADJUSTMENT"


#: The allowlist the endpoint checks. Anything else is rejected rather than
#: attempted, so a client bug cannot smuggle a booking through the sync path.
SYNCABLE_ENTITIES = frozenset(SyncEntity)


class MutationOperation(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    #: Soft delete. Section 38 uses tombstones rather than removing rows, so a
    #: deletion propagates to other devices instead of silently reappearing.
    DELETE = "DELETE"


class MutationStatus(StrEnum):
    APPLIED = "APPLIED"
    #: Already processed. The stored result is returned again.
    DUPLICATE = "DUPLICATE"
    #: The server's version moved on. The client is told, and decides
    #: (section 128); nothing is overwritten.
    CONFLICT = "CONFLICT"
    #: The payload cannot be applied. The seller must fix something.
    REJECTED = "REJECTED"


class SyncMutation(Base, TenantOwned, PrimaryKeyMixin):
    """One mutation submitted by a device, and what became of it."""

    __tablename__ = "sync_mutations"
    __table_args__ = (
        # The idempotency guarantee. A replayed batch matches here and returns
        # the original outcome instead of executing again.
        sa.UniqueConstraint(
            "tenant_id", "mutation_id", name="uq_sync_mutations_tenant_id_mutation_id"
        ),
        sa.Index("ix_sync_mutations_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_sync_mutations_entity", "tenant_id", "entity_type", "entity_id"),
        sa.Index("ix_sync_mutations_device", "device_id", "created_at"),
    )

    #: Generated on the device (UUIDv7/UUID per section 37).
    mutation_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)

    entity_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    #: The client's id for the record, which becomes the server id on create.
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    operation: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: Version the client had when it made the edit. A mismatch is a conflict.
    base_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: Replayed verbatim on a duplicate submission.
    result: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: When the seller made the change on their device. Recorded for ordering
    #: offline edits; never used for money or aging, which use the server clock
    #: (section 69).
    client_timestamp: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )

    @property
    def mutation_status(self) -> MutationStatus:
        return MutationStatus(self.status)

    @property
    def was_applied(self) -> bool:
        return self.mutation_status is MutationStatus.APPLIED
