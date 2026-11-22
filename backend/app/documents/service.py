"""Loading orders into printable documents (V4 WP0.5).

Reads only the current shop's orders (``orders`` is tenant-owned, so another
shop's id is simply not found). The customer's full phone is decrypted for the
label because the courier needs it; the seller can ask for the masked form
instead. Each print request writes one audit row (kind, size, count, masked)
— never a phone number.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.audit import AuditAction, record_audit
from app.common.phone import try_normalize_bd_phone
from app.consignments.models import MANUAL_PROVIDER, Consignment
from app.core.errors import NotFoundError
from app.core.security import CredentialVault
from app.customers.models import Customer
from app.customers.service import CUSTOMER_PHONE_CONTEXT
from app.documents.render import (
    DocumentItem,
    DocumentOrder,
    LabelSize,
    render_invoices,
    render_labels,
)
from app.orders.models import Order
from app.tenants.models import Tenant

__all__ = ["COURIER_NAMES", "MAX_BULK", "DocumentService"]

MAX_BULK = 100
_PICKUP_PHONE_CONTEXT = "tenant.pickup_phone"
COURIER_NAMES = {
    "steadfast": "Steadfast",
    "pathao": "Pathao",
    "redx": "RedX",
    "carrybee": "Carrybee",
    MANUAL_PROVIDER: "Own delivery",
}


class DocumentService:
    def __init__(self, session: AsyncSession, *, vault: CredentialVault) -> None:
        self._db = session
        self._vault = vault

    async def invoices(self, tenant_id: uuid.UUID, order_ids: Sequence[uuid.UUID]) -> str:
        orders = await self._load(tenant_id, order_ids, mask_phone=False)
        await self._audit(tenant_id, "invoice", None, len(orders), masked=False)
        return render_invoices(orders)

    async def labels(
        self,
        tenant_id: uuid.UUID,
        order_ids: Sequence[uuid.UUID],
        *,
        size: LabelSize,
        mask_phone: bool,
    ) -> str:
        orders = await self._load(tenant_id, order_ids, mask_phone=mask_phone)
        await self._audit(tenant_id, "label", size, len(orders), masked=mask_phone)
        return render_labels(orders, size)

    # ------------------------------------------------------------ loading ---

    async def _load(
        self, tenant_id: uuid.UUID, order_ids: Sequence[uuid.UUID], *, mask_phone: bool
    ) -> list[DocumentOrder]:
        wanted = list(dict.fromkeys(order_ids))
        rows = (
            await self._db.scalars(
                sa.select(Order)
                .where(Order.id.in_(wanted), Order.deleted_at.is_(None))
                .options(selectinload(Order.items))
            )
        ).all()
        by_id = {order.id: order for order in rows}
        if len(by_id) != len(wanted):
            raise NotFoundError("Order not found")

        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError("Shop not found")
        shop_phone = self._pickup_phone(tenant)
        consignments = await self._latest_consignments(wanted)
        customers = await self._customers({o.customer_id for o in rows if o.customer_id})

        documents = []
        for order_id in wanted:
            order = by_id[order_id]
            consignment = consignments.get(order_id)
            documents.append(
                DocumentOrder(
                    order_number=order.order_number,
                    order_date=order.business_date,
                    shop_name=tenant.name,
                    shop_phone=shop_phone,
                    customer_name=order.customer_name,
                    customer_phone=self._customer_phone(
                        order, customers.get(order.customer_id), mask=mask_phone
                    ),
                    address=order.delivery_address_raw,
                    area=order.delivery_area,
                    district=order.delivery_district,
                    courier=COURIER_NAMES.get(consignment.provider, consignment.provider)
                    if consignment
                    else None,
                    tracking=(consignment.tracking_code or consignment.provider_consignment_id)
                    if consignment
                    else None,
                    subtotal_paisa=order.subtotal_paisa,
                    discount_paisa=order.discount_paisa,
                    delivery_fee_paisa=order.delivery_fee_paisa,
                    cod_amount_paisa=order.cod_amount_paisa,
                    items=tuple(
                        DocumentItem(
                            name=item.product_name,
                            variant=item.variant_label,
                            quantity=item.quantity,
                            unit_price_paisa=item.unit_price_paisa,
                            line_total_paisa=item.line_total_paisa,
                        )
                        for item in sorted(order.items, key=lambda i: i.position)
                    ),
                    note=order.note,
                )
            )
        return documents

    async def _latest_consignments(
        self, order_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Consignment]:
        rows = (
            await self._db.scalars(
                sa.select(Consignment)
                .where(Consignment.order_id.in_(order_ids))
                .order_by(Consignment.created_at)
            )
        ).all()
        latest: dict[uuid.UUID, Consignment] = {}
        for row in rows:  # ascending, so the newest wins
            latest[row.order_id] = row
        return latest

    async def _customers(self, ids: set[uuid.UUID]) -> dict[uuid.UUID | None, Customer]:
        if not ids:
            return {}
        rows = (await self._db.scalars(sa.select(Customer).where(Customer.id.in_(ids)))).all()
        return {row.id: row for row in rows}

    def _customer_phone(self, order: Order, customer: Customer | None, *, mask: bool) -> str | None:
        if customer is None:
            return order.customer_phone_masked
        number = try_normalize_bd_phone(
            self._vault.decrypt(customer.phone_enc, context=CUSTOMER_PHONE_CONTEXT)
        )
        if number is None:
            return order.customer_phone_masked
        return number.masked if mask else number.national

    def _pickup_phone(self, tenant: Tenant) -> str | None:
        if not tenant.pickup_phone_enc:
            return None
        number = try_normalize_bd_phone(
            self._vault.decrypt(tenant.pickup_phone_enc, context=_PICKUP_PHONE_CONTEXT)
        )
        return number.national if number else None

    async def _audit(
        self, tenant_id: uuid.UUID, kind: str, size: LabelSize | None, count: int, *, masked: bool
    ) -> None:
        await record_audit(
            self._db,
            AuditAction.DOCUMENTS_PRINTED,
            entity_type="order_documents",
            context={
                "kind": kind,
                "size": str(size) if size else None,
                "count": count,
                "phone": "masked" if masked else "full",
            },
            tenant_id=tenant_id,
        )
