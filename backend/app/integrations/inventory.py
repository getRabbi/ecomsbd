"""Stock between ecomsbd's ledger and a store, without either overwriting the other.

The ledger is the only stock truth. What is compared with the store is
*available to sell*: on-hand minus units on open orders that have not left
the shelf yet (the ledger decrements at courier booking, a store at checkout,
so on-hand alone would disagree after every sale).

For each mapped item, both sides are compared with what they last agreed on:

    authority ECOMSBD   ecomsbd changed          -> push to the store
                        store changed            -> wait for the order that
                                                    explains it, then conflict
    authority EXTERNAL  store changed            -> wait, then an explained
                                                    EXTERNAL_SYNC movement
                        ecomsbd changed          -> conflict
    either              both changed, now equal  -> accept
                        both changed, different  -> conflict
                        first sync, different    -> conflict

A conflict freezes that item until a person decides.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import Consignment, ConsignmentItem
from app.core.clock import utc_now
from app.integrations import catalog, sync
from app.integrations.models import IntegrationConnection, IntegrationEvent
from app.integrations.sync_models import IntegrationLink
from app.orders.models import Order, OrderItem
from app.products.models import Product, ProductVariant, StockMovementReason, StockMovementSource
from app.products.service import StockAdjustment, StockService

GRACE = timedelta(minutes=30)
OPEN_ORDER = ("DRAFT", "CONFIRMED", "PACKED", "FULFILLMENT_STARTED")
#: Consignment states whose stock is back on the shelf or never left it.
NOT_ON_ROAD = ("NOT_BOOKED", "BOOKING", "BOOKING_UNKNOWN", "CANCELLED", "FAILED", "RETURNED")

Key = tuple[uuid.UUID, uuid.UUID | None]


@dataclass(frozen=True)
class Decision:
    action: str  # NOOP, ACCEPT, WAIT, PUSH, PULL or CONFLICT
    target: int | None = None
    conflict: str | None = None


def decide(
    authority: str,
    *,
    synced: int | None,
    basis: int | None,
    external: int,
    local: int,
    pending_since: datetime | None,
    now: datetime,
) -> Decision:
    if authority not in {"ECOMSBD", "EXTERNAL"}:
        return Decision("NOOP")
    if external == local:
        return Decision("ACCEPT")
    if synced is None or basis is None:
        return Decision("CONFLICT", conflict="STOCK_INITIAL_MISMATCH")
    store_moved, ledger_moved = external != synced, local != basis
    if not store_moved and not ledger_moved:
        return Decision("NOOP")  # a difference someone already accepted
    if store_moved and ledger_moved:
        return Decision("CONFLICT", conflict="STOCK_CHANGED_BOTH")
    waited = pending_since is not None and now - pending_since >= GRACE
    if authority == "ECOMSBD":
        if ledger_moved:
            return Decision("PUSH", target=local)
        return (
            Decision("CONFLICT", conflict="STOCK_CHANGED_EXTERNALLY")
            if waited
            else Decision("WAIT")
        )
    if store_moved:
        return Decision("PULL", target=external) if waited else Decision("WAIT")
    return Decision("CONFLICT", conflict="STOCK_CHANGED_IN_ECOMSBD")


async def available(db: AsyncSession, keys: list[Key]) -> dict[Key, int]:
    """On-hand minus units on open orders still on the shelf, per item."""
    if not keys:
        return {}
    product_ids = {p for p, _ in keys}
    variant_ids = {v for _, v in keys if v is not None}
    on_hand: dict[Key, int] = {}
    for product in (await db.scalars(sa.select(Product).where(Product.id.in_(product_ids)))).all():
        on_hand[(product.id, None)] = product.stock_on_hand
    if variant_ids:
        for variant in (
            await db.scalars(sa.select(ProductVariant).where(ProductVariant.id.in_(variant_ids)))
        ).all():
            on_hand[(variant.product_id, variant.id)] = variant.stock_on_hand
    shipped = (
        sa.select(ConsignmentItem.id)
        .join(Consignment, Consignment.id == ConsignmentItem.consignment_id)
        .where(
            ConsignmentItem.order_item_id == OrderItem.id,
            Consignment.status.not_in(NOT_ON_ROAD),
        )
        .exists()
    )
    rows = await db.execute(
        sa.select(OrderItem.product_id, OrderItem.variant_id, sa.func.sum(OrderItem.quantity))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            OrderItem.product_id.in_(product_ids),
            Order.status.in_(OPEN_ORDER),
            Order.deleted_at.is_(None),
            ~shipped,
        )
        .group_by(OrderItem.product_id, OrderItem.variant_id)
    )
    committed = {(p, v): int(q or 0) for p, v, q in rows.all()}
    return {key: on_hand.get(key, 0) - committed.get(key, 0) for key in keys}


def key_of(link: IntegrationLink) -> Key | None:
    return (link.product_id, link.variant_id) if link.product_id else None


async def mapped_links(db: AsyncSession, conn: IntegrationConnection) -> list[IntegrationLink]:
    return list(
        (
            await db.scalars(
                sa.select(IntegrationLink).where(
                    IntegrationLink.connection_id == conn.id,
                    IntegrationLink.state == "MATCHED",
                    IntegrationLink.external_tracked.is_(True),
                )
            )
        ).all()
    )


async def _push_pending(db: AsyncSession, conn_id: uuid.UUID, link_id: uuid.UUID) -> bool:
    return (
        await db.scalar(
            sa.select(IntegrationEvent.id).where(
                IntegrationEvent.connection_id == conn_id,
                IntegrationEvent.operation == "PUSH_INVENTORY",
                IntegrationEvent.status == "QUEUED",
                IntegrationEvent.delivery_id.like(f"inv:{link_id}:%"),
            )
        )
    ) is not None


async def pull(
    db: AsyncSession, link: IntegrationLink, *, local: int, external: int, key: str
) -> None:
    """Move the ledger by exactly the difference, with an idempotency key."""
    if link.product_id is None or external == local:
        return
    await StockService(db).record_movement(
        StockAdjustment(
            product_id=link.product_id,
            variant_id=link.variant_id,
            quantity_delta=external - local,
            reason=StockMovementReason.EXTERNAL_SYNC,
            source=StockMovementSource.INTEGRATION,
            note=f"Store stock is {external}",
            reference=f"integration-link:{link.id}"[:120],
            idempotency_key=key[:120],
        ),
        allow_negative=True,
    )


def agreed(link: IntegrationLink, *, external: int, local: int) -> None:
    link.synced_qty, link.local_basis = external, local
    link.external_qty = external
    link.pending_since, link.last_synced_at = None, utc_now()


async def apply(
    db: AsyncSession,
    conn: IntegrationConnection,
    link: IntegrationLink,
    *,
    external: int,
    local: int,
) -> str:
    """Act on one item's comparison inside the caller's (shop-locked) transaction."""
    now = utc_now()
    authority = sync.settings_of(conn)["inventory"]
    if await sync.has_open(db, conn.id, f"stock:{link.id}") or await _push_pending(
        db, conn.id, link.id
    ):
        link.external_qty = external
        return "FROZEN"
    decision = decide(
        authority,
        synced=link.synced_qty,
        basis=link.local_basis,
        external=external,
        local=local,
        pending_since=link.pending_since,
        now=now,
    )
    link.external_qty = external
    if decision.action == "ACCEPT":
        agreed(link, external=external, local=local)
    elif decision.action == "WAIT":
        link.pending_since = link.pending_since or now
    elif decision.action == "PULL":
        await pull(
            db,
            link,
            local=local,
            external=external,
            key=f"extsync:{link.id}:{link.synced_qty}>{external}:{link.local_basis}",
        )
        agreed(link, external=external, local=external)
    elif decision.action == "PUSH":
        await catalog.queue(
            db,
            conn,
            operation="PUSH_INVENTORY",
            key=f"inv:{link.id}:{external}>{max(0, local)}:{local}",
            payload={
                "link_id": str(link.id),
                "quantity": max(0, local),
                "change_from": external,
                "basis": local,
            },
        )
    elif decision.action == "CONFLICT" and decision.conflict:
        await sync.open_conflict(
            db,
            conn,
            kind=decision.conflict,
            entity="INVENTORY",
            fingerprint=f"stock:{link.id}",
            link_id=link.id,
            recommended="USE_EXTERNAL" if authority == "EXTERNAL" else "USE_ECOMSBD",
            detail={
                "ecomsbd": {"available": local},
                "external": {"available": external},
                "last_agreed": link.synced_qty,
                "title": link.external_title,
            },
        )
    return decision.action


async def resolve_stock(
    db: AsyncSession, conn: IntegrationConnection, link: IntegrationLink, resolution: str
) -> None:
    """Carry out a person's answer to a stock conflict."""
    key = key_of(link)
    if key is None:
        return
    local = (await available(db, [key]))[key]
    external = link.external_qty if link.external_qty is not None else local
    if resolution == "USE_ECOMSBD":
        await catalog.queue(
            db,
            conn,
            operation="PUSH_INVENTORY",
            key=f"inv:{link.id}:{external}>{max(0, local)}:{local}:resolved",
            payload={
                "link_id": str(link.id),
                "quantity": max(0, local),
                "change_from": external,
                "basis": local,
            },
        )
    elif resolution == "USE_EXTERNAL":
        await pull(
            db,
            link,
            local=local,
            external=external,
            key=f"extsync:{link.id}:resolved:{external}:{local}:{uuid.uuid4().hex[:8]}",
        )
        agreed(link, external=external, local=external)
    elif resolution == "ACCEPT_DIFFERENCE":
        agreed(link, external=external, local=local)


def summary(links: list[IntegrationLink]) -> dict[str, Any]:
    return {
        "mapped": sum(1 for link in links if link.state == "MATCHED"),
        "tracked": sum(1 for link in links if link.external_tracked),
        "last_synced_at": max(
            (link.last_synced_at for link in links if link.last_synced_at), default=None
        ),
    }
