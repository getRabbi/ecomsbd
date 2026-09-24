"""ecomsbd -> store: order status, fulfillment/tracking, stock and price pushes.

Operations are queued from outbox events in the same place every consumer of
those events runs, one durable ``IntegrationEvent`` per operation key, so a
replayed outbox event or a double-clicked retry cannot push twice. Execution
reads the current ecomsbd state first: a queued cancel for an order that is no
longer cancelled is dropped, not sent.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic
from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.integrations import catalog, shopify_sync, sync, woocommerce_sync
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection, IntegrationEvent
from app.integrations.sync_models import IntegrationLink, IntegrationOrderState
from app.order_sources.models import ExternalOrder
from app.orders.models import Order
from app.worker.jobs import register_handler

CARRIERS = {"steadfast": "Steadfast", "pathao": "Pathao", "redx": "RedX", "manual": "Courier"}
#: How each store says "cancelled"; a cancel it reported is not pushed back.
CANCELLED_WORDS = frozenset({"CANCELLED", "cancelled"})
#: Outcomes that are not failures: the store is already there, or cannot be.
SETTLED = frozenset({"NOTHING_TO_FULFILL", "NOT_SUPPORTED_BY_PROVIDER"})
DIVERGED = frozenset({"STORE_STATUS_FINAL", "ORDER_CANCELLED_AT_PROVIDER"})


def carrier_name(provider: str | None) -> str:
    return CARRIERS.get((provider or "").lower(), (provider or "Courier").title())


async def _connection_for_order(
    session: AsyncSession, tenant_id: uuid.UUID, order_id: uuid.UUID
) -> tuple[IntegrationConnection, str] | None:
    row = (
        await session.execute(
            sa.select(ExternalOrder.source_id, ExternalOrder.external_order_id).where(
                ExternalOrder.tenant_id == tenant_id, ExternalOrder.order_id == order_id
            )
        )
    ).first()
    if row is None:
        return None
    conn = await session.scalar(
        sa.select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationConnection.source_id == row[0],
            IntegrationConnection.provider.in_(sorted(sync.SYNC_PROVIDERS)),
        )
    )
    return (conn, row[1]) if conn is not None else None


async def order_state(
    session: AsyncSession, conn: IntegrationConnection, order_id: uuid.UUID, external_id: str
) -> IntegrationOrderState:
    state = await session.scalar(
        sa.select(IntegrationOrderState).where(
            IntegrationOrderState.tenant_id == conn.tenant_id,
            IntegrationOrderState.connection_id == conn.id,
            IntegrationOrderState.order_id == order_id,
        )
    )
    if state is None:
        state = IntegrationOrderState(
            tenant_id=conn.tenant_id,
            connection_id=conn.id,
            order_id=order_id,
            external_order_id=external_id,
        )
        session.add(state)
        await session.flush()
    return state


def wanted(conn: IntegrationConnection, operation: str) -> bool:
    """Whether settings and provider both call for this push."""
    settings = sync.settings_of(conn)
    if operation == "PUSH_CONFIRM":
        return conn.provider == "WOOCOMMERCE" and sync.can(conn, "order_status")
    if operation == "PUSH_CANCEL":
        return sync.can(conn, "order_status")
    if operation == "PUSH_FULFILLMENT":
        return sync.can(conn, "fulfillment")
    if operation == "PUSH_DELIVERED":
        if conn.provider == "SHOPIFY":
            return sync.can(conn, "fulfillment")
        return settings["order_status"] == "TWO_WAY" and sync.can(conn, "order_status")
    return False


async def schedule(
    session: AsyncSession,
    event: OutboxEvent,
    operation: str,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> int:
    if event.tenant_id is None:
        return 0
    try:
        order_id = uuid.UUID(str(event.payload.get("order_id")))
    except ValueError:
        return 0
    return await queue_for_order(
        session, event.tenant_id, order_id, operation, key=key, payload=payload
    )


async def queue_for_order(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
    operation: str,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> int:
    """Queue one store push for an order; 0 when nothing is connected or wanted.

    The operation key is the dedupe: the automatic push and a workflow asking
    for the same push land on the same ``IntegrationEvent``.
    """
    found = await _connection_for_order(session, tenant_id, order_id)
    if found is None:
        return 0
    conn, external_id = found
    if not wanted(conn, operation):
        return 0
    state = await order_state(session, conn, order_id, external_id)
    if operation == "PUSH_CANCEL" and state.provider_status in CANCELLED_WORDS:
        return 0  # the store told us first; echoing it back would loop
    return await catalog.queue(
        session,
        conn,
        operation=operation,
        key=key,
        payload={"order_id": str(order_id), **(payload or {})},
        order_id=order_id,
    )


@register_handler(OutboxTopic.ORDER_STATUS_CHANGED)
async def on_status_changed(session: AsyncSession, event: OutboxEvent) -> None:
    target = event.payload.get("to")
    operation = {"CONFIRMED": "PUSH_CONFIRM", "CANCELLED": "PUSH_CANCEL"}.get(str(target))
    if operation:
        await schedule(
            session, event, operation, key=f"{operation}:{event.payload.get('order_id')}"
        )


@register_handler(OutboxTopic.ORDER_BOOKED)
async def on_booked(session: AsyncSession, event: OutboxEvent) -> None:
    consignment = event.payload.get("consignment_id")
    await schedule(
        session,
        event,
        "PUSH_FULFILLMENT",
        key=f"PUSH_FULFILLMENT:{consignment}",
        payload={"consignment_id": str(consignment)},
    )


@register_handler(OutboxTopic.CONSIGNMENT_STATUS_CHANGED)
async def on_consignment(session: AsyncSession, event: OutboxEvent) -> None:
    if event.payload.get("new_status") in {"DELIVERED", "PARTIAL_DELIVERED"}:
        consignment = event.payload.get("consignment_id")
        await schedule(
            session,
            event,
            "PUSH_DELIVERED",
            key=f"PUSH_DELIVERED:{consignment}",
            payload={"consignment_id": str(consignment)},
        )


@register_handler(OutboxTopic.PRODUCT_UPDATED)
async def on_product(session: AsyncSession, event: OutboxEvent) -> None:
    if event.tenant_id is None or "price" not in (event.payload.get("changed") or []):
        return
    product_id = uuid.UUID(str(event.payload["product_id"]))
    variant = event.payload.get("variant_id")
    conns = (
        await session.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == event.tenant_id,
                IntegrationConnection.state == "CONNECTED",
                IntegrationConnection.provider.in_(sorted(sync.SYNC_PROVIDERS)),
            )
        )
    ).all()
    for conn in conns:
        await catalog.schedule_price_push(
            session, conn, product_id, uuid.UUID(variant) if variant else None
        )


# Topics whose consumers (signed webhooks, automation) run when the event is
# written. Registered so the dispatcher marks them done instead of re-queueing
# them forever at the head of the outbox, ahead of topics that do have work.
async def _fanned_out_at_enqueue(_session: AsyncSession, _event: OutboxEvent) -> None:
    return None


for _topic in (
    OutboxTopic.ORDER_CREATED,
    OutboxTopic.ORDER_UPDATED,
    OutboxTopic.INVENTORY_CHANGED,
):
    register_handler(_topic)(_fanned_out_at_enqueue)


# ------------------------------------------------------------- execution ---


async def prepare(db: AsyncSession, event: IntegrationEvent) -> dict[str, Any] | str:
    """Everything the provider call needs, or a code saying why not to make it."""
    payload = event.payload or {}
    op = event.operation or ""
    if op in {"PUSH_INVENTORY", "PUSH_PRICE"}:
        link = await db.get(IntegrationLink, uuid.UUID(payload["link_id"]))
        if link is None or link.state != "MATCHED":
            return "MAPPING_CHANGED"
        return {
            "link": link,
            "product_id": link.external_product_id,
            "variant_id": link.external_variant_id,
            "inventory_id": link.external_inventory_id,
        }
    order = await db.scalar(sa.select(Order).where(Order.id == uuid.UUID(payload["order_id"])))
    if order is None:
        return "NOT_FOUND"
    state = await db.scalar(
        sa.select(IntegrationOrderState).where(
            IntegrationOrderState.connection_id == event.connection_id,
            IntegrationOrderState.order_id == order.id,
        )
    )
    if state is None:
        return "NOT_FOUND"
    if op == "PUSH_CANCEL" and order.status != "CANCELLED":
        return "NO_LONGER_WANTED"
    if op == "PUSH_CONFIRM" and order.status == "CANCELLED":
        return "NO_LONGER_WANTED"
    context: dict[str, Any] = {"state": state, "external_id": state.external_order_id}
    if op in {"PUSH_FULFILLMENT", "PUSH_DELIVERED"}:
        consignment = await db.get(Consignment, uuid.UUID(payload["consignment_id"]))
        if consignment is None:
            return "NOT_FOUND"
        context |= {
            "carrier": carrier_name(consignment.provider),
            "tracking": consignment.tracking_code,
            "happened_at": (consignment.delivered_at or utc_now()).isoformat(),
        }
    return context


async def execute(
    link_view: Any, credentials: dict[str, Any], event: IntegrationEvent, ctx: dict[str, Any]
) -> str | None:
    """One provider call. Returns a reference later operations may need."""
    account = link_view.account_id or ""
    op = event.operation
    payload = event.payload or {}
    shop = link_view.provider == "SHOPIFY"
    if op == "PUSH_INVENTORY":
        if shop:
            location = sync.DEFAULT_SYNC | (link_view.config.get("sync") or {})
            if not ctx["inventory_id"] or not location.get("location_id"):
                raise ProviderError("NOT_STOCKED_AT_LOCATION")
            await shopify_sync.set_stock(
                account,
                credentials,
                inventory_id=ctx["inventory_id"],
                location_id=str(location["location_id"]),
                quantity=int(payload["quantity"]),
                change_from=payload.get("change_from"),
                idempotency_key=str(event.id),
            )
        else:
            await woocommerce_sync.set_stock(
                account, credentials, ctx["product_id"], ctx["variant_id"], int(payload["quantity"])
            )
        return None
    if op == "PUSH_PRICE":
        if shop:
            await shopify_sync.set_price(
                account,
                credentials,
                product_id=ctx["product_id"],
                variant_id=ctx["variant_id"] or "",
                price_paisa=int(payload["price_paisa"]),
            )
        else:
            await woocommerce_sync.set_price(
                account,
                credentials,
                ctx["product_id"],
                ctx["variant_id"],
                int(payload["price_paisa"]),
            )
        return None
    external = ctx["external_id"]
    if op == "PUSH_CANCEL":
        if shop:
            await shopify_sync.cancel(account, credentials, external)
            return "CANCELLED"
        return await woocommerce_sync.push_status(account, credentials, external, "CANCELLED")
    if op == "PUSH_CONFIRM":
        return await woocommerce_sync.push_status(account, credentials, external, "CONFIRMED")
    if op == "PUSH_FULFILLMENT":
        if shop:
            return await shopify_sync.fulfill(
                account, credentials, external, carrier=ctx["carrier"], tracking=ctx["tracking"]
            )
        return await woocommerce_sync.add_tracking_note(
            account,
            credentials,
            external,
            key=str(event.delivery_id),
            carrier=ctx["carrier"],
            tracking=ctx["tracking"],
        )
    if op == "PUSH_DELIVERED":
        if shop:
            fulfillment = ctx["state"].fulfillment_ref
            if not fulfillment:
                raise ProviderError("NOTHING_TO_FULFILL")
            await shopify_sync.mark_delivered(account, credentials, fulfillment, ctx["happened_at"])
            return "DELIVERED"
        return await woocommerce_sync.push_status(account, credentials, external, "DELIVERED")
    raise ProviderError("NOT_SUPPORTED_BY_PROVIDER")


async def record(
    db: AsyncSession, conn: IntegrationConnection, event: IntegrationEvent, ref: str | None
) -> None:
    """Remember what the store now says, after a successful push."""
    payload = event.payload or {}
    op = event.operation
    if op == "PUSH_INVENTORY":
        link = await db.get(IntegrationLink, uuid.UUID(payload["link_id"]))
        if link is not None:
            link.synced_qty = link.external_qty = int(payload["quantity"])
            link.local_basis = int(payload.get("basis", payload["quantity"]))
            link.pending_since, link.last_synced_at = None, utc_now()
        return
    if op == "PUSH_PRICE":
        link = await db.get(IntegrationLink, uuid.UUID(payload["link_id"]))
        if link is not None:
            link.external_price_paisa = int(payload["price_paisa"])
        return
    state = await db.scalar(
        sa.select(IntegrationOrderState).where(
            IntegrationOrderState.connection_id == conn.id,
            IntegrationOrderState.order_id == uuid.UUID(payload["order_id"]),
        )
    )
    if state is None:
        return
    state.pushed_status = (op or "").removeprefix("PUSH_")
    if op == "PUSH_FULFILLMENT":
        consignment = await db.get(Consignment, uuid.UUID(payload["consignment_id"]))
        state.fulfillment_ref = ref
        if consignment is not None:
            state.carrier = carrier_name(consignment.provider)
            state.tracking_code = consignment.tracking_code
    elif ref:
        state.provider_status = ref[:40]


async def diverged(
    db: AsyncSession, conn: IntegrationConnection, event: IntegrationEvent, code: str
) -> None:
    """The store is somewhere ecomsbd will not overwrite; a person looks."""
    if not event.order_id:
        return
    await sync.open_conflict(
        db,
        conn,
        kind="STATUS_DIVERGED",
        entity="ORDER",
        fingerprint=f"status:{event.order_id}",
        order_id=event.order_id,
        detail={
            "ecomsbd": {"wanted": (event.operation or "").removeprefix("PUSH_")},
            "external": {"code": code},
        },
    )
