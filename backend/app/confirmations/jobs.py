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

__all__ = ["follow_up_confirmations", "notify_attention"]

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


@register_handler(OutboxTopic.CONFIRMATION_CALLBACK_REQUESTED)
async def _on_callback_requested(_session: AsyncSession, event: OutboxEvent) -> None:
    # The review_needed event of the same move raises the seller alert.
    _log(event)


@register_handler(OutboxTopic.CONFIRMATION_REVIEW_NEEDED)
async def _on_review_needed(_session: AsyncSession, event: OutboxEvent) -> None:
    await notify_attention(_log(event), "REVIEW_NEEDED")


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
