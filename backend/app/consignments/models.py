"""Consignment and consignment item models.

Master spec sections 10.2, 31 and 72.

**Why this ships in Phase B, before any courier integration.** Section 72 is
explicit: an order may contain several items and may produce several parcels, so
"a partial delivery cannot be modelled correctly with only an order-level COD
amount". Every later phase depends on the mapping being right —

    partial delivery  -> which units actually arrived
    realized revenue  -> collectible for the delivered units only
    stock restore     -> how many units to put back
    profit            -> cost snapshot of the units that were fulfilled
    COD receivable    -> the amount that is genuinely collectible

Adding this table after the money engine exists would mean rewriting the money
engine. It is cheaper and safer to have it now, empty, than to bolt it on later.

Nothing in this module calls a provider. The state machine is the frozen one
from section 10.2, including ``BOOKING_UNKNOWN`` — the state a create call ends
in when it times out, which must never be collapsed into ``FAILED`` (sections
11, 36).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "MANUAL_PROVIDER",
    "Consignment",
    "ConsignmentItem",
    "ConsignmentStatus",
]

#: The provider used when a seller manages the parcel by hand. Master spec
#: section 5 makes manual courier mode a first-class V1 path, not a fallback:
#: order, tracking entry, statement upload and reconciliation all work without
#: any courier API.
MANUAL_PROVIDER = "manual"


class ConsignmentStatus(StrEnum):
    """The frozen consignment lifecycle (master spec section 10.2)."""

    NOT_BOOKED = "NOT_BOOKED"
    #: A create call is in flight.
    BOOKING = "BOOKING"
    #: The create call timed out or returned something unreadable. The parcel
    #: may or may not exist at the provider. Section 11: a network timeout must
    #: never automatically imply "not booked", and this must never be retried
    #: blindly — reconciliation against the merchant reference resolves it.
    BOOKING_UNKNOWN = "BOOKING_UNKNOWN"
    BOOKED = "BOOKED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    #: Some units delivered, some returned. Needs ConsignmentItem to say which.
    PARTIAL_DELIVERED = "PARTIAL_DELIVERED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    RETURNING = "RETURNING"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    LOST = "LOST"
    DAMAGED = "DAMAGED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ConsignmentStatus.DELIVERED,
            ConsignmentStatus.PARTIAL_DELIVERED,
            ConsignmentStatus.RETURNED,
            ConsignmentStatus.CANCELLED,
            ConsignmentStatus.LOST,
            ConsignmentStatus.DAMAGED,
            ConsignmentStatus.FAILED,
        )

    @property
    def is_ambiguous(self) -> bool:
        """Whether the provider-side outcome is genuinely unknown."""
        return self in (ConsignmentStatus.BOOKING, ConsignmentStatus.BOOKING_UNKNOWN)

    @property
    def produces_collectible(self) -> bool:
        """Whether this outcome can create a COD receivable.

        Only a verified delivery outcome does. ``DELIVERED`` alone is not the
        same as "paid" — that is the receivable's own state machine
        (section 1.4).
        """
        return self in (
            ConsignmentStatus.DELIVERED,
            ConsignmentStatus.PARTIAL_DELIVERED,
        )


class Consignment(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A parcel handed to a courier, or recorded by hand in manual mode."""

    __tablename__ = "consignments"
    __table_args__ = (
        # Section 108: a provider's own id must be unique within that provider
        # for this tenant, so a duplicated webhook cannot create a second parcel.
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_consignment_id",
            name="uq_consignments_tenant_provider_consignment_id",
        ),
        # The stable reference sent to the provider. This is what a
        # BOOKING_UNKNOWN is resolved against (section 36), so it must be
        # unique per provider or recovery cannot identify the parcel.
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "merchant_reference",
            name="uq_consignments_tenant_provider_merchant_reference",
        ),
        sa.Index("ix_consignments_tenant_status", "tenant_id", "provider", "status"),
        sa.Index("ix_consignments_order", "order_id"),
        sa.Index("ix_consignments_tenant_created", "tenant_id", "created_at"),
        sa.CheckConstraint("cod_amount_paisa >= 0", name="consignment_cod_non_negative"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False, default=MANUAL_PROVIDER)
    #: NULL until a provider confirms a parcel, or forever in manual mode.
    provider_consignment_id: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(sa.String(120), nullable=True, index=True)
    #: Ours, stable, sent to the provider on create. Derived from the order and
    #: booking attempt so recovery can look the parcel up (section 11).
    merchant_reference: Mapped[str] = mapped_column(sa.String(120), nullable=False)

    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=ConsignmentStatus.NOT_BOOKED, index=True
    )
    #: The provider's own status string, kept verbatim. Section 62.13: every
    #: provider status keeps its raw original, because a mapping mistake must
    #: stay diagnosable.
    provider_raw_status: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    cod_amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    booked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    picked_up_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Whole provider payloads live in courier_events; this holds the small
    #: amount of normalised metadata the domain itself needs.
    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    # --- provider integration (phase C) ------------------------------------

    #: Which credentials booked this parcel. Needed by every later provider
    #: call about it: a shop that reconnects with a different key must still be
    #: able to look up a parcel booked under the old one, and a status poll has
    #: to know which account to authenticate as.
    courier_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("courier_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    #: How many create attempts this parcel has had. Never resets, and never
    #: causes a new merchant reference — the reference is stable across every
    #: attempt, which is what makes an ambiguous create recoverable.
    booking_attempt_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: When the provider's status was last *observed*. Distinct from
    #: ``last_status_at``, which is when the parcel's own state last changed:
    #: a parcel polled hourly for a week has a fresh observation and a
    #: week-old state, and confusing the two makes a stale sync invisible.
    provider_status_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: When this parcel is next due a status poll. Null means "not on the
    #: polling schedule" — a manual parcel, or one that has finished. Adaptive:
    #: a fresh parcel is checked often, a settled one not at all.
    next_poll_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    poll_failure_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: True once a provider observation says the outcome needs a person to
    #: supply quantities — ``partial_delivered`` carries none. The parcel's
    #: status is not advanced to a final state until they do.
    needs_quantity_resolution: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False
    )

    items: Mapped[list[ConsignmentItem]] = relationship(
        back_populates="consignment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    @property
    def consignment_status(self) -> ConsignmentStatus:
        return ConsignmentStatus(self.status)

    @property
    def is_manual(self) -> bool:
        return self.provider == MANUAL_PROVIDER

    @property
    def collectible_paisa(self) -> int:
        """Amount genuinely collectible from the delivered units.

        Section 17.8: a partial delivery creates a partial collectible amount
        and never assumes the original COD. For a full delivery this equals the
        parcel's COD; for a partial one it is the delivered units only.
        """
        if not self.consignment_status.produces_collectible:
            return 0
        if self.consignment_status is ConsignmentStatus.DELIVERED and not self.items:
            return self.cod_amount_paisa
        return sum(item.delivered_collectible_paisa for item in self.items)


class ConsignmentItem(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """How many units of one order line were shipped, delivered and returned.

    The row master spec section 72 adds specifically so partial delivery can be
    modelled. Its invariant is enforced by a database check constraint, not only
    in Python: application validation is not enough for something this load
    bearing (section 108).
    """

    __tablename__ = "consignment_items"
    __table_args__ = (
        sa.UniqueConstraint(
            "consignment_id",
            "order_item_id",
            name="uq_consignment_items_consignment_id_order_item_id",
        ),
        sa.Index("ix_consignment_items_order_item", "order_item_id"),
        sa.CheckConstraint("qty_shipped > 0", name="qty_shipped_positive"),
        sa.CheckConstraint("qty_delivered >= 0", name="qty_delivered_non_negative"),
        sa.CheckConstraint("qty_returned >= 0", name="qty_returned_non_negative"),
        # The section 72 invariant, in the database.
        sa.CheckConstraint(
            "qty_delivered + qty_returned <= qty_shipped",
            name="fulfilled_within_shipped",
        ),
        sa.CheckConstraint("unit_collectible_paisa >= 0", name="unit_collectible_non_negative"),
        sa.CheckConstraint("unit_cost_snapshot_paisa >= 0", name="unit_cost_snapshot_non_negative"),
    )

    consignment_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("consignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("order_items.id", ondelete="RESTRICT"),
        nullable=False,
    )

    qty_shipped: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    qty_delivered: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    qty_returned: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: Collectible per unit, snapshotted so a later price change cannot alter
    #: what this parcel was supposed to collect.
    unit_collectible_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: Cost per unit at fulfilment. Profit uses the cost of the units actually
    #: fulfilled (section 72).
    unit_cost_snapshot_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    consignment: Mapped[Consignment] = relationship(back_populates="items")

    @property
    def qty_unresolved(self) -> int:
        """Units shipped whose outcome is not yet known."""
        return self.qty_shipped - self.qty_delivered - self.qty_returned

    @property
    def delivered_collectible_paisa(self) -> int:
        return self.qty_delivered * self.unit_collectible_paisa

    @property
    def delivered_cost_paisa(self) -> int:
        return self.qty_delivered * self.unit_cost_snapshot_paisa

    @property
    def returned_cost_paisa(self) -> int:
        return self.qty_returned * self.unit_cost_snapshot_paisa

    def is_valid(self) -> bool:
        """Mirror of the database check, for use before a flush."""
        return (
            self.qty_shipped > 0
            and self.qty_delivered >= 0
            and self.qty_returned >= 0
            and self.qty_delivered + self.qty_returned <= self.qty_shipped
        )
