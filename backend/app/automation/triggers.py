"""Which workflow triggers and event waits one domain event stands for.

Workflows consume the transactional outbox *when an event is written*, in the
producer's own transaction (see ``app.common.outbox.enqueue``). Nothing here
polls for events that were already emitted, and a rolled-back business change
never starts or resumes a workflow.

One internal event can stand for several seller-facing triggers, in the same
way it can stand for several public webhook topics: ``order.status_changed`` to
``CONFIRMED`` is both "status changed" and "order confirmed".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.customer_segments import REPEAT_MIN_ORDERS
from app.common.outbox import OutboxEvent, OutboxTopic
from app.orders.models import Order
from app.products.models import Product, ProductVariant
from app.worker.jobs import register_handler

__all__ = ["Match", "Reading", "read_event"]

#: Smart-alert kinds (app.notifications.smart) that start a workflow.
ALERT_TRIGGERS = {
    "PAYOUT_OVERDUE": "payout.overdue",
    "RECONCILIATION_DISCREPANCY": "reconciliation.issue",
    "FOLLOW_UP_DUE": "followup.due",
}
DELIVERED = frozenset({"DELIVERED", "PARTIAL_DELIVERED"})
RETURNED = frozenset({"RETURNED"})


@dataclass
class Match:
    trigger: str
    subject: dict[str, Any]
    subject_key: str
    #: Facts of the event itself; they outrank live facts in entry conditions.
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reading:
    matches: list[Match] = field(default_factory=list)
    #: Keys of event waits this event satisfies.
    wait_keys: list[str] = field(default_factory=list)


def wait_key(event: str, subject: dict[str, Any]) -> str | None:
    """The key a waiting run and the event that wakes it agree on."""
    from app.automation.schemas import WAIT_EVENTS

    kind = WAIT_EVENTS.get(event)
    if kind == "order" and subject.get("order_id"):
        return f"order:{subject['order_id']}:{event}"
    if kind == "customer" and subject.get("customer_id"):
        return f"customer:{subject['customer_id']}:{event}"
    return None


def _id(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _order_subject(db: AsyncSession, event: OutboxEvent) -> tuple[Order, dict] | None:
    order_id = _id(event.payload.get("order_id"))
    if order_id is None:
        return None
    order = await db.scalar(
        sa.select(Order).where(
            Order.id == order_id, Order.tenant_id == event.tenant_id, Order.deleted_at.is_(None)
        )
    )
    if order is None:
        return None
    subject = {
        "order_id": str(order.id),
        "customer_id": str(order.customer_id) if order.customer_id else None,
    }
    return order, subject


async def read_event(db: AsyncSession, event: OutboxEvent) -> Reading:
    reading = Reading()
    topic, payload = event.topic, event.payload or {}

    def add(trigger: str, subject: dict, key: str, facts: dict | None = None) -> None:
        reading.matches.append(Match(trigger, subject, key, facts or {}))

    if topic in {
        OutboxTopic.ORDER_CREATED,
        OutboxTopic.ORDER_STATUS_CHANGED,
        OutboxTopic.ORDER_BOOKED,
        OutboxTopic.CONSIGNMENT_STATUS_CHANGED,
        OutboxTopic.COD_SETTLED,
    }:
        found = await _order_subject(db, event)
        if found is None:
            return reading
        order, subject = found
        key = f"order:{order.id}"
        if topic == OutboxTopic.ORDER_CREATED:
            add("order.created", subject, key)
            if str(order.channel) == "API":
                add("order.external_received", subject, key)
            if order.customer_id is not None:
                count = await db.scalar(
                    sa.select(sa.func.count(Order.id)).where(
                        Order.customer_id == order.customer_id, Order.deleted_at.is_(None)
                    )
                )
                # Exactly the order that crossed the threshold: deterministic,
                # and a later order cannot announce the same entry again.
                if count == REPEAT_MIN_ORDERS:
                    buyer = {"customer_id": str(order.customer_id)}
                    add(
                        "customer.segment_entered",
                        buyer,
                        f"customer:{order.customer_id}",
                        {"entered_segment": "REPEAT"},
                    )
        elif topic == OutboxTopic.ORDER_STATUS_CHANGED:
            target = str(payload.get("to") or "")
            facts = {"status": target}
            add("order.status_changed", subject, key, facts)
            if target == "CONFIRMED":
                add("order.confirmed", subject, key, facts)
                reading.wait_keys.append(f"{key}:order.confirmed")
            if target == "CANCELLED":
                add("order.cancelled", subject, key, facts)
        elif topic == OutboxTopic.ORDER_BOOKED:
            provider = str(payload.get("provider") or "").lower() or None
            add("courier.booked", subject, key, {"courier": provider} if provider else {})
            reading.wait_keys.append(f"{key}:courier.booked")
        elif topic == OutboxTopic.CONSIGNMENT_STATUS_CHANGED:
            new = str(payload.get("new_status") or "")
            facts = {"consignment_status": new}
            add("courier.status_changed", subject, key, facts)
            if new in DELIVERED:
                add("order.delivered", subject, key, facts)
                reading.wait_keys.append(f"{key}:order.delivered")
            if new in RETURNED:
                add("order.returned", subject, key, facts)
        else:
            add("cod.settled", subject, key)
        return reading

    if topic == OutboxTopic.ALERT_RAISED:
        kind = str(payload.get("kind") or "")
        trigger = ALERT_TRIGGERS.get(kind)
        if trigger:
            add(trigger, {"alert_kind": kind}, f"alert:{kind}", {"alert_kind": kind})
        return reading

    if topic == OutboxTopic.INVENTORY_CHANGED:
        crossed = await _crossed_low_stock(db, payload)
        if crossed is not None:
            add(
                "inventory.low",
                crossed,
                f"product:{crossed['variant_id'] or crossed['product_id']}",
            )
        return reading

    if topic == OutboxTopic.INTEGRATION_ISSUE_OPENED:
        subject = {
            "integration_event_id": payload.get("integration_event_id"),
            "connection_id": payload.get("connection_id"),
        }
        add("integration.sync_failed", subject, f"integration:{payload.get('connection_id')}")
        return reading

    customer = _id(payload.get("customer_id"))
    if customer is None:
        return reading
    person = {"customer_id": str(customer)}
    key = f"customer:{customer}"
    if topic == OutboxTopic.SEGMENT_ENTERED:
        add(
            "customer.segment_entered",
            person,
            key,
            {"entered_segment": str(payload.get("segment") or "")},
        )
    elif topic == OutboxTopic.CUSTOMER_REPLIED:
        add("customer.replied", person, key)
        reading.wait_keys.append(f"{key}:customer.replied")
    elif topic == OutboxTopic.FOLLOWUP_COMPLETED:
        add("followup.completed", person, key)
        reading.wait_keys.append(f"{key}:followup.completed")
    return reading


async def _crossed_low_stock(db: AsyncSession, payload: dict[str, Any]) -> dict | None:
    """The item, if this movement took it from above its low level to at or below.

    Only the crossing counts: every later sale of an already-low item is not a
    new "stock went low" event.
    """
    after, delta = payload.get("stock_on_hand"), payload.get("quantity_delta")
    if type(after) is not int or type(delta) is not int or delta >= 0:
        return None
    variant_id, product_id = _id(payload.get("variant_id")), _id(payload.get("product_id"))
    if variant_id is not None:
        threshold = await db.scalar(
            sa.select(ProductVariant.low_stock_threshold).where(ProductVariant.id == variant_id)
        )
    elif product_id is not None:
        threshold = await db.scalar(
            sa.select(Product.low_stock_threshold).where(
                Product.id == product_id, Product.stock_tracking_enabled.is_(True)
            )
        )
    else:
        return None
    if threshold is None or not (after <= threshold < after - delta):
        return None
    return {
        "product_id": str(product_id) if product_id else None,
        "variant_id": str(variant_id) if variant_id else None,
    }


# Consumed when written; registered so the dispatcher marks them done rather
# than leaving them at the head of the outbox forever.
async def _consumed_at_enqueue(_session: AsyncSession, _event: OutboxEvent) -> None:
    return None


for _topic in (
    OutboxTopic.COD_SETTLED,
    OutboxTopic.ALERT_RAISED,
    OutboxTopic.INTEGRATION_ISSUE_OPENED,
    OutboxTopic.CUSTOMER_REPLIED,
    OutboxTopic.FOLLOWUP_COMPLETED,
    OutboxTopic.SEGMENT_ENTERED,
):
    register_handler(_topic)(_consumed_at_enqueue)
