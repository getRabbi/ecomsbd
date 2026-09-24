"""Suppliers, purchase orders, receiving, payables, locations and transfers (V3.5).

Who may do what, enforced here:

* read suppliers, purchase orders, stock and locations: ``product.view``;
* build, order and cancel purchase orders, manage suppliers and locations,
  approve an over-receipt: ``procurement.manage`` (owner, manager);
* receive goods and transfer stock: ``inventory.adjust``;
* see what is owed to suppliers: ``money.view``; record a supplier payment:
  ``payable.manage`` (owner, finance).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Principal, require_permission
from app.core.clock import utc_now
from app.core.errors import ForbiddenError
from app.procurement.models import (
    OPEN_PO,
    PAYMENT_METHODS,
    REJECT_REASONS,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    StockTransfer,
    StockTransferLine,
    Supplier,
    SupplierItem,
    SupplierPayment,
    Warehouse,
)
from app.procurement.service import (
    LineInput,
    ProcurementService,
    ReceiveLine,
    TransferLine,
    payable_state,
)
from app.products.models import Product, ProductVariant
from app.tenants.roles import Permission

router = APIRouter(prefix="/procurement", tags=["procurement"])
Viewer = Annotated[Principal, Depends(require_permission(Permission.PRODUCT_VIEW))]
Manager = Annotated[Principal, Depends(require_permission(Permission.PROCUREMENT_MANAGE))]
Receiver = Annotated[Principal, Depends(require_permission(Permission.INVENTORY_ADJUST))]
MoneyViewer = Annotated[Principal, Depends(require_permission(Permission.MONEY_VIEW))]
Payer = Annotated[Principal, Depends(require_permission(Permission.PAYABLE_MANAGE))]
PAGE = 50


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SupplierInput(Input):
    name: str = Field(min_length=1, max_length=160)
    contact_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(
        default=None, max_length=254, pattern=r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$"
    )
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None


class SupplierItemInput(Input):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    supplier_sku: str | None = Field(default=None, max_length=64)
    unit_cost_paisa: int | None = Field(default=None, ge=0, le=10**12)
    is_preferred: bool = False


class WarehouseInput(Input):
    name: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")
    address: str | None = Field(default=None, max_length=300)
    is_active: bool | None = None


class Line(Input):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: int = Field(ge=1, le=1_000_000)
    unit_cost_paisa: int = Field(ge=0, le=10**12)
    update_cost: bool = True


class PurchaseOrderInput(Input):
    supplier_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    lines: list[Line] = Field(min_length=1, max_length=100)
    expected_at: AwareDatetime | None = None
    payment_due_at: AwareDatetime | None = None
    reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class PurchaseOrderUpdate(PurchaseOrderInput):
    version: int


class CancelInput(Input):
    reason: str = Field(min_length=3, max_length=300)


class ReceiveLineInput(Input):
    line_id: uuid.UUID
    accepted: int = Field(ge=0, le=1_000_000)
    rejected: int = Field(default=0, ge=0, le=1_000_000)
    reject_reason: Literal["DAMAGED", "WRONG_ITEM", "EXPIRED", "OTHER"] | None = None


class ReceiveInput(Input):
    lines: list[ReceiveLineInput] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)
    received_at: AwareDatetime | None = None
    supplier_reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    #: Only for people who manage procurement; audited.
    over_receipt_reason: str | None = Field(default=None, min_length=3, max_length=300)


class PaymentInput(Input):
    amount_paisa: int = Field(ge=1, le=10**12)
    method: Literal["CASH", "BKASH", "NAGAD", "ROCKET", "BANK", "CHEQUE", "OTHER"]
    idempotency_key: str = Field(min_length=8, max_length=100)
    paid_at: AwareDatetime | None = None
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


class TransferLineInput(Input):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: int = Field(ge=1, le=1_000_000)


class TransferInput(Input):
    from_warehouse_id: uuid.UUID | None = None
    to_warehouse_id: uuid.UUID | None = None
    lines: list[TransferLineInput] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


def _svc(db: DbSession, actor: Principal) -> ProcurementService:
    return ProcurementService(db, actor.user_id)


def _view(row: object, fields: str) -> dict[str, Any]:
    return {key: getattr(row, key) for key in fields.split()}


SUPPLIER_FIELDS = (
    "id name contact_name phone email address notes payment_terms_days lead_time_days is_active "
    "created_at"
)
PO_FIELDS = (
    "id number supplier_id warehouse_id status expected_at reference notes total_paisa "
    "received_value_paisa ordered_at first_received_at received_at cancelled_at source "
    "version created_at"
)
LINE_FIELDS = (
    "id product_id variant_id description quantity_ordered quantity_received "
    "quantity_rejected unit_cost_paisa update_cost"
)


def po_view(po: PurchaseOrder, *, money: bool, supplier: str | None = None) -> dict[str, Any]:
    data = _view(po, PO_FIELDS)
    data["supplier_name"] = supplier
    data["payable"] = payable_state(po) if money else None
    return data


async def _supplier_names(db: DbSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return dict(
        (await db.execute(sa.select(Supplier.id, Supplier.name).where(Supplier.id.in_(ids))))
        .tuples()
        .all()
    )


# ---------------------------------------------------------------- suppliers ---


@router.get("/suppliers")
async def suppliers(
    db: DbSession,
    actor: Viewer,
    search: str | None = Query(None, max_length=80),
    active: bool | None = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(Supplier)
    if search:
        query = query.where(Supplier.name_key.contains(search.lower().strip(), autoescape=True))
    if active is not None:
        query = query.where(Supplier.is_active.is_(active))
    rows = (await db.scalars(query.order_by(Supplier.name_key).offset(offset).limit(PAGE))).all()
    ids = [row.id for row in rows]
    open_counts: dict[uuid.UUID, int] = {}
    balances: dict[uuid.UUID, int] = {}
    if ids:
        for supplier_id, count in (
            await db.execute(
                sa.select(PurchaseOrder.supplier_id, sa.func.count())
                .where(PurchaseOrder.supplier_id.in_(ids), PurchaseOrder.status.in_(OPEN_PO))
                .group_by(PurchaseOrder.supplier_id)
            )
        ).all():
            open_counts[supplier_id] = int(count)
        if actor.can(Permission.MONEY_VIEW):
            for supplier_id, owed, paid in (
                await db.execute(
                    sa.select(
                        PurchaseOrder.supplier_id,
                        sa.func.sum(PurchaseOrder.received_value_paisa),
                        sa.func.sum(PurchaseOrder.paid_paisa),
                    )
                    .where(PurchaseOrder.supplier_id.in_(ids))
                    .group_by(PurchaseOrder.supplier_id)
                )
            ).all():
                balances[supplier_id] = int(owed or 0) - int(paid or 0)
    return {
        "items": [
            {
                **_view(row, SUPPLIER_FIELDS),
                "open_orders": open_counts.get(row.id, 0),
                "balance_paisa": balances.get(row.id) if actor.can(Permission.MONEY_VIEW) else None,
            }
            for row in rows
        ],
        "can_manage": actor.can(Permission.PROCUREMENT_MANAGE),
    }


@router.post("/suppliers", status_code=201)
async def create_supplier(body: SupplierInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    row = await _svc(db, actor).save_supplier(body.model_dump())
    return _view(row, SUPPLIER_FIELDS)


@router.patch("/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: uuid.UUID, body: SupplierInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    row = await _svc(db, actor).save_supplier(body.model_dump(exclude_unset=True), supplier_id)
    return _view(row, SUPPLIER_FIELDS)


@router.get("/suppliers/{supplier_id}")
async def supplier_detail(supplier_id: uuid.UUID, db: DbSession, actor: Viewer) -> dict[str, Any]:
    svc = _svc(db, actor)
    row = await svc._supplier(supplier_id)
    items = (
        await db.execute(
            sa.select(SupplierItem, Product.name, ProductVariant.name)
            .join(Product, Product.id == SupplierItem.product_id)
            .outerjoin(ProductVariant, ProductVariant.id == SupplierItem.variant_id)
            .where(SupplierItem.supplier_id == supplier_id)
            .order_by(Product.name)
            .limit(500)
        )
    ).all()
    orders = (
        await db.scalars(
            sa.select(PurchaseOrder)
            .where(PurchaseOrder.supplier_id == supplier_id)
            .order_by(PurchaseOrder.created_at.desc())
            .limit(20)
        )
    ).all()
    money = actor.can(Permission.MONEY_VIEW)
    return {
        "supplier": _view(row, SUPPLIER_FIELDS),
        "items": [
            {
                **_view(
                    link,
                    "id product_id variant_id supplier_sku last_unit_cost_paisa "
                    "last_received_at is_preferred",
                ),
                "product_name": product_name,
                "variant_name": variant_name,
            }
            for link, product_name, variant_name in items
        ],
        "purchase_orders": [po_view(po, money=money, supplier=row.name) for po in orders],
        "can_manage": actor.can(Permission.PROCUREMENT_MANAGE),
    }


@router.put("/suppliers/{supplier_id}/items")
async def save_supplier_item(
    supplier_id: uuid.UUID, body: SupplierItemInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    row = await _svc(db, actor).save_supplier_item(
        supplier_id,
        body.product_id,
        body.variant_id,
        supplier_sku=body.supplier_sku,
        unit_cost_paisa=body.unit_cost_paisa,
        is_preferred=body.is_preferred,
    )
    return _view(row, "id product_id variant_id supplier_sku last_unit_cost_paisa is_preferred")


# --------------------------------------------------------------- locations ---


@router.get("/warehouses")
async def warehouses(db: DbSession, actor: Viewer) -> dict[str, Any]:
    rows = (
        await db.scalars(sa.select(Warehouse).order_by(Warehouse.is_default.desc(), Warehouse.name))
    ).all()
    items = [_view(row, "id name code address is_default is_active") for row in rows]
    if not any(row.is_default for row in rows):
        # Not written by a read: the main location appears on first use.
        items.insert(
            0,
            {
                "id": None,
                "name": "Main",
                "code": "MAIN",
                "address": None,
                "is_default": True,
                "is_active": True,
            },
        )
    return {"items": items, "can_manage": actor.can(Permission.PROCUREMENT_MANAGE)}


@router.post("/warehouses", status_code=201)
async def create_warehouse(body: WarehouseInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    row = await _svc(db, actor).save_warehouse(body.model_dump())
    return _view(row, "id name code address is_default is_active")


@router.patch("/warehouses/{warehouse_id}")
async def update_warehouse(
    warehouse_id: uuid.UUID, body: WarehouseInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    row = await _svc(db, actor).save_warehouse(body.model_dump(), warehouse_id)
    return _view(row, "id name code address is_default is_active")


# --------------------------------------------------------- purchase orders ---


def _lines(body: PurchaseOrderInput) -> list[LineInput]:
    return [
        LineInput(x.product_id, x.variant_id, x.quantity, x.unit_cost_paisa, x.update_cost)
        for x in body.lines
    ]


@router.get("/purchase-orders")
async def purchase_orders(
    db: DbSession,
    actor: Viewer,
    status: Literal["DRAFT", "ORDERED", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED", "OPEN"]
    | None = None,
    supplier_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(PurchaseOrder)
    if status == "OPEN":
        query = query.where(PurchaseOrder.status.in_(OPEN_PO))
    elif status:
        query = query.where(PurchaseOrder.status == status)
    if supplier_id:
        query = query.where(PurchaseOrder.supplier_id == supplier_id)
    rows = (
        await db.scalars(query.order_by(PurchaseOrder.created_at.desc()).offset(offset).limit(PAGE))
    ).all()
    names = await _supplier_names(db, {row.supplier_id for row in rows})
    money = actor.can(Permission.MONEY_VIEW)
    return {
        "items": [po_view(row, money=money, supplier=names.get(row.supplier_id)) for row in rows],
        "can_manage": actor.can(Permission.PROCUREMENT_MANAGE),
        "can_receive": actor.can(Permission.INVENTORY_ADJUST),
    }


@router.post("/purchase-orders", status_code=201)
async def create_po(body: PurchaseOrderInput, db: DbSession, actor: Manager) -> dict[str, Any]:
    po = await _svc(db, actor).create_po(
        supplier_id=body.supplier_id,
        lines=_lines(body),
        warehouse_id=body.warehouse_id,
        expected_at=body.expected_at,
        payment_due_at=body.payment_due_at,
        reference=body.reference,
        notes=body.notes,
    )
    return await _detail(db, actor, po)


@router.get("/purchase-orders/{po_id}")
async def po_detail(po_id: uuid.UUID, db: DbSession, actor: Viewer) -> dict[str, Any]:
    return await _detail(db, actor, await _svc(db, actor).po(po_id))


async def _detail(db: DbSession, actor: Principal, po: PurchaseOrder) -> dict[str, Any]:
    svc = _svc(db, actor)
    lines = await svc.lines(po.id)
    receipts = (
        await db.scalars(
            sa.select(GoodsReceipt)
            .where(GoodsReceipt.purchase_order_id == po.id)
            .order_by(GoodsReceipt.received_at.desc())
            .limit(100)
        )
    ).all()
    receipt_lines: dict[uuid.UUID, list[dict[str, Any]]] = {r.id: [] for r in receipts}
    if receipts:
        for row in (
            await db.scalars(
                sa.select(GoodsReceiptLine).where(
                    GoodsReceiptLine.receipt_id.in_([r.id for r in receipts])
                )
            )
        ).all():
            receipt_lines[row.receipt_id].append(
                _view(row, "line_id quantity_accepted quantity_rejected reject_reason movement_id")
            )
    money = actor.can(Permission.MONEY_VIEW)
    payments = (
        (
            await db.scalars(
                sa.select(SupplierPayment)
                .where(SupplierPayment.purchase_order_id == po.id)
                .order_by(SupplierPayment.paid_at.desc())
                .limit(100)
            )
        ).all()
        if money
        else []
    )
    names = await _supplier_names(db, {po.supplier_id})
    return {
        "purchase_order": po_view(po, money=money, supplier=names.get(po.supplier_id)),
        "lines": [
            {
                **_view(line, LINE_FIELDS),
                "remaining": max(line.quantity_ordered - line.quantity_received, 0),
            }
            for line in lines
        ],
        "receipts": [
            {
                **_view(
                    r,
                    "id received_at supplier_reference note accepted_units rejected_units "
                    "value_paisa over_receipt_reason actor_id",
                ),
                "lines": receipt_lines[r.id],
            }
            for r in receipts
        ],
        "payments": [
            _view(p, "id amount_paisa paid_at method reference note actor_id") for p in payments
        ],
        "can_manage": actor.can(Permission.PROCUREMENT_MANAGE),
        "can_receive": actor.can(Permission.INVENTORY_ADJUST),
        "can_pay": actor.can(Permission.PAYABLE_MANAGE),
    }


@router.put("/purchase-orders/{po_id}")
async def update_po(
    po_id: uuid.UUID, body: PurchaseOrderUpdate, db: DbSession, actor: Manager
) -> dict[str, Any]:
    po = await _svc(db, actor).update_po(
        po_id,
        version=body.version,
        supplier_id=body.supplier_id,
        lines=_lines(body),
        warehouse_id=body.warehouse_id,
        expected_at=body.expected_at,
        payment_due_at=body.payment_due_at,
        reference=body.reference,
        notes=body.notes,
    )
    return await _detail(db, actor, po)


@router.post("/purchase-orders/{po_id}/order")
async def order_po(po_id: uuid.UUID, db: DbSession, actor: Manager) -> dict[str, Any]:
    return await _detail(db, actor, await _svc(db, actor).order_po(po_id))


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_po(
    po_id: uuid.UUID, body: CancelInput, db: DbSession, actor: Manager
) -> dict[str, Any]:
    return await _detail(db, actor, await _svc(db, actor).cancel_po(po_id, body.reason))


@router.post("/purchase-orders/{po_id}/receive", status_code=201)
async def receive(
    po_id: uuid.UUID, body: ReceiveInput, db: DbSession, actor: Receiver
) -> dict[str, Any]:
    if body.over_receipt_reason and not actor.can(Permission.PROCUREMENT_MANAGE):
        raise ForbiddenError(
            "Only an owner or manager can accept more than was ordered",
            details={"code": "OVER_RECEIPT_NOT_ALLOWED"},
        )
    svc = _svc(db, actor)
    receipt, replayed = await svc.receive(
        po_id,
        lines=[ReceiveLine(x.line_id, x.accepted, x.rejected, x.reject_reason) for x in body.lines],
        idempotency_key=body.idempotency_key,
        received_at=body.received_at,
        supplier_reference=body.supplier_reference,
        note=body.note,
        over_receipt_reason=body.over_receipt_reason,
    )
    detail = await _detail(db, actor, await svc.po(receipt.purchase_order_id))
    return {"receipt_id": receipt.id, "replayed": replayed, **detail}


@router.post("/purchase-orders/{po_id}/payments", status_code=201)
async def pay(po_id: uuid.UUID, body: PaymentInput, db: DbSession, actor: Payer) -> dict[str, Any]:
    svc = _svc(db, actor)
    payment, replayed = await svc.record_payment(
        po_id,
        amount_paisa=body.amount_paisa,
        method=body.method,
        idempotency_key=body.idempotency_key,
        paid_at=body.paid_at,
        reference=body.reference,
        note=body.note,
    )
    po = await svc.po(po_id)
    return {"payment_id": payment.id, "replayed": replayed, "payable": payable_state(po)}


# ----------------------------------------------------------------- payables ---


@router.get("/payables")
async def payables(
    db: DbSession,
    actor: MoneyViewer,
    supplier_id: uuid.UUID | None = None,
    state: Literal["OPEN", "OVERDUE", "PAID", "ALL"] = "OPEN",
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    now = utc_now()
    query = sa.select(PurchaseOrder).where(PurchaseOrder.status != "DRAFT")
    if supplier_id:
        query = query.where(PurchaseOrder.supplier_id == supplier_id)
    owing = PurchaseOrder.paid_paisa < PurchaseOrder.received_value_paisa
    if state == "OPEN":
        query = query.where(owing)
    elif state == "OVERDUE":
        query = query.where(owing, PurchaseOrder.payment_due_at < now)
    elif state == "PAID":
        query = query.where(
            PurchaseOrder.received_value_paisa > 0,
            PurchaseOrder.paid_paisa >= PurchaseOrder.received_value_paisa,
        )
    rows = (
        await db.scalars(
            query.order_by(
                PurchaseOrder.payment_due_at.is_(None),
                PurchaseOrder.payment_due_at,
                PurchaseOrder.created_at,
            )
            .offset(offset)
            .limit(PAGE)
        )
    ).all()
    names = await _supplier_names(db, {row.supplier_id for row in rows})
    owed, paid, overdue = (
        await db.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(PurchaseOrder.received_value_paisa), 0),
                sa.func.coalesce(sa.func.sum(PurchaseOrder.paid_paisa), 0),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.case(
                            (
                                sa.and_(owing, PurchaseOrder.payment_due_at < now),
                                PurchaseOrder.received_value_paisa - PurchaseOrder.paid_paisa,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(PurchaseOrder.status != "DRAFT")
        )
    ).one()
    return {
        "items": [po_view(row, money=True, supplier=names.get(row.supplier_id)) for row in rows],
        "totals": {
            "owed_paisa": int(owed),
            "paid_paisa": int(paid),
            "outstanding_paisa": max(int(owed) - int(paid), 0),
            "overdue_paisa": int(overdue),
        },
        "can_pay": actor.can(Permission.PAYABLE_MANAGE),
    }


# ---------------------------------------------------------------- transfers ---


@router.get("/transfers")
async def transfers(db: DbSession, actor: Viewer, offset: int = Query(0, ge=0)) -> dict[str, Any]:
    rows = (
        await db.scalars(
            sa.select(StockTransfer)
            .order_by(StockTransfer.created_at.desc())
            .offset(offset)
            .limit(PAGE)
        )
    ).all()
    counts: dict[uuid.UUID, int] = {}
    if rows:
        for transfer_id, units in (
            await db.execute(
                sa.select(StockTransferLine.transfer_id, sa.func.sum(StockTransferLine.quantity))
                .where(StockTransferLine.transfer_id.in_([r.id for r in rows]))
                .group_by(StockTransferLine.transfer_id)
            )
        ).all():
            counts[transfer_id] = int(units or 0)
    return {
        "items": [
            {
                **_view(
                    r,
                    "id number from_warehouse_id to_warehouse_id status reference note "
                    "completed_at created_at",
                ),
                "units": counts.get(r.id, 0),
            }
            for r in rows
        ],
        "can_transfer": actor.can(Permission.INVENTORY_ADJUST),
    }


@router.post("/transfers", status_code=201)
async def transfer(body: TransferInput, db: DbSession, actor: Receiver) -> dict[str, Any]:
    row, replayed = await _svc(db, actor).transfer(
        from_warehouse_id=body.from_warehouse_id,
        to_warehouse_id=body.to_warehouse_id,
        lines=[TransferLine(x.product_id, x.variant_id, x.quantity) for x in body.lines],
        idempotency_key=body.idempotency_key,
        reference=body.reference,
        note=body.note,
    )
    return {
        **_view(row, "id number from_warehouse_id to_warehouse_id status completed_at"),
        "replayed": replayed,
    }


# -------------------------------------------------------------------- stock ---


@router.get("/stock")
async def stock(
    db: DbSession,
    actor: Viewer,
    search: str | None = Query(None, max_length=80),
    low: bool = False,
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = sa.select(Product).where(
        Product.archived_at.is_(None), Product.stock_tracking_enabled.is_(True)
    )
    if search:
        term = search.strip()
        query = query.where(
            sa.or_(
                sa.func.lower(Product.name).contains(term.lower(), autoescape=True),
                Product.sku == term,
            )
        )
    products = (
        await db.scalars(query.order_by(Product.name, Product.id).offset(offset).limit(PAGE))
    ).all()
    variants: dict[uuid.UUID, list[ProductVariant]] = {}
    ids = [p.id for p in products if p.has_variants]
    if ids:
        for variant in (
            await db.scalars(
                sa.select(ProductVariant)
                .where(ProductVariant.product_id.in_(ids), ProductVariant.is_active.is_(True))
                .order_by(ProductVariant.position, ProductVariant.name)
            )
        ).all():
            variants.setdefault(variant.product_id, []).append(variant)
    items: list[tuple[Product, ProductVariant | None]] = []
    for product in products:
        if product.has_variants:
            items += [(product, v) for v in variants.get(product.id, [])]
        else:
            items.append((product, None))
    rows = await _svc(db, actor).position(items)
    if low:
        rows = [row for row in rows if row["low"]]
    return {
        "items": rows,
        "has_more": len(products) == PAGE,
        # Orders deduct stock when a parcel is booked; nothing is held earlier,
        # so an "available to sell" figure would only restate on hand.
        "reservations_tracked": False,
        "reject_reasons": list(REJECT_REASONS),
        "payment_methods": list(PAYMENT_METHODS),
    }
