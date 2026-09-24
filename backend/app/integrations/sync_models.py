"""V3.2 two-way sync state: catalog links, conflicts and per-order provider state.

None of these is a second truth. Stock lives in the Inventory V2 ledger, orders
in the order service; these rows only remember how an external item maps to an
ecomsbd one and what was last agreed with the store, so a change on either side
can be told apart from a change on both.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime


class IntegrationLink(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One sellable external item (a Shopify variant, a WooCommerce product or
    variation) and the ecomsbd product or variant it is mapped to."""

    __tablename__ = "integration_links"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "connection_id", "external_key"),
        # One ecomsbd item maps to at most one external item per connection:
        # a second claim on the same SKU becomes a conflict, never a merge.
        sa.UniqueConstraint("tenant_id", "connection_id", "internal_key"),
        sa.Index("ix_integration_link_state", "connection_id", "state"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id")
    )
    #: "<external product id>:<external variant id or empty>".
    external_key: Mapped[str] = mapped_column(sa.String(200))
    external_product_id: Mapped[str] = mapped_column(sa.String(100))
    external_variant_id: Mapped[str | None] = mapped_column(sa.String(100))
    #: Shopify InventoryItem id; stock is set per item and location there.
    external_inventory_id: Mapped[str | None] = mapped_column(sa.String(100))
    external_sku: Mapped[str | None] = mapped_column(sa.String(100))
    external_title: Mapped[str] = mapped_column(sa.String(300))
    external_price_paisa: Mapped[int | None] = mapped_column()
    #: Whether the store counts stock for this item at all.
    external_tracked: Mapped[bool] = mapped_column(default=False)
    external_qty: Mapped[int | None] = mapped_column()
    external_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    product_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("products.id"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("product_variants.id"))
    #: "<product id>:<variant id or empty>" while MATCHED, else NULL.
    internal_key: Mapped[str | None] = mapped_column(sa.String(80))
    #: UNMATCHED, MATCHED, CONFLICT, IGNORED or DELETED.
    state: Mapped[str] = mapped_column(sa.String(16), default="UNMATCHED")
    #: SKU, MANUAL or CREATED.
    match_source: Mapped[str | None] = mapped_column(sa.String(16))
    #: The store's quantity both sides last agreed on.
    synced_qty: Mapped[int | None] = mapped_column()
    #: ecomsbd's available-to-sell at that moment.
    local_basis: Mapped[int | None] = mapped_column()
    #: When a one-sided change was first seen; a grace period lets the order
    #: that explains it arrive before anything is called a conflict.
    pending_since: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class IntegrationConflict(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """A state ecomsbd will not settle on its own. A person chooses."""

    __tablename__ = "integration_conflicts"
    __table_args__ = (
        # One open conflict per fingerprint; open_key is cleared on resolution.
        sa.UniqueConstraint("tenant_id", "connection_id", "open_key"),
        sa.Index("ix_integration_conflict_state", "connection_id", "status"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id")
    )
    provider: Mapped[str] = mapped_column(sa.String(24))
    kind: Mapped[str] = mapped_column(sa.String(40))
    #: PRODUCT, INVENTORY or ORDER.
    entity: Mapped[str] = mapped_column(sa.String(16))
    link_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("integration_links.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("orders.id"))
    #: {"ecomsbd": ..., "external": ...}: what each side says, nothing secret.
    detail: Mapped[dict] = mapped_column(JSONColumn, default=dict)
    recommended: Mapped[str] = mapped_column(sa.String(40))
    options: Mapped[list] = mapped_column(JSONColumn, default=list)
    #: OPEN, RESOLVED or DISMISSED.
    status: Mapped[str] = mapped_column(sa.String(16), default="OPEN")
    open_key: Mapped[str | None] = mapped_column(sa.String(200))
    resolution: Mapped[str | None] = mapped_column(sa.String(40))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class IntegrationOrderState(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """What the store says about one imported order, and what was pushed to it."""

    __tablename__ = "integration_order_states"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "connection_id", "order_id"),)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID, sa.ForeignKey("integration_connections.id")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("orders.id"))
    external_order_id: Mapped[str] = mapped_column(sa.String(200))
    #: The store's own status word ("processing", "CANCELLED"), as last seen.
    provider_status: Mapped[str | None] = mapped_column(sa.String(40))
    #: The last ecomsbd state successfully written to the store.
    pushed_status: Mapped[str | None] = mapped_column(sa.String(40))
    #: Shopify fulfillment id or WooCommerce note id for the tracking push.
    fulfillment_ref: Mapped[str | None] = mapped_column(sa.String(200))
    carrier: Mapped[str | None] = mapped_column(sa.String(60))
    tracking_code: Mapped[str | None] = mapped_column(sa.String(120))
