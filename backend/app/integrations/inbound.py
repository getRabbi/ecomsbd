"""Store -> ecomsbd for orders already imported (V3.2, order status two-way).

A cancellation in the store cancels the ecomsbd order only while nothing has
left the shelf. Once a parcel exists, cancelling here would not stop the
courier, so it becomes a conflict for the seller instead. An edit in the store
(address, items, amount) is never applied silently: it is shown side by side.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_hasher, get_vault
from app.consignments.models import Consignment
from app.customers.service import CustomerService
from app.integrations import outbound, shopify_sync, sync
from app.integrations.models import IntegrationConnection
from app.integrations.normalize import Reject, Skip
from app.order_sources.models import ExternalOrder
from app.orders.models import Order, OrderItem, OrderStatus
from app.orders.service import OrderService

#: Consignment states in which no parcel exists at the courier.
NO_PARCEL = ("NOT_BOOKED", "CANCELLED", "FAILED")


def two_way(conn: IntegrationConnection) -> bool:
    return (
        conn.provider in sync.SYNC_PROVIDERS and sync.settings_of(conn)["order_status"] == "TWO_WAY"
    )


def store_word(conn: IntegrationConnection, node: dict[str, Any]) -> str:
    if conn.provider == "SHOPIFY":
        return shopify_sync.provider_status(node)
    return str(node.get("status") or "")[:40]


async def booked(db: AsyncSession, order: Order) -> bool:
    if order.status in {OrderStatus.FULFILLMENT_STARTED, OrderStatus.COMPLETED}:
        return True
    parcel = await db.scalar(
        sa.select(Consignment.id).where(
            Consignment.order_id == order.id, Consignment.status.not_in(NO_PARCEL)
        )
    )
    return parcel is not None


async def order_summary(db: AsyncSession, order: Order) -> dict[str, Any]:
    items = (
        await db.execute(
            sa.select(OrderItem.product_name, OrderItem.quantity)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.position)
        )
    ).all()
    return {
        "address": order.delivery_address_raw,
        "cod_amount_paisa": order.cod_amount_paisa,
        "items": [{"name": name, "quantity": qty} for name, qty in items],
        "status": str(order.status),
    }


def payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "address": payload.get("address"),
        "cod_amount_paisa": payload.get("cod_amount_paisa"),
        "items": [
            {"name": item.get("name"), "quantity": item.get("quantity")}
            for item in payload.get("items") or []
        ],
    }


async def on_existing(
    db: AsyncSession,
    conn: IntegrationConnection,
    node: dict[str, Any],
    existing: ExternalOrder,
    connector: Any,
) -> tuple[str, str] | None:
    """Handle a store update for an imported order; None leaves V3.1 behaviour."""
    if not two_way(conn):
        return None
    order = await db.scalar(sa.select(Order).where(Order.id == existing.order_id))
    if order is None:
        return None
    state = await outbound.order_state(db, conn, order.id, existing.external_order_id)
    state.provider_status = store_word(conn, node)
    if connector.is_cancelled(node):
        if order.status == OrderStatus.CANCELLED:
            return "IGNORED", "DUPLICATE_IGNORED"
        if await booked(db, order):
            await sync.open_conflict(
                db,
                conn,
                kind="CANCELLED_AFTER_BOOKING",
                entity="ORDER",
                fingerprint=f"cancel:{order.id}",
                order_id=order.id,
                detail={
                    "ecomsbd": {"status": str(order.status), "order_number": order.order_number},
                    "external": {"status": state.provider_status},
                },
            )
            return "IGNORED", "CONFLICT_OPENED"
        service = OrderService(
            db, customers=CustomerService(db, hasher=get_hasher(), vault=get_vault())
        )
        await service.transition(
            order.id,
            OrderStatus.CANCELLED,
            reason=f"Cancelled in {conn.provider.title()}"[:200],
        )
        return "PROCESSED", "CANCELLED_FROM_STORE"
    try:
        payload = connector.to_native(node)
    except (Skip, Reject):
        return "IGNORED", "DUPLICATE_IGNORED"
    from app.integrations.service import store_identity

    digest = store_identity(payload)
    if digest == existing.request_hash:
        return "IGNORED", "DUPLICATE_IGNORED"
    parcel = await booked(db, order)
    await sync.open_conflict(
        db,
        conn,
        kind="ORDER_CHANGED_EXTERNALLY",
        entity="ORDER",
        fingerprint=f"changed:{order.id}",
        order_id=order.id,
        recommended="CONTACT_COURIER" if parcel else "REVIEW_ORDER",
        detail={
            "ecomsbd": await order_summary(db, order),
            "external": payload_summary(payload),
            "external_hash": digest,
            "booked": parcel,
        },
    )
    return "IGNORED", "CONFLICT_OPENED"


async def mark_reviewed(
    db: AsyncSession, detail: dict[str, Any], order_id: Any, conn_id: Any
) -> None:
    """The seller has seen this version of the store order; do not raise it again."""
    digest = detail.get("external_hash")
    if not digest:
        return
    source_id = await db.scalar(
        sa.select(IntegrationConnection.source_id).where(IntegrationConnection.id == conn_id)
    )
    receipt = await db.scalar(
        sa.select(ExternalOrder).where(
            ExternalOrder.order_id == order_id, ExternalOrder.source_id == source_id
        )
    )
    if receipt is not None:
        receipt.request_hash = digest
