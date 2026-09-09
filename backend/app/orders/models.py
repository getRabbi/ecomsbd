"""Order and order item models.

Master spec sections 10.1, 31 and 70.

The order is a **commerce** object and nothing else. Section 1.4 is blunt about
why: "a parcel can be DELIVERED while its COD is still unpaid", so courier
status, COD settlement status and return status each get their own state
machine. An order that reached ``COMPLETED`` says the seller finished their
part; it says nothing about whether the money arrived.

Order items snapshot the product's name, SKU, price and **cost** at the moment
the order was created. Section 18.2: changing a product's cost tomorrow must
never rewrite the profit on an order sold today.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, SoftDeleteMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "ORDER_TRANSITIONS",
    "Order",
    "OrderChannel",
    "OrderItem",
    "OrderStatus",
    "can_transition",
]


class OrderStatus(StrEnum):
    """The frozen order lifecycle (master spec section 10.1).

    Deliberately short. Anything about a parcel belongs on the consignment;
    anything about money belongs on the receivable.
    """

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PACKED = "PACKED"
    #: A consignment exists. What that consignment is *doing* is its own state.
    FULFILLMENT_STARTED = "FULFILLMENT_STARTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.COMPLETED, OrderStatus.CANCELLED)

    @property
    def is_editable(self) -> bool:
        """Whether the seller may still change items and delivery details.

        Once fulfilment has started the shipping details are with a courier, and
        section 37 requires an explicit correction workflow rather than a
        silent edit.
        """
        return self in (OrderStatus.DRAFT, OrderStatus.CONFIRMED, OrderStatus.PACKED)


#: Allowed transitions. Anything absent is rejected, so an order cannot jump
#: from DRAFT straight to COMPLETED and skip the states that create stock
#: movements and receivables.
ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset(
        {OrderStatus.PACKED, OrderStatus.FULFILLMENT_STARTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.PACKED: frozenset(
        {OrderStatus.FULFILLMENT_STARTED, OrderStatus.CONFIRMED, OrderStatus.CANCELLED}
    ),
    OrderStatus.FULFILLMENT_STARTED: frozenset({OrderStatus.COMPLETED, OrderStatus.CANCELLED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in ORDER_TRANSITIONS[current]


class OrderChannel(StrEnum):
    """Where the order came from. Feeds the activation funnel (section 119)."""

    MANUAL = "MANUAL"
    PASTE_PARSE = "PASTE_PARSE"
    CSV_IMPORT = "CSV_IMPORT"
    #: Reserved: Messenger/page ingestion is a later-phase feature.
    MESSENGER = "MESSENGER"
    API = "API"


class Order(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A seller's order."""

    __tablename__ = "orders"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "order_number", name="uq_orders_tenant_id_order_number"),
        # Section 37: a client-generated id makes an offline create idempotent.
        # Replaying the same queued mutation finds the existing row instead of
        # inserting a second order.
        sa.UniqueConstraint("tenant_id", "client_id", name="uq_orders_tenant_id_client_id"),
        # Section 107's recommended index set.
        sa.Index("ix_orders_tenant_created_at", "tenant_id", "created_at"),
        sa.Index("ix_orders_tenant_status_created", "tenant_id", "status", "created_at"),
        sa.Index("ix_orders_tenant_business_date", "tenant_id", "business_date"),
        sa.Index("ix_orders_customer_created", "customer_id", "created_at"),
        # Duplicate detection scans recent orders for a phone.
        sa.Index("ix_orders_dup_scan", "tenant_id", "customer_phone_hmac", "created_at"),
        sa.Index("ix_orders_tenant_updated_at", "tenant_id", "updated_at"),
        sa.CheckConstraint("subtotal_paisa >= 0", name="subtotal_non_negative"),
        sa.CheckConstraint("discount_paisa >= 0", name="discount_non_negative"),
        sa.CheckConstraint("delivery_fee_paisa >= 0", name="delivery_fee_non_negative"),
        sa.CheckConstraint("cod_amount_paisa >= 0", name="cod_non_negative"),
    )

    #: Seller-facing, human-searchable: ``CP-20260909-0042`` (section 70).
    #: The ``CP-`` prefix is a stored technical identifier, not branding, and is
    #: tenant-configurable.
    order_number: Mapped[str] = mapped_column(sa.String(32), nullable=False)

    #: UUID minted on the device. Present for every order, so an offline create
    #: and its server row are the same thing.
    client_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: Denormalised from the customer so duplicate detection can scan without
    #: joining, and so an order keeps its recipient if the customer is merged.
    customer_phone_hmac: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(sa.String(160), nullable=True)
    customer_phone_masked: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    # --- delivery address snapshot (section 71) ------------------------------
    #: Copied at order time. The customer editing their address later must not
    #: change where an already-placed parcel was sent.
    delivery_address_raw: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    delivery_address_normalized: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    delivery_district: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    delivery_area: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=OrderStatus.DRAFT, index=True
    )
    channel: Mapped[str] = mapped_column(sa.String(20), nullable=False, default=OrderChannel.MANUAL)

    #: The seller's business date in their timezone (section 69). An explicit
    #: DATE, never derived by truncating a UTC timestamp.
    business_date: Mapped[date] = mapped_column(sa.Date, nullable=False)

    # --- money, all integer paisa -------------------------------------------
    subtotal_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    discount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    delivery_fee_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: What the courier should collect. Not what was collected — that is the COD
    #: receivable's job, and it only exists after a delivery outcome.
    cod_amount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: The original pasted text, kept verbatim. Section 96: a parse failure must
    #: return the seller's text, never lose it.
    source_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    packed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    #: Bumped on every server-side change. The client sends the version it read;
    #: a mismatch is a conflict rather than a silent overwrite (section 37).
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    metadata_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="OrderItem.position",
    )

    @property
    def order_status(self) -> OrderStatus:
        return OrderStatus(self.status)

    @property
    def is_editable(self) -> bool:
        return self.order_status.is_editable and self.deleted_at is None

    @property
    def computed_subtotal_paisa(self) -> int:
        return sum(item.line_total_paisa for item in self.items)

    @property
    def estimated_item_cost_paisa(self) -> int:
        """Cost of goods from the snapshots. An input to profit, not profit."""
        return sum(item.unit_cost_snapshot_paisa * item.quantity for item in self.items)

    def mark_status(self, target: OrderStatus, *, at: datetime | None = None) -> None:
        """Apply a validated transition and stamp its timestamp."""
        moment = at or utc_now()
        self.status = target
        match target:
            case OrderStatus.CONFIRMED:
                self.confirmed_at = moment
            case OrderStatus.PACKED:
                self.packed_at = moment
            case OrderStatus.COMPLETED:
                self.completed_at = moment
            case OrderStatus.CANCELLED:
                self.cancelled_at = moment
            case _:
                pass
        self.version += 1


class OrderItem(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One line of an order, with its economics frozen at creation.

    ``product_id`` is nullable on purpose: master spec section 20 allows an
    "optional free-text order item", because sellers routinely add something
    that is not in their catalogue and should not be forced to create a product
    first.
    """

    __tablename__ = "order_items"
    __table_args__ = (
        sa.Index("ix_order_items_order_position", "order_id", "position"),
        sa.Index("ix_order_items_product", "product_id"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint("unit_price_paisa >= 0", name="unit_price_non_negative"),
        sa.CheckConstraint("unit_cost_snapshot_paisa >= 0", name="unit_cost_non_negative"),
        sa.CheckConstraint("discount_paisa >= 0", name="item_discount_non_negative"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: NULL for a free-text line, or when the product was later archived and
    #: the reference relaxed. The snapshot below stays readable either way.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    # --- snapshots (section 18.2) -------------------------------------------
    product_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    variant_label: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    unit_price_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: The product's cost *at order time*. This is the number that keeps old
    #: profit correct when the seller's supplier price changes.
    unit_cost_snapshot_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    discount_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    note: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)

    order: Mapped[Order] = relationship(back_populates="items")

    @property
    def line_total_paisa(self) -> int:
        """What this line contributes to the order subtotal."""
        return max(0, self.unit_price_paisa * self.quantity - self.discount_paisa)

    @property
    def line_cost_paisa(self) -> int:
        return self.unit_cost_snapshot_paisa * self.quantity
