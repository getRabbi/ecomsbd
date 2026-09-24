"""Procurement on top of the V2 stock ledger (V3.5).

Rules that shape this module:

* **Stock only moves through ``StockService``.** Receiving writes one RESTOCK
  movement (source PURCHASE) per accepted line, keyed by receipt and line, so a
  retried receipt can never add units twice. A transfer writes a
  TRANSFER_OUT/TRANSFER_IN pair that nets to zero. Nothing here sets a total.
* **Every mutation holds the shop lock** (``lock_shop``) before it reads a
  purchase order or a location balance, so two devices receiving the same
  order cannot both pass the over-receipt check.
* **Cost.** When a line says ``update_cost`` (the default), the unit cost of
  each receipt becomes the item's current cost: the figure *future* orders
  snapshot. Nothing is averaged and no past order is revalued; that is the
  rule V2.2 restock already follows.
* **Supplier money stays here.** A payable is ``received_value - paid`` on the
  purchase order. It never enters the financial ledger and is never mixed with
  COD receivables or courier payouts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.idempotency import request_hash
from app.common.operation_lock import lock_shop
from app.common.outbox import OutboxTopic, enqueue
from app.core.clock import ensure_utc, utc_now
from app.core.errors import (
    ConflictError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError,
)
from app.procurement.models import (
    OPEN_PO,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    StockTransfer,
    StockTransferLine,
    Supplier,
    SupplierItem,
    SupplierPayment,
    Warehouse,
    WarehouseStock,
    item_key,
)
from app.products.models import (
    Product,
    ProductVariant,
    StockMovement,
    StockMovementReason,
    StockMovementSource,
)
from app.products.service import StockAdjustment, StockService

MAX_LINES = 100
MAX_QUANTITY = 1_000_000


@dataclass(frozen=True)
class LineInput:
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    quantity: int
    unit_cost_paisa: int
    update_cost: bool = True


@dataclass(frozen=True)
class ReceiveLine:
    line_id: uuid.UUID
    accepted: int
    rejected: int = 0
    reject_reason: str | None = None


@dataclass(frozen=True)
class TransferLine:
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    quantity: int


def payable_state(po: PurchaseOrder, now: datetime | None = None) -> dict[str, Any]:
    """What the shop owes the supplier on one purchase order."""
    owed, paid = po.received_value_paisa, po.paid_paisa
    if owed == 0 and paid == 0:
        status = "NOTHING_DUE"
    elif paid >= owed:
        status = "PAID"
    elif paid == 0:
        status = "UNPAID"
    else:
        status = "PARTIALLY_PAID"
    due = po.payment_due_at
    overdue = bool(due and paid < owed and ensure_utc(due) < (now or utc_now()))
    return {
        "status": status,
        "owed_paisa": owed,
        "paid_paisa": paid,
        "balance_paisa": max(owed - paid, 0),
        "advance_paisa": max(paid - owed, 0),
        "due_at": due,
        "overdue": overdue,
    }


class ProcurementService:
    def __init__(self, db: AsyncSession, actor_id: uuid.UUID | None) -> None:
        self.db = db
        self.actor_id = actor_id
        self.stock = StockService(db)

    # ------------------------------------------------------------ helpers ---

    async def _item(
        self, product_id: uuid.UUID, variant_id: uuid.UUID | None
    ) -> tuple[Product, ProductVariant | None]:
        product = await self.db.scalar(
            sa.select(Product).where(Product.id == product_id, Product.archived_at.is_(None))
        )
        if product is None:
            raise NotFoundError("Product not found", details={"product_id": str(product_id)})
        if not product.stock_tracking_enabled:
            raise ValidationError(
                "This product does not track stock",
                details={"code": "STOCK_NOT_TRACKED", "product_id": str(product_id)},
            )
        if product.has_variants:
            if variant_id is None:
                raise ValidationError(
                    "Choose a variant",
                    details={"code": "VARIANT_REQUIRED", "product_id": str(product_id)},
                )
            variant = await self.db.scalar(
                sa.select(ProductVariant).where(
                    ProductVariant.id == variant_id, ProductVariant.product_id == product.id
                )
            )
            if variant is None:
                raise NotFoundError("Variant not found")
            return product, variant
        if variant_id is not None:
            raise ValidationError("This product has no variants")
        return product, None

    async def _supplier(self, supplier_id: uuid.UUID, *, active: bool = False) -> Supplier:
        row = await self.db.scalar(sa.select(Supplier).where(Supplier.id == supplier_id))
        if row is None:
            raise NotFoundError("Supplier not found")
        if active and not row.is_active:
            raise ConflictError("This supplier is inactive", details={"code": "SUPPLIER_INACTIVE"})
        return row

    async def po(self, po_id: uuid.UUID, *, lock: bool = False) -> PurchaseOrder:
        query = sa.select(PurchaseOrder).where(PurchaseOrder.id == po_id)
        row = await self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFoundError("Purchase order not found")
        return row

    async def lines(self, po_id: uuid.UUID) -> list[PurchaseOrderLine]:
        return list(
            (
                await self.db.scalars(
                    sa.select(PurchaseOrderLine)
                    .where(PurchaseOrderLine.purchase_order_id == po_id)
                    .order_by(PurchaseOrderLine.created_at, PurchaseOrderLine.id)
                )
            ).all()
        )

    async def _audit(
        self, action: AuditAction, entity: str, entity_id: Any, **context: Any
    ) -> None:
        await record_audit(
            self.db, action, entity_type=entity, entity_id=entity_id, context=context or None
        )

    async def _next_number(self, model: Any, prefix: str) -> str:
        # Callers hold the shop lock, so the count cannot race.
        count = await self.db.scalar(sa.select(sa.func.count()).select_from(model)) or 0
        return f"{prefix}-{count + 1:05d}"

    # ---------------------------------------------------------- suppliers ---

    async def save_supplier(
        self, values: dict[str, Any], supplier_id: uuid.UUID | None = None
    ) -> Supplier:
        await lock_shop(self.db)
        name = values["name"].strip()
        key = " ".join(name.lower().split())
        clash = await self.db.scalar(
            sa.select(Supplier.id).where(Supplier.name_key == key, Supplier.id != supplier_id)
        )
        if clash is not None:
            raise ConflictError(
                "A supplier with this name exists", details={"code": "SUPPLIER_EXISTS"}
            )
        row = await self._supplier(supplier_id) if supplier_id else None
        if row is None:
            row = Supplier(created_by=self.actor_id, is_active=True)
            self.db.add(row)
        row.name, row.name_key = name, key
        for field in (
            "contact_name",
            "phone",
            "email",
            "address",
            "notes",
            "payment_terms_days",
            "lead_time_days",
        ):
            if field in values:
                setattr(row, field, values[field])
        if "is_active" in values and values["is_active"] is not None:
            row.is_active = bool(values["is_active"])
        await self.db.flush()
        await self._audit(AuditAction.SUPPLIER_SAVED, "supplier", row.id, active=row.is_active)
        return row

    async def save_supplier_item(
        self,
        supplier_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None,
        *,
        supplier_sku: str | None,
        unit_cost_paisa: int | None,
        is_preferred: bool,
    ) -> SupplierItem:
        await lock_shop(self.db)
        await self._supplier(supplier_id)
        await self._item(product_id, variant_id)
        key = item_key(product_id, variant_id)
        row = await self.db.scalar(
            sa.select(SupplierItem).where(
                SupplierItem.supplier_id == supplier_id, SupplierItem.item_key == key
            )
        )
        if row is None:
            row = SupplierItem(
                supplier_id=supplier_id, product_id=product_id, variant_id=variant_id, item_key=key
            )
            self.db.add(row)
        row.supplier_sku = supplier_sku
        if unit_cost_paisa is not None:
            row.last_unit_cost_paisa = unit_cost_paisa
        if is_preferred:
            # One preferred supplier per item: it is who a draft PO goes to.
            for other in (
                await self.db.scalars(
                    sa.select(SupplierItem).where(
                        SupplierItem.item_key == key,
                        SupplierItem.supplier_id != supplier_id,
                        SupplierItem.is_preferred.is_(True),
                    )
                )
            ).all():
                other.is_preferred = False
        row.is_preferred = is_preferred
        await self.db.flush()
        return row

    # --------------------------------------------------------- warehouses ---

    async def default_warehouse(self) -> Warehouse:
        row = await self.db.scalar(sa.select(Warehouse).where(Warehouse.is_default.is_(True)))
        if row is None:
            row = Warehouse(name="Main", code="MAIN", is_default=True, is_active=True)
            self.db.add(row)
            await self.db.flush()
        return row

    async def warehouse(self, warehouse_id: uuid.UUID | None) -> Warehouse:
        if warehouse_id is None:
            return await self.default_warehouse()
        row = await self.db.scalar(sa.select(Warehouse).where(Warehouse.id == warehouse_id))
        if row is None:
            raise NotFoundError("Location not found")
        return row

    async def save_warehouse(
        self, values: dict[str, Any], warehouse_id: uuid.UUID | None = None
    ) -> Warehouse:
        await lock_shop(self.db)
        await self.default_warehouse()
        code = values["code"].strip().upper()
        clash = await self.db.scalar(
            sa.select(Warehouse.id).where(Warehouse.code == code, Warehouse.id != warehouse_id)
        )
        if clash is not None:
            raise ConflictError(
                "A location with this code exists", details={"code": "LOCATION_EXISTS"}
            )
        row = await self.warehouse(warehouse_id) if warehouse_id else None
        if row is None:
            row = Warehouse(is_default=False, is_active=True)
            self.db.add(row)
        row.name, row.code, row.address = values["name"].strip(), code, values.get("address")
        if values.get("is_active") is False:
            if row.is_default:
                raise ConflictError(
                    "The main location cannot be closed", details={"code": "DEFAULT_LOCATION"}
                )
            held = await self.db.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(WarehouseStock.quantity), 0)).where(
                    WarehouseStock.warehouse_id == row.id
                )
            )
            if held:
                raise ConflictError(
                    "Move this location's stock out before closing it",
                    details={"code": "LOCATION_NOT_EMPTY", "units": int(held)},
                )
            row.is_active = False
        elif values.get("is_active") is True:
            row.is_active = True
        await self.db.flush()
        await self._audit(AuditAction.WAREHOUSE_SAVED, "warehouse", row.id, code=row.code)
        return row

    async def _allocated(self, key: str) -> int:
        """Units of an item held at non-default locations."""
        return int(
            await self.db.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(WarehouseStock.quantity), 0)).where(
                    WarehouseStock.item_key == key
                )
            )
            or 0
        )

    async def _location_row(
        self, warehouse: Warehouse, product_id: uuid.UUID, variant_id: uuid.UUID | None
    ) -> WarehouseStock:
        key = item_key(product_id, variant_id)
        row = await self.db.scalar(
            sa.select(WarehouseStock)
            .where(WarehouseStock.warehouse_id == warehouse.id, WarehouseStock.item_key == key)
            .with_for_update()
        )
        if row is None:
            row = WarehouseStock(
                warehouse_id=warehouse.id,
                product_id=product_id,
                variant_id=variant_id,
                item_key=key,
                quantity=0,
            )
            self.db.add(row)
            await self.db.flush()
        return row

    # ---------------------------------------------------- purchase orders ---

    async def _write_lines(self, po: PurchaseOrder, lines: list[LineInput]) -> None:
        if not 1 <= len(lines) <= MAX_LINES:
            raise ValidationError(f"A purchase order has 1 to {MAX_LINES} lines")
        seen: set[str] = set()
        total = 0
        for line in lines:
            product, variant = await self._item(line.product_id, line.variant_id)
            key = item_key(product.id, variant.id if variant else None)
            if key in seen:
                raise ValidationError(
                    "Each item appears once per purchase order", details={"code": "DUPLICATE_LINE"}
                )
            seen.add(key)
            if not 0 < line.quantity <= MAX_QUANTITY or line.unit_cost_paisa < 0:
                raise ValidationError("Quantities are positive and costs are not negative")
            self.db.add(
                PurchaseOrderLine(
                    purchase_order_id=po.id,
                    product_id=product.id,
                    variant_id=variant.id if variant else None,
                    item_key=key,
                    description=(f"{product.name} ({variant.name})" if variant else product.name)[
                        :240
                    ],
                    quantity_ordered=line.quantity,
                    quantity_received=0,
                    quantity_rejected=0,
                    unit_cost_paisa=line.unit_cost_paisa,
                    update_cost=line.update_cost,
                )
            )
            total += line.quantity * line.unit_cost_paisa
        po.total_paisa = total

    async def create_po(
        self,
        *,
        supplier_id: uuid.UUID,
        lines: list[LineInput],
        warehouse_id: uuid.UUID | None = None,
        expected_at: datetime | None = None,
        payment_due_at: datetime | None = None,
        reference: str | None = None,
        notes: str | None = None,
        source: str = "SELLER",
    ) -> PurchaseOrder:
        await lock_shop(self.db)
        await self._supplier(supplier_id, active=True)
        if warehouse_id is not None:
            warehouse = await self.warehouse(warehouse_id)
            if not warehouse.is_active:
                raise ConflictError("This location is closed", details={"code": "LOCATION_CLOSED"})
        po = PurchaseOrder(
            number=await self._next_number(PurchaseOrder, "PO"),
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            status="DRAFT",
            expected_at=expected_at,
            payment_due_at=payment_due_at,
            reference=reference,
            notes=notes,
            total_paisa=0,
            received_value_paisa=0,
            paid_paisa=0,
            source=source,
            created_by=self.actor_id,
            version=1,
        )
        self.db.add(po)
        await self.db.flush()
        await self._write_lines(po, lines)
        await self.db.flush()
        await self._audit(
            AuditAction.PURCHASE_ORDER_SAVED,
            "purchase_order",
            po.id,
            number=po.number,
            source=source,
        )
        return po

    async def update_po(
        self,
        po_id: uuid.UUID,
        *,
        version: int,
        supplier_id: uuid.UUID,
        lines: list[LineInput],
        warehouse_id: uuid.UUID | None,
        expected_at: datetime | None,
        payment_due_at: datetime | None,
        reference: str | None,
        notes: str | None,
    ) -> PurchaseOrder:
        await lock_shop(self.db)
        po = await self.po(po_id, lock=True)
        if po.version != version:
            raise ConflictError("This purchase order changed; reload it", details={"code": "STALE"})
        if po.status != "DRAFT":
            # After ordering, the supplier has it: only dates and notes change.
            po.expected_at, po.payment_due_at = expected_at, payment_due_at
            po.reference, po.notes = reference, notes
        else:
            await self._supplier(supplier_id, active=True)
            po.supplier_id, po.warehouse_id = supplier_id, warehouse_id
            po.expected_at, po.payment_due_at = expected_at, payment_due_at
            po.reference, po.notes = reference, notes
            for old_line in await self.lines(po.id):
                await self.db.delete(old_line)
            await self.db.flush()
            await self._write_lines(po, lines)
        po.version += 1
        await self.db.flush()
        await self._audit(
            AuditAction.PURCHASE_ORDER_SAVED, "purchase_order", po.id, number=po.number
        )
        return po

    async def order_po(self, po_id: uuid.UUID) -> PurchaseOrder:
        """Mark a draft as sent to the supplier. ecomsbd itself sends nothing."""
        await lock_shop(self.db)
        po = await self.po(po_id, lock=True)
        if po.status == "ORDERED":
            return po
        if po.status != "DRAFT":
            raise ConflictError("Only a draft can be ordered", details={"code": "PO_NOT_DRAFT"})
        await self._supplier(po.supplier_id, active=True)
        po.status, po.ordered_at, po.ordered_by = "ORDERED", utc_now(), self.actor_id
        po.version += 1
        await self.db.flush()
        await enqueue(
            self.db, OutboxTopic.PURCHASE_ORDER_ORDERED, {"purchase_order_id": str(po.id)}
        )
        await self._audit(
            AuditAction.PURCHASE_ORDER_ORDERED, "purchase_order", po.id, number=po.number
        )
        return po

    async def cancel_po(self, po_id: uuid.UUID, reason: str) -> PurchaseOrder:
        """Nothing more will arrive. What was already received stays received and owed."""
        await lock_shop(self.db)
        po = await self.po(po_id, lock=True)
        if po.status in {"RECEIVED", "CANCELLED"}:
            raise ConflictError("This purchase order is closed", details={"code": "PO_CLOSED"})
        po.status, po.cancelled_at = "CANCELLED", utc_now()
        po.version += 1
        await self.db.flush()
        await self._audit(
            AuditAction.PURCHASE_ORDER_CANCELLED, "purchase_order", po.id, reason=reason[:300]
        )
        return po

    # ---------------------------------------------------------- receiving ---

    async def receive(
        self,
        po_id: uuid.UUID,
        *,
        lines: list[ReceiveLine],
        idempotency_key: str,
        received_at: datetime | None = None,
        supplier_reference: str | None = None,
        note: str | None = None,
        over_receipt_reason: str | None = None,
    ) -> tuple[GoodsReceipt, bool]:
        """Accept (and reject) delivered units. Returns (receipt, replayed).

        Each accepted line is one RESTOCK movement into the purchase order's
        location. Rejected units are recorded on the receipt and never enter
        stock. Receiving more than was ordered needs ``over_receipt_reason``;
        the API only passes one for people who manage procurement, and it is
        audited.
        """
        await lock_shop(self.db)
        digest = request_hash(
            {
                "po": str(po_id),
                "lines": sorted(
                    (str(x.line_id), x.accepted, x.rejected, x.reject_reason or "") for x in lines
                ),
                "ref": supplier_reference or "",
                "over": bool(over_receipt_reason),
            }
        )
        existing = await self.db.scalar(
            sa.select(GoodsReceipt).where(GoodsReceipt.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.request_hash != digest:
                raise IdempotencyConflictError()
            return existing, True
        po = await self.po(po_id, lock=True)
        if po.status not in OPEN_PO:
            raise ConflictError(
                "Only an ordered purchase order can be received",
                details={"code": "PO_NOT_OPEN", "status": po.status},
            )
        by_id = {line.id: line for line in await self.lines(po.id)}
        if not lines or len({x.line_id for x in lines}) != len(lines):
            raise ValidationError("List each purchase order line once")
        over: list[str] = []
        for entry in lines:
            line = by_id.get(entry.line_id)
            if line is None:
                raise NotFoundError("Line not on this purchase order")
            if entry.accepted < 0 or entry.rejected < 0 or entry.accepted + entry.rejected == 0:
                raise ValidationError("Receive or reject at least one unit per listed line")
            if entry.rejected and not entry.reject_reason:
                raise ValidationError(
                    "Say why units were rejected", details={"code": "REJECT_REASON_REQUIRED"}
                )
            if line.quantity_received + entry.accepted > line.quantity_ordered:
                over.append(str(line.id))
        if over and not (over_receipt_reason and over_receipt_reason.strip()):
            raise ConflictError(
                "More than was ordered: an owner or manager must allow it with a reason",
                details={"code": "OVER_RECEIPT", "lines": over},
            )
        warehouse = await self.warehouse(po.warehouse_id) if po.warehouse_id else None
        now = utc_now()
        at = received_at or now
        receipt = GoodsReceipt(
            purchase_order_id=po.id,
            warehouse_id=po.warehouse_id,
            received_at=at,
            supplier_reference=supplier_reference,
            note=note,
            actor_id=self.actor_id,
            idempotency_key=idempotency_key,
            request_hash=digest,
            accepted_units=0,
            rejected_units=0,
            value_paisa=0,
            over_receipt_reason=(over_receipt_reason or "").strip()[:300] if over else None,
        )
        self.db.add(receipt)
        await self.db.flush()
        supplier = await self._supplier(po.supplier_id)
        for entry in lines:
            line = by_id[entry.line_id]
            movement_id = None
            if entry.accepted:
                movement = await self.stock.record_movement(
                    StockAdjustment(
                        product_id=line.product_id,
                        variant_id=line.variant_id,
                        quantity_delta=entry.accepted,
                        reason=StockMovementReason.RESTOCK,
                        source=StockMovementSource.PURCHASE,
                        reference=po.number,
                        note=f"Received {po.number}"[:400],
                        unit_cost_paisa=line.unit_cost_paisa,
                        occurred_at=at,
                        warehouse_id=po.warehouse_id,
                        idempotency_key=f"po-receipt:{receipt.id}:{line.id}",
                    )
                )
                movement_id = movement.id
                if warehouse is not None and not warehouse.is_default:
                    location = await self._location_row(warehouse, line.product_id, line.variant_id)
                    location.quantity += entry.accepted
                await self._record_cost(supplier, line, at)
            line.quantity_received += entry.accepted
            line.quantity_rejected += entry.rejected
            receipt.accepted_units += entry.accepted
            receipt.rejected_units += entry.rejected
            receipt.value_paisa += entry.accepted * line.unit_cost_paisa
            self.db.add(
                GoodsReceiptLine(
                    receipt_id=receipt.id,
                    line_id=line.id,
                    quantity_accepted=entry.accepted,
                    quantity_rejected=entry.rejected,
                    reject_reason=entry.reject_reason if entry.rejected else None,
                    movement_id=movement_id,
                )
            )
        po.received_value_paisa += receipt.value_paisa
        if receipt.accepted_units and po.first_received_at is None:
            po.first_received_at = at
            if po.payment_due_at is None and supplier.payment_terms_days is not None:
                po.payment_due_at = at + timedelta(days=supplier.payment_terms_days)
        complete = all(line.quantity_received >= line.quantity_ordered for line in by_id.values())
        po.status = "RECEIVED" if complete else "PARTIALLY_RECEIVED"
        if complete:
            po.received_at = at
        po.version += 1
        await self.db.flush()
        await enqueue(
            self.db,
            OutboxTopic.PURCHASE_ORDER_RECEIVED,
            {
                "purchase_order_id": str(po.id),
                "receipt_id": str(receipt.id),
                "complete": complete,
            },
        )
        await self._audit(
            AuditAction.GOODS_OVER_RECEIVED if over else AuditAction.GOODS_RECEIVED,
            "purchase_order",
            po.id,
            receipt_id=str(receipt.id),
            accepted=receipt.accepted_units,
            rejected=receipt.rejected_units,
            over_lines=over or None,
            reason=receipt.over_receipt_reason,
        )
        return receipt, False

    async def _record_cost(self, supplier: Supplier, line: PurchaseOrderLine, at: datetime) -> None:
        """The documented cost rule: the latest received cost becomes current."""
        if line.update_cost:
            target: Product | ProductVariant | None = (
                await self.db.get(ProductVariant, line.variant_id)
                if line.variant_id is not None
                else await self.db.get(Product, line.product_id)
            )
            if target is not None:
                target.cost_paisa = line.unit_cost_paisa
        link = await self.db.scalar(
            sa.select(SupplierItem).where(
                SupplierItem.supplier_id == supplier.id, SupplierItem.item_key == line.item_key
            )
        )
        if link is None:
            link = SupplierItem(
                supplier_id=supplier.id,
                product_id=line.product_id,
                variant_id=line.variant_id,
                item_key=line.item_key,
                is_preferred=False,
            )
            self.db.add(link)
        link.last_unit_cost_paisa, link.last_received_at = line.unit_cost_paisa, at

    # ----------------------------------------------------------- payables ---

    async def record_payment(
        self,
        po_id: uuid.UUID,
        *,
        amount_paisa: int,
        method: str,
        idempotency_key: str,
        paid_at: datetime | None = None,
        reference: str | None = None,
        note: str | None = None,
    ) -> tuple[SupplierPayment, bool]:
        await lock_shop(self.db)
        existing = await self.db.scalar(
            sa.select(SupplierPayment).where(SupplierPayment.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.purchase_order_id != po_id or existing.amount_paisa != amount_paisa:
                raise IdempotencyConflictError()
            return existing, True
        po = await self.po(po_id, lock=True)
        if po.status == "DRAFT":
            raise ConflictError("Order it before paying for it", details={"code": "PO_NOT_ORDERED"})
        if amount_paisa <= 0:
            raise ValidationError("A payment is more than zero")
        # An advance is fine up to what was ordered; never beyond what is owed
        # once nothing more can arrive.
        ceiling = (
            po.received_value_paisa
            if po.status in {"RECEIVED", "CANCELLED"}
            else max(po.total_paisa, po.received_value_paisa)
        )
        if po.paid_paisa + amount_paisa > ceiling:
            raise ConflictError(
                "That pays more than this purchase order is worth",
                details={"code": "OVERPAYMENT", "remaining_paisa": max(ceiling - po.paid_paisa, 0)},
            )
        payment = SupplierPayment(
            purchase_order_id=po.id,
            supplier_id=po.supplier_id,
            amount_paisa=amount_paisa,
            paid_at=paid_at or utc_now(),
            method=method,
            reference=reference,
            note=note,
            actor_id=self.actor_id,
            idempotency_key=idempotency_key,
        )
        self.db.add(payment)
        po.paid_paisa += amount_paisa
        po.version += 1
        await self.db.flush()
        await self._audit(
            AuditAction.SUPPLIER_PAYMENT_RECORDED,
            "purchase_order",
            po.id,
            amount_paisa=amount_paisa,
            method=method,
        )
        return payment, False

    # ---------------------------------------------------------- transfers ---

    async def transfer(
        self,
        *,
        from_warehouse_id: uuid.UUID | None,
        to_warehouse_id: uuid.UUID | None,
        lines: list[TransferLine],
        idempotency_key: str,
        reference: str | None = None,
        note: str | None = None,
    ) -> tuple[StockTransfer, bool]:
        await lock_shop(self.db)
        digest = request_hash(
            {
                "from": str(from_warehouse_id),
                "to": str(to_warehouse_id),
                "lines": sorted((str(x.product_id), str(x.variant_id), x.quantity) for x in lines),
            }
        )
        existing = await self.db.scalar(
            sa.select(StockTransfer).where(StockTransfer.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.request_hash != digest:
                raise IdempotencyConflictError()
            return existing, True
        source = await self.warehouse(from_warehouse_id)
        target = await self.warehouse(to_warehouse_id)
        if source.id == target.id:
            raise ValidationError("Choose two different locations")
        if not target.is_active:
            raise ConflictError("The destination is closed", details={"code": "LOCATION_CLOSED"})
        if not 1 <= len(lines) <= MAX_LINES:
            raise ValidationError(f"A transfer has 1 to {MAX_LINES} lines")
        transfer = StockTransfer(
            number=await self._next_number(StockTransfer, "TR"),
            from_warehouse_id=source.id,
            to_warehouse_id=target.id,
            status="COMPLETED",
            reference=reference,
            note=note,
            actor_id=self.actor_id,
            idempotency_key=idempotency_key,
            request_hash=digest,
            completed_at=utc_now(),
        )
        self.db.add(transfer)
        await self.db.flush()
        seen: set[str] = set()
        for entry in lines:
            product, variant = await self._item(entry.product_id, entry.variant_id)
            variant_id = variant.id if variant else None
            key = item_key(product.id, variant_id)
            if key in seen or not 0 < entry.quantity <= MAX_QUANTITY:
                raise ValidationError("Each item once, with a positive quantity")
            seen.add(key)
            on_hand = variant.stock_on_hand if variant else product.stock_on_hand
            if source.is_default:
                available = on_hand - await self._allocated(key)
            else:
                available = (await self._location_row(source, product.id, variant_id)).quantity
            if available < entry.quantity:
                raise ConflictError(
                    f"Only {max(available, 0)} of {product.name} at {source.name}",
                    details={
                        "code": "INSUFFICIENT_LOCATION_STOCK",
                        "product_id": str(product.id),
                        "available": max(available, 0),
                    },
                )
            if not source.is_default:
                (
                    await self._location_row(source, product.id, variant_id)
                ).quantity -= entry.quantity
            if not target.is_default:
                (
                    await self._location_row(target, product.id, variant_id)
                ).quantity += entry.quantity
            await self.stock.record_transfer(
                product_id=product.id,
                variant_id=variant_id,
                quantity=entry.quantity,
                from_warehouse_id=None if source.is_default else source.id,
                to_warehouse_id=None if target.is_default else target.id,
                key=f"transfer:{transfer.id}:{key}",
                reference=transfer.number,
                note=note,
            )
            self.db.add(
                StockTransferLine(
                    transfer_id=transfer.id,
                    product_id=product.id,
                    variant_id=variant_id,
                    item_key=key,
                    quantity=entry.quantity,
                )
            )
        await self.db.flush()
        await enqueue(
            self.db, OutboxTopic.STOCK_TRANSFER_COMPLETED, {"transfer_id": str(transfer.id)}
        )
        await self._audit(
            AuditAction.STOCK_TRANSFERRED,
            "stock_transfer",
            transfer.id,
            number=transfer.number,
            lines=len(lines),
        )
        return transfer, False

    # ------------------------------------------------------ stock position ---

    async def incoming(self, keys: list[str]) -> dict[str, int]:
        """Units still expected on open purchase orders, per item."""
        if not keys:
            return {}
        remaining = sa.func.greatest if self._pg else sa.func.max
        rows = await self.db.execute(
            sa.select(
                PurchaseOrderLine.item_key,
                sa.func.sum(
                    remaining(
                        PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received, 0
                    )
                ),
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(PurchaseOrder.status.in_(OPEN_PO), PurchaseOrderLine.item_key.in_(keys))
            .group_by(PurchaseOrderLine.item_key)
        )
        return {key: int(value or 0) for key, value in rows.all()}

    @property
    def _pg(self) -> bool:
        return self.db.bind is not None and self.db.bind.dialect.name == "postgresql"

    async def position(
        self, items: list[tuple[Product, ProductVariant | None]]
    ) -> list[dict[str, Any]]:
        """On hand, low, incoming, per location and recent damage for a page of items.

        Reserved / available-to-sell is deliberately absent: orders deduct stock
        when a parcel is booked, so there is no reliable reservation to show.
        """
        keys = [item_key(p.id, v.id if v else None) for p, v in items]
        incoming = await self.incoming(keys)
        warehouses = {row.id: row for row in (await self.db.scalars(sa.select(Warehouse))).all()}
        located: dict[str, dict[uuid.UUID, int]] = {}
        if keys:
            for key, warehouse_id, quantity in (
                await self.db.execute(
                    sa.select(
                        WarehouseStock.item_key,
                        WarehouseStock.warehouse_id,
                        WarehouseStock.quantity,
                    ).where(WarehouseStock.item_key.in_(keys), WarehouseStock.quantity > 0)
                )
            ).all():
                located.setdefault(key, {})[warehouse_id] = int(quantity)
        since = utc_now() - timedelta(days=30)
        damaged: dict[str, int] = {}
        rejected: dict[str, int] = {}
        if keys:
            product_ids = list({p.id for p, _ in items})
            for product_id, variant_id, units in (
                await self.db.execute(
                    sa.select(
                        StockMovement.product_id,
                        StockMovement.variant_id,
                        sa.func.sum(-StockMovement.quantity_delta),
                    )
                    .where(
                        StockMovement.product_id.in_(product_ids),
                        StockMovement.reason == StockMovementReason.DAMAGED_WRITE_OFF,
                        StockMovement.occurred_at >= since,
                    )
                    .group_by(StockMovement.product_id, StockMovement.variant_id)
                )
            ).all():
                damaged[item_key(product_id, variant_id)] = int(units or 0)
            for key, units in (
                await self.db.execute(
                    sa.select(
                        PurchaseOrderLine.item_key, sa.func.sum(GoodsReceiptLine.quantity_rejected)
                    )
                    .join(PurchaseOrderLine, PurchaseOrderLine.id == GoodsReceiptLine.line_id)
                    .where(
                        PurchaseOrderLine.item_key.in_(keys),
                        GoodsReceiptLine.created_at >= since,
                        GoodsReceiptLine.quantity_rejected > 0,
                    )
                    .group_by(PurchaseOrderLine.item_key)
                )
            ).all():
                rejected[key] = int(units or 0)
        default = next((w for w in warehouses.values() if w.is_default), None)
        result = []
        for product, variant in items:
            key = item_key(product.id, variant.id if variant else None)
            on_hand = variant.stock_on_hand if variant else product.stock_on_hand
            threshold = variant.low_stock_threshold if variant else product.low_stock_threshold
            elsewhere = located.get(key, {})
            locations = [
                {
                    "warehouse_id": default.id if default else None,
                    "name": default.name if default else "Main",
                    "quantity": on_hand - sum(elsewhere.values()),
                    "is_default": True,
                }
            ] + [
                {
                    "warehouse_id": warehouse_id,
                    "name": warehouses[warehouse_id].name if warehouse_id in warehouses else "",
                    "quantity": quantity,
                    "is_default": False,
                }
                for warehouse_id, quantity in elsewhere.items()
            ]
            result.append(
                {
                    "product_id": product.id,
                    "variant_id": variant.id if variant else None,
                    "name": product.name,
                    "variant_name": variant.name if variant else None,
                    "sku": (variant.sku if variant else product.sku),
                    "on_hand": on_hand,
                    "low_stock_threshold": threshold,
                    "low": threshold is not None and on_hand <= threshold,
                    "incoming": incoming.get(key, 0),
                    "locations": locations,
                    "damaged_30d": damaged.get(key, 0),
                    "rejected_on_receipt_30d": rejected.get(key, 0),
                    "cost_paisa": variant.cost_paisa
                    if variant and variant.cost_paisa is not None
                    else product.cost_paisa,
                }
            )
        return result

    # ------------------------------------------------------- automation ---

    async def draft_for_item(
        self,
        product_id: uuid.UUID,
        variant_id: uuid.UUID | None,
        quantity: int,
        *,
        source: str = "AUTOMATION",
    ) -> tuple[PurchaseOrder | None, str | None]:
        """A DRAFT purchase order to the item's preferred supplier, or why not.

        Deterministic: the one preferred supplier, its last cost (else the item's
        cost), the given quantity. Never ordered: a person reviews and orders it.
        """
        key = item_key(product_id, variant_id)
        link = await self.db.scalar(
            sa.select(SupplierItem)
            .join(Supplier, Supplier.id == SupplierItem.supplier_id)
            .where(
                SupplierItem.item_key == key,
                SupplierItem.is_preferred.is_(True),
                Supplier.is_active.is_(True),
            )
        )
        if link is None:
            return None, "NO_PREFERRED_SUPPLIER"
        pending = await self.db.scalar(
            sa.select(PurchaseOrderLine.id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(
                PurchaseOrderLine.item_key == key,
                PurchaseOrder.status.in_(("DRAFT", *OPEN_PO)),
            )
            .limit(1)
        )
        if pending is not None:
            return None, "ALREADY_ON_ORDER"
        product, variant = await self._item(product_id, variant_id)
        cost = link.last_unit_cost_paisa
        if cost is None:
            cost = (
                variant.cost_paisa
                if variant and variant.cost_paisa is not None
                else product.cost_paisa
            )
        po = await self.create_po(
            supplier_id=link.supplier_id,
            lines=[LineInput(product_id, variant_id, quantity, cost or 0)],
            notes=(
                "Prepared by an automation workflow. Review before ordering."
                if source == "AUTOMATION"
                else "Prepared from a reorder suggestion. Review before ordering."
            ),
            source=source,
        )
        return po, None
