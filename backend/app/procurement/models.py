"""Suppliers, purchase orders, goods receipts, supplier payments and stock locations (V3.5).

Stock itself never lives here. Every accepted unit is a movement in the V2
stock ledger (``stock_movements``); a location balance is a cache for the
non-default locations only, kept in the same transaction as the movement that
changed it. The default location holds whatever is not allocated elsewhere, so
a shop that never adds a second location behaves exactly as before.

Supplier money is tracked here and nowhere else: a supplier payable is not a
COD receivable, never enters the financial ledger and is never reconciled
against courier payouts.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, Paisa, TZDateTime

PO_STATUSES = ("DRAFT", "ORDERED", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED")
OPEN_PO = ("ORDERED", "PARTIALLY_RECEIVED")
REJECT_REASONS = ("DAMAGED", "WRONG_ITEM", "EXPIRED", "OTHER")
PAYMENT_METHODS = ("CASH", "BKASH", "NAGAD", "ROCKET", "BANK", "CHEQUE", "OTHER")


def item_key(product_id: uuid.UUID, variant_id: uuid.UUID | None) -> str:
    """One stock-keeping item: a product, or one variant of it."""
    return f"{product_id}:{variant_id or '-'}"


class _Item:
    product_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("products.id"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("product_variants.id"))
    item_key: Mapped[str] = mapped_column(sa.String(80))


class Supplier(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "suppliers"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "name_key"),)
    name: Mapped[str] = mapped_column(sa.String(160))
    name_key: Mapped[str] = mapped_column(sa.String(160))
    contact_name: Mapped[str | None] = mapped_column(sa.String(160))
    phone: Mapped[str | None] = mapped_column(sa.String(32))
    email: Mapped[str | None] = mapped_column(sa.String(254))
    address: Mapped[str | None] = mapped_column(sa.String(500))
    notes: Mapped[str | None] = mapped_column(sa.String(1000))
    #: Days after the first receipt that a PO's payment falls due, by default.
    payment_terms_days: Mapped[int | None] = mapped_column()
    #: V3.6: the seller's own "days from order to arrival", used for reorder
    #: suggestions until enough received orders show the real figure.
    lead_time_days: Mapped[int | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)


class SupplierItem(_Item, Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """What a supplier sells the shop, and what it last cost."""

    __tablename__ = "supplier_items"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "supplier_id", "item_key"),
        sa.Index("ix_supplier_items_item", "tenant_id", "item_key"),
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("suppliers.id"))
    supplier_sku: Mapped[str | None] = mapped_column(sa.String(64))
    last_unit_cost_paisa: Mapped[int | None] = mapped_column(Paisa)
    last_received_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: The supplier an automatic draft purchase order goes to. One per item.
    is_preferred: Mapped[bool] = mapped_column(default=False)


class Warehouse(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "warehouses"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "code"),)
    name: Mapped[str] = mapped_column(sa.String(80))
    code: Mapped[str] = mapped_column(sa.String(24))
    address: Mapped[str | None] = mapped_column(sa.String(300))
    is_default: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class WarehouseStock(_Item, Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """Units held at a non-default location. The default location is derived."""

    __tablename__ = "warehouse_stock"
    __table_args__ = (
        sa.CheckConstraint("quantity >= 0", name="warehouse_stock_non_negative"),
        sa.UniqueConstraint("tenant_id", "warehouse_id", "item_key"),
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("warehouses.id"))
    quantity: Mapped[int] = mapped_column(default=0)


class PurchaseOrder(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        sa.CheckConstraint("paid_paisa >= 0", name="purchase_orders_paid_non_negative"),
        sa.UniqueConstraint("tenant_id", "number"),
        sa.Index("ix_purchase_orders_status", "tenant_id", "status"),
    )
    number: Mapped[str] = mapped_column(sa.String(24))
    supplier_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("suppliers.id"))
    #: Receiving destination. ``None``: the default location.
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(sa.String(24), default="DRAFT")
    expected_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    reference: Mapped[str | None] = mapped_column(sa.String(120))
    notes: Mapped[str | None] = mapped_column(sa.String(1000))
    #: Ordered value: sum of quantity x unit cost.
    total_paisa: Mapped[int] = mapped_column(Paisa, default=0)
    #: What the shop owes: accepted units x unit cost, accrued per receipt.
    received_value_paisa: Mapped[int] = mapped_column(Paisa, default=0)
    paid_paisa: Mapped[int] = mapped_column(Paisa, default=0)
    payment_due_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    ordered_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    first_received_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    received_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: SELLER or AUTOMATION (a draft a workflow prepared; never ordered by it).
    source: Mapped[str] = mapped_column(sa.String(16), default="SELLER")
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID)
    ordered_by: Mapped[uuid.UUID | None] = mapped_column(GUID)
    version: Mapped[int] = mapped_column(default=1)


class PurchaseOrderLine(_Item, Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        sa.CheckConstraint("quantity_ordered > 0", name="po_line_quantity_positive"),
        sa.UniqueConstraint("tenant_id", "purchase_order_id", "item_key"),
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("purchase_orders.id"))
    description: Mapped[str] = mapped_column(sa.String(240))
    quantity_ordered: Mapped[int] = mapped_column()
    #: Accepted into stock.
    quantity_received: Mapped[int] = mapped_column(default=0)
    #: Delivered but refused (damaged, wrong item): never entered stock.
    quantity_rejected: Mapped[int] = mapped_column(default=0)
    unit_cost_paisa: Mapped[int] = mapped_column(Paisa)
    #: Received cost becomes the item's current cost (see receive()).
    update_cost: Mapped[bool] = mapped_column(default=True)


class GoodsReceipt(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "goods_receipts"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "idempotency_key"),)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("purchase_orders.id"))
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("warehouses.id"))
    received_at: Mapped[datetime] = mapped_column(TZDateTime)
    supplier_reference: Mapped[str | None] = mapped_column(sa.String(120))
    note: Mapped[str | None] = mapped_column(sa.String(500))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    idempotency_key: Mapped[str] = mapped_column(sa.String(120))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    accepted_units: Mapped[int] = mapped_column(default=0)
    rejected_units: Mapped[int] = mapped_column(default=0)
    value_paisa: Mapped[int] = mapped_column(Paisa, default=0)
    #: Set only when a line was received beyond its ordered quantity.
    over_receipt_reason: Mapped[str | None] = mapped_column(sa.String(300))


class GoodsReceiptLine(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "goods_receipt_lines"
    receipt_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("goods_receipts.id"))
    line_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("purchase_order_lines.id"))
    quantity_accepted: Mapped[int] = mapped_column(default=0)
    quantity_rejected: Mapped[int] = mapped_column(default=0)
    reject_reason: Mapped[str | None] = mapped_column(sa.String(24))
    movement_id: Mapped[uuid.UUID | None] = mapped_column(GUID, sa.ForeignKey("stock_movements.id"))


class SupplierPayment(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "supplier_payments"
    __table_args__ = (
        sa.CheckConstraint("amount_paisa > 0", name="supplier_payment_positive"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("purchase_orders.id"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("suppliers.id"))
    amount_paisa: Mapped[int] = mapped_column(Paisa)
    paid_at: Mapped[datetime] = mapped_column(TZDateTime)
    method: Mapped[str] = mapped_column(sa.String(16))
    reference: Mapped[str | None] = mapped_column(sa.String(120))
    note: Mapped[str | None] = mapped_column(sa.String(300))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    idempotency_key: Mapped[str] = mapped_column(sa.String(120))


class StockTransfer(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stock_transfers"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.UniqueConstraint("tenant_id", "number"),
    )
    number: Mapped[str] = mapped_column(sa.String(24))
    from_warehouse_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("warehouses.id"))
    to_warehouse_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("warehouses.id"))
    #: Transfers complete in one transaction; COMPLETED is the only status today.
    status: Mapped[str] = mapped_column(sa.String(16), default="COMPLETED")
    reference: Mapped[str | None] = mapped_column(sa.String(120))
    note: Mapped[str | None] = mapped_column(sa.String(500))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    idempotency_key: Mapped[str] = mapped_column(sa.String(120))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class StockTransferLine(_Item, Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stock_transfer_lines"
    __table_args__ = (sa.CheckConstraint("quantity > 0", name="transfer_line_quantity_positive"),)
    transfer_id: Mapped[uuid.UUID] = mapped_column(GUID, sa.ForeignKey("stock_transfers.id"))
    quantity: Mapped[int] = mapped_column()
