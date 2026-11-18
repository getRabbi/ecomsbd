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
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic
from app.confirmations.models import OrderOutcome
from app.core.logging import get_logger
from app.db.session import session_scope
from app.worker.jobs import register_handler

__all__ = ["notify_attention"]

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
