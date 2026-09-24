"""Product catalogue and the stock movement ledger.

Master spec sections 10.4, 20 and 31.

The rule that shapes this module: **stock is never a mutable number that
someone sets.** Section 10.4 is explicit — "use stock movements, not a mutable
'restocked' boolean". Every change to what a seller has on hand is an
append-only row saying how much moved, why, and because of which order. The
running total on :class:`Product` is a cache of that ledger, maintained in the
same transaction, and a test recomputes it from the movements to prove they
agree.

Without this, a returned parcel that was never restocked is invisible, and
"returned but inventory not restored" is one of the reconciliation cases the
product exists to surface (section 16).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, Paisa, TZDateTime

__all__ = [
    "MOVEMENT_SIGN",
    "Product",
    "ProductVariant",
    "StockMovement",
    "StockMovementReason",
    "StockMovementSource",
]


class StockMovementReason(StrEnum):
    """Why stock moved.

    These are the names from master spec section 10.4, used verbatim. The value
    is stored, so renaming one would rewrite the meaning of historical rows.
    """

    #: Initial count when a product is created or first imported.
    OPENING = "OPENING"
    #: A seller correcting the count by hand. Requires a note.
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    #: Held for an order that is not yet booked. Reserve policy (section 20).
    BOOKED_RESERVE = "BOOKED_RESERVE"
    #: Removed from stock because a parcel was booked. The default decrement
    #: moment per section 20.
    BOOKED_DECREMENT = "BOOKED_DECREMENT"
    #: Order cancelled before pickup.
    CANCEL_RESTORE = "CANCEL_RESTORE"
    #: Whole parcel came back and is sellable.
    RETURN_RESTORE = "RETURN_RESTORE"
    #: Some units came back. Needs consignment_items to know how many
    #: (section 72).
    PARTIAL_RETURN_RESTORE = "PARTIAL_RETURN_RESTORE"
    #: Came back damaged; written off rather than restored.
    DAMAGED_WRITE_OFF = "DAMAGED_WRITE_OFF"

    #: Extension beyond section 10.4: a CSV import that set or corrected stock.
    #: Kept distinct from MANUAL_ADJUSTMENT so an import that went wrong can be
    #: found and reversed as a unit.
    IMPORT_ADJUSTMENT = "IMPORT_ADJUSTMENT"

    #: V2.2: new goods arrived on the shelf. Not a purchase order and not a
    #: payable — only the quantity, with an optional unit cost for reference.
    RESTOCK = "RESTOCK"

    #: V3.2: a connected store is the stock authority and its count changed.
    #: Always a delta computed against the ledger, never an overwrite.
    EXTERNAL_SYNC = "EXTERNAL_SYNC"


#: Expected sign of each reason's delta, or ``None`` where either direction is
#: legitimate. Enforced by the service so a "restore" can never quietly remove
#: stock.
MOVEMENT_SIGN: dict[StockMovementReason, int | None] = {
    StockMovementReason.OPENING: None,
    StockMovementReason.MANUAL_ADJUSTMENT: None,
    StockMovementReason.BOOKED_RESERVE: -1,
    StockMovementReason.BOOKED_DECREMENT: -1,
    StockMovementReason.CANCEL_RESTORE: +1,
    StockMovementReason.RETURN_RESTORE: +1,
    StockMovementReason.PARTIAL_RETURN_RESTORE: +1,
    StockMovementReason.DAMAGED_WRITE_OFF: -1,
    StockMovementReason.IMPORT_ADJUSTMENT: None,
    StockMovementReason.RESTOCK: +1,
    StockMovementReason.EXTERNAL_SYNC: None,
}


class StockMovementSource(StrEnum):
    """What caused the movement, for provenance."""

    SELLER = "SELLER"
    ORDER = "ORDER"
    COURIER_EVENT = "COURIER_EVENT"
    IMPORT = "IMPORT"
    SYSTEM = "SYSTEM"
    #: V3.2: a connected store (Integrations Hub) moved stock.
    INTEGRATION = "INTEGRATION"


class Product(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A sellable product.

    Deliberately **not** soft-deleted with ``deleted_at``: a product referenced
    by a historical order is archived, not deleted, so old order economics stay
    readable. ``archived_at`` says "stop offering this", never "forget it".
    """

    __tablename__ = "products"
    __table_args__ = (
        # SKU is optional but unique per tenant when present. NULLs compare as
        # distinct on both PostgreSQL and SQLite, so several products may have
        # no SKU.
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_id_sku"),
        sa.Index("ix_products_tenant_active_name", "tenant_id", "is_active", "name"),
        sa.Index("ix_products_tenant_created_at", "tenant_id", "created_at"),
        sa.CheckConstraint("cost_paisa >= 0", name="cost_non_negative"),
        sa.CheckConstraint("default_selling_price_paisa >= 0", name="selling_price_non_negative"),
    )

    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    #: What the seller pays. Snapshotted onto each order item at order time, so
    #: changing it never rewrites historical profit (master spec section 18.2).
    cost_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)
    #: Suggested price. The order item carries the price actually charged.
    default_selling_price_paisa: Mapped[int] = mapped_column(Paisa, nullable=False, default=0)

    #: False for made-to-order or service lines that have no countable stock.
    stock_tracking_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    #: Cloudflare R2 object key. The bucket is not provisioned yet, so this is
    #: written only once uploads ship.
    image_storage_key: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    #: Running total, maintained in the same transaction as every movement.
    #: Section 10.4 permits a transactionally-maintained figure; the ledger
    #: remains the source of truth and ``recalculate_stock`` can rebuild this.
    stock_on_hand: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    attributes: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: V2.2: stock is held per variant rather than on the product. Once set it
    #: stays set — variants are deactivated, never deleted, because orders
    #: reference them — and ``stock_on_hand`` is then the sum of the variants'.
    has_variants: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    movements: Mapped[list[StockMovement]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    #: Eager: every product read that shows stock needs its variants, and a
    #: lazy load is an error under asyncio. ``selectin`` is one query per batch
    #: of products, never one per product.
    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product",
        lazy="selectin",
        order_by="(ProductVariant.position, ProductVariant.created_at)",
        passive_deletes=True,
    )

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_low_stock(self) -> bool:
        """Low on the product's own threshold or, with variants, on any active
        variant's. A variant product's own threshold is not consulted."""
        if not self.stock_tracking_enabled:
            return False
        if self.has_variants:
            return any(variant.is_low_stock for variant in self.variants if variant.is_active)
        if self.low_stock_threshold is None:
            return False
        return self.stock_on_hand <= self.low_stock_threshold

    @property
    def margin_paisa(self) -> int:
        """Indicative unit margin. Not a profit figure — that needs settlement."""
        return self.default_selling_price_paisa - self.cost_paisa


class ProductVariant(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One sellable version of a product — "Black / M".

    Deliberately small: a display name, optional option values, its own SKU,
    stock and threshold, and optional price/cost overrides. Not an attribute
    engine. Never deleted: an order line may point at it.
    """

    __tablename__ = "product_variants"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "sku", name="uq_product_variants_tenant_id_sku"),
        sa.UniqueConstraint("product_id", "name", name="uq_product_variants_product_id_name"),
        sa.CheckConstraint("price_paisa IS NULL OR price_paisa >= 0", name="price_non_negative"),
        sa.CheckConstraint("cost_paisa IS NULL OR cost_paisa >= 0", name="cost_non_negative"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: {"color": "Black", "size": "M"}. Informational; ``name`` is what is shown.
    options: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    sku: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    #: Override the product's price/cost when set. Snapshotted onto the order
    #: line exactly like the product's, so a later change never rewrites history.
    price_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    cost_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)

    #: Running total of this variant's movements. Same rules as the product's.
    stock_on_hand: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    product: Mapped[Product] = relationship(back_populates="variants")

    @property
    def is_low_stock(self) -> bool:
        if self.low_stock_threshold is None:
            return False
        return self.stock_on_hand <= self.low_stock_threshold


class StockMovement(Base, TenantOwned, PrimaryKeyMixin):
    """One append-only change to a product's stock.

    Never updated and never deleted. A mistake is corrected by recording the
    opposite movement, which keeps the history explainable — the same rule the
    financial ledger follows (master spec section 80).
    """

    __tablename__ = "stock_movements"
    __table_args__ = (
        sa.Index("ix_stock_movements_product_occurred", "product_id", "occurred_at"),
        sa.Index("ix_stock_movements_tenant_occurred", "tenant_id", "occurred_at"),
        sa.Index("ix_stock_movements_order_id", "order_id"),
        sa.Index("ix_stock_movements_variant_occurred", "variant_id", "occurred_at"),
        sa.CheckConstraint("quantity_delta <> 0", name="quantity_delta_non_zero"),
        # A retried request, a re-run dispatch or a resumed import carries the
        # same key, and the database refuses the second row.
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_stock_movements_tenant_id_idempotency_key"
        ),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: The variant whose stock moved. Required on a product with variants.
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, sa.ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True
    )

    #: Set when the movement came from an order's lifecycle.
    order_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    #: Set when a courier event caused it. The consignment lives in its own
    #: table; no FK here because a movement may outlive a cancelled parcel.
    consignment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    #: Signed. Negative removes stock, positive restores it.
    quantity_delta: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: Running balance immediately after this movement, so history can be read
    #: without summing every prior row. The variant's balance when the movement
    #: is for a variant, the product's otherwise.
    balance_after: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    reason: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, default=StockMovementSource.SELLER
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    #: Required for MANUAL_ADJUSTMENT: an unexplained correction to stock is
    #: indistinguishable from a bug.
    note: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)
    #: A seller's own reference for a restock ("Invoice 42"). Free text.
    reference: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    #: What one unit cost on a restock. Informational: V2.2 does no inventory
    #: valuation, and profit keeps using the order line's cost snapshot.
    unit_cost_paisa: Mapped[int | None] = mapped_column(Paisa, nullable=True)
    #: Makes a retried write a no-op rather than a second movement.
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    #: When the change happened in the real world, which is not always when the
    #: row was written (an import backfills history).
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, index=True
    )

    product: Mapped[Product] = relationship(back_populates="movements")

    @property
    def is_increase(self) -> bool:
        return self.quantity_delta > 0
