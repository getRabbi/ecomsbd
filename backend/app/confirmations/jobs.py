"""Outbox handlers for confirmation topics (V4 WP2.1).

The order has already moved, in the same transaction as the confirmation, by
the time these run; they only tell the seller when something needs them:

*   ``confirmation.review_needed`` → a ``CONFIRMATION_NEEDS_ATTENTION`` alert;
*   ``confirmation.confirmed`` / ``confirmation.cancelled`` whose answer could
    not be applied (the order changed or moved on) → the same alert with that
    reason.

Everything else is recorded in the log. WP2.4 turns the topics into automation
triggers. Every notification is deduplicated per confirmation and reason, so a
retried event never alerts twice.

WP2.3 adds ``confirmation.link_requested`` (send the SMS confirm link) and the
``follow_up_confirmations`` sweep: a due ``NO_ANSWER`` row is called again,
sent the link or handed to the seller; an expired link goes to the seller.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic
from app.confirmations.models import ConfirmationState, OrderConfirmation, OrderOutcome
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import session_scope, system_session
from app.worker.jobs import register_handler

__all__ = [
    "dispatch_confirmation_calls",
    "follow_up_confirmations",
    "notify_attention",
    "notify_callback_requested",
]

log = get_logger("app.confirmations")

#: A customer answer that the order could not take: the seller decides.
_NOT_APPLIED = frozenset({str(OrderOutcome.ORDER_CHANGED), str(OrderOutcome.ORDER_NOT_DRAFT)})


def _log(event: OutboxEvent) -> dict[str, Any]:
    payload = event.payload or {}
    extra = {
        "topic": event.topic,
        "confirmation_id": str(payload.get("confirmation_id") or ""),
        "cause": str(payload.get("cause") or ""),
        "order_outcome": str(payload.get("order_outcome") or ""),
        "tenant_id": str(event.tenant_id or ""),
    }
    log.info("confirmation event", extra=extra)
    return payload


async def notify_attention(payload: dict[str, Any], reason: str) -> None:
    """Raise the seller alert in a tenant-scoped session (the dispatcher has
    already put the event's tenant into the context)."""
    from app.notifications.models import NotificationKind, Severity
    from app.notifications.service import NotificationService
    from app.notifications.templates import render

    confirmation_id = str(payload["confirmation_id"])
    params = {"reason": reason}
    title, body = render(NotificationKind.CONFIRMATION_NEEDS_ATTENTION, params, "en") or ("", "")
    async with session_scope() as db:
        await NotificationService(db).notify(
            kind=NotificationKind.CONFIRMATION_NEEDS_ATTENTION,
            severity=Severity.ACTION,
            title=title,
            body=body,
            dedupe_key=f"confirmation:{confirmation_id}:{reason}",
            entity_type="order",
            entity_id=uuid.UUID(str(payload["order_id"])),
            payload={"params": params, "target": {"route": "orders"}},
        )


async def _answer_not_applied(event: OutboxEvent) -> None:
    payload = _log(event)
    outcome = str(payload.get("order_outcome") or "")
    if outcome in _NOT_APPLIED:
        await notify_attention(payload, outcome)


@register_handler(OutboxTopic.CONFIRMATION_CONFIRMED)
async def _on_confirmed(_session: AsyncSession, event: OutboxEvent) -> None:
    await _answer_not_applied(event)


@register_handler(OutboxTopic.CONFIRMATION_CANCELLED)
async def _on_cancelled(_session: AsyncSession, event: OutboxEvent) -> None:
    await _answer_not_applied(event)


@register_handler(OutboxTopic.CONFIRMATION_NO_ANSWER)
async def _on_no_answer(_session: AsyncSession, event: OutboxEvent) -> None:
    _log(event)


async def notify_callback_requested(payload: dict[str, Any]) -> None:
    """The customer pressed 3 on the IVR (WP3.2): tell the owner and managers,
    opening the call queue where the Call button is."""
    from app.notifications.models import NotificationKind, Severity
    from app.notifications.service import NotificationService
    from app.notifications.templates import render

    confirmation_id = str(payload["confirmation_id"])
    params = {"digit": "3"}  # the template needs a parameter to render
    title, body = render(NotificationKind.CHAT_CALLBACK_REQUESTED, params, "en") or ("", "")
    async with session_scope() as db:
        await NotificationService(db).notify(
            kind=NotificationKind.CHAT_CALLBACK_REQUESTED,
            severity=Severity.ACTION,
            title=title,
            body=body,
            dedupe_key=f"confirmation:{confirmation_id}:CALLBACK",
            entity_type="order",
            entity_id=uuid.UUID(str(payload["order_id"])),
            payload={"params": params, "target": {"route": "call_queue"}},
        )


@register_handler(OutboxTopic.CONFIRMATION_CALLBACK_REQUESTED)
async def _on_callback_requested(_session: AsyncSession, event: OutboxEvent) -> None:
    await notify_callback_requested(_log(event))


@register_handler(OutboxTopic.CONFIRMATION_REVIEW_NEEDED)
async def _on_review_needed(_session: AsyncSession, event: OutboxEvent) -> None:
    payload = _log(event)
    if str(payload.get("cause") or "") == "DTMF_CALLBACK":
        return  # the callback request of the same move tells the seller
    await notify_attention(payload, "REVIEW_NEEDED")


def _services(db: AsyncSession) -> tuple[Any, Any, Any]:
    from app.api.deps import get_hasher, get_vault
    from app.confirmations.links import LinkService
    from app.customers.service import CustomerService
    from app.orders.service import OrderService

    hasher = get_hasher()
    customers = CustomerService(db, hasher=hasher, vault=get_vault())
    orders = OrderService(db, customers=customers)
    return LinkService(db, orders=orders, hasher=hasher), customers, orders


@register_handler(OutboxTopic.CONFIRMATION_LINK_REQUESTED)
async def _on_link_requested(_session: AsyncSession, event: OutboxEvent) -> None:
    payload = _log(event)
    async with session_scope() as db:
        links, customers, _orders = _services(db)
        sent = await links.deliver(payload, now=utc_now(), customers=customers)
    log.info(
        "confirmation link",
        extra={
            "confirmation_id": str(payload.get("confirmation_id") or ""),
            "outcome": str(sent.outcome) if sent is not None else "NOTHING_TO_SEND",
            "segments": sent.segments if sent is not None else None,
        },
    )


#: Rows looked at per sweep; the rest wait for the next one.
SWEEP_LIMIT = 500


async def follow_up_confirmations(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Due ``NO_ANSWER`` rows and expired links, one tenant session each."""
    from app.confirmations.links import FollowUp
    from app.confirmations.service import MAX_CALL_ATTEMPTS

    now = utc_now()
    no_answer = OrderConfirmation.state == str(ConfirmationState.NO_ANSWER)
    link_out = OrderConfirmation.state == str(ConfirmationState.SMS_LINK_SENT)
    async with system_session("confirmations: follow up") as db:
        due = (
            await db.execute(
                sa.select(OrderConfirmation.tenant_id, OrderConfirmation.id)
                .where(
                    sa.or_(
                        sa.and_(
                            no_answer,
                            sa.or_(
                                OrderConfirmation.next_action_at.is_(None),
                                OrderConfirmation.next_action_at <= now,
                                OrderConfirmation.attempt_count >= MAX_CALL_ATTEMPTS,
                            ),
                        ),
                        sa.and_(link_out, OrderConfirmation.next_action_at <= now),
                    )
                )
                .order_by(OrderConfirmation.next_action_at, OrderConfirmation.id)
                .limit(SWEEP_LIMIT)
            )
        ).all()
    counts = {str(outcome): 0 for outcome in FollowUp} | {"failed": 0}
    for tenant_id, confirmation_id in due:
        token = set_context(
            RequestContext(
                trace_id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                actor_type=ActorType.SYSTEM,
                job_name="confirmations_follow_up",
            )
        )
        try:
            async with session_scope() as db:
                row = await db.scalar(
                    sa.select(OrderConfirmation)
                    .where(OrderConfirmation.id == confirmation_id)
                    .with_for_update()
                )
                if row is None:
                    continue
                links, _customers, _orders = _services(db)
                outcome = await links.follow_up(row, now=now)
            counts[str(outcome)] += 1
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "confirmation follow-up failed",
                extra={"confirmation_id": str(confirmation_id), "error": type(exc).__name__},
            )
        finally:
            clear_context(token)
    log.info("confirmation follow-up finished", extra=counts)
    return counts


#: Calls placed per tick at most; the rest wait 30 seconds.
DISPATCH_LIMIT = 100


def _tenant_context(tenant_id: uuid.UUID, job: str) -> Any:
    return set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name=job,
        )
    )


async def dispatch_confirmation_calls(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Place due IVR calls and reconcile stuck ones (V4 WP3.2, every 30 s).

    Per row: transaction 1 records the attempt and ``CALLING`` and commits;
    then the provider is asked; transaction 2 records its answer. A crash in
    between leaves a ``CALLING`` row that :meth:`IvrCalls.reconcile` hands to
    the seller after ``CALL_TIMEOUT``.
    """
    from collections import Counter

    from app.confirmations.calls import PreparedCall, place, services, voice_gate, voice_transport
    from app.confirmations.manual import QuietHours
    from app.delivery_rules.service import DeliveryRulesService
    from app.orders.models import Order, OrderStatus
    from app.tenants.models import Tenant, TenantStatus

    now = utc_now()
    counts: Counter[str] = Counter()
    calling = OrderConfirmation.state == str(ConfirmationState.CALLING)
    async with system_session("confirmations: stuck calls") as db:
        stuck = (
            await db.execute(
                sa.select(OrderConfirmation.tenant_id, OrderConfirmation.id)
                .where(calling, OrderConfirmation.next_action_at <= now)
                .order_by(OrderConfirmation.next_action_at, OrderConfirmation.id)
                .limit(SWEEP_LIMIT)
            )
        ).all()
    for tenant_id, confirmation_id in stuck:
        token = _tenant_context(tenant_id, "confirmation_calls")
        try:
            async with session_scope() as db:
                row = await db.scalar(
                    sa.select(OrderConfirmation)
                    .where(OrderConfirmation.id == confirmation_id, calling)
                    .with_for_update(skip_locked=True)
                )
                if row is not None and await services(db).reconcile(row, now=now):
                    counts["reconciled"] += 1
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "stuck call reconcile failed",
                extra={"confirmation_id": str(confirmation_id), "error": type(exc).__name__},
            )
        finally:
            clear_context(token)

    async with system_session("confirmations: due calls") as db:
        due = (
            await db.execute(
                sa.select(OrderConfirmation.tenant_id, OrderConfirmation.id)
                .join(Order, Order.id == OrderConfirmation.order_id)
                .join(Tenant, Tenant.id == OrderConfirmation.tenant_id)
                .where(
                    OrderConfirmation.state == str(ConfirmationState.CALL_QUEUED),
                    sa.or_(
                        OrderConfirmation.next_action_at.is_(None),
                        OrderConfirmation.next_action_at <= now,
                    ),
                    # An order the seller confirmed or cancelled is never called.
                    Order.status == str(OrderStatus.DRAFT),
                    Order.deleted_at.is_(None),
                    Tenant.status == str(TenantStatus.ACTIVE),
                )
                .order_by(OrderConfirmation.next_action_at, OrderConfirmation.id)
                .limit(DISPATCH_LIMIT)
            )
        ).all()
    by_tenant: dict[uuid.UUID, list[uuid.UUID]] = {}
    for tenant_id, confirmation_id in due:
        by_tenant.setdefault(tenant_id, []).append(confirmation_id)

    transport = voice_transport()
    for tenant_id, ids in by_tenant.items():
        token = _tenant_context(tenant_id, "confirmation_calls")
        try:
            async with session_scope() as db:
                gate = await voice_gate(db, tenant_id, transport=transport)
                rules = await DeliveryRulesService(db).get(tenant_id)
            if not gate.on:
                counts["voice_off"] += len(ids)  # they stay in the manual queue
                continue
            if QuietHours.from_rules(rules).active(now):
                counts["quiet"] += len(ids)
                continue
            for confirmation_id in ids:
                try:
                    async with session_scope() as db:
                        prepared = await services(db).prepare(confirmation_id, gate=gate, now=now)
                    if not isinstance(prepared, PreparedCall):
                        counts[str(prepared)] += 1
                        continue
                    result, latency = await place(transport, prepared.call)
                    async with session_scope() as db:
                        await services(db).record_placed(
                            prepared, result, now=utc_now(), latency_ms=latency
                        )
                    counts[str(result.outcome)] += 1
                except Exception as exc:
                    counts["failed"] += 1
                    log.warning(
                        "confirmation call failed",
                        extra={
                            "confirmation_id": str(confirmation_id),
                            "error": type(exc).__name__,
                        },
                    )
        finally:
            clear_context(token)
    result_counts = dict(counts)
    log.info("confirmation calls finished", extra=result_counts)
    return result_counts
