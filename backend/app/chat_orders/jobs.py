"""Chat-to-order workers.

``process_chat_messages`` reads received messages into drafts, one shop at a
time in that shop's own session (the per-shop scoping fixed for couriers in
PR #27 applies here from the start). ``chat_order_housekeeping`` expires
unfinished drafts and applies the raw-message retention.

A failure is logged by type and message id only. Message text is never passed
to the logger, an exception message or Sentry.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from app.chat_orders import service
from app.chat_orders.models import ChatMessage
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import session_scope, system_session

log = get_logger(__name__)

BATCH = 100


def _enter(tenant_id: uuid.UUID) -> Any:
    return set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="chat_orders",
        )
    )


async def process_one(tenant_id: uuid.UUID, message_id: uuid.UUID) -> str:
    token = _enter(tenant_id)
    try:
        async with session_scope() as db:
            return await service.process_message(db, message_id)
    finally:
        clear_context(token)


async def _give_up(tenant_id: uuid.UUID, message_id: uuid.UUID, code: str) -> None:
    token = _enter(tenant_id)
    try:
        async with session_scope() as db:
            row = await db.scalar(
                sa.select(ChatMessage).where(ChatMessage.id == message_id).with_for_update()
            )
            if row is None or row.processing_status != "RECEIVED":
                return
            row.attempts += 1
            if row.attempts >= service.MAX_ATTEMPTS:
                row.processing_status, row.processing_code = "FAILED", code
    finally:
        clear_context(token)


async def process_chat_messages(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("chat orders: received messages") as db:
        rows = (
            await db.execute(
                sa.select(ChatMessage.tenant_id, ChatMessage.id)
                .where(ChatMessage.processing_status == "RECEIVED")
                # Oldest first, so one customer's messages are read in order.
                .order_by(ChatMessage.provider_timestamp, ChatMessage.received_at)
                .limit(BATCH)
            )
        ).all()
    counts = {"processed": 0, "failed": 0}
    for tenant_id, message_id in rows:
        try:
            await process_one(tenant_id, message_id)
            counts["processed"] += 1
        except Exception as exc:
            counts["failed"] += 1
            # Type only: an exception's text can carry the message it was reading.
            log.warning(
                "chat message processing failed",
                extra={"message_id": str(message_id), "error": type(exc).__name__},
            )
            try:
                await _give_up(tenant_id, message_id, type(exc).__name__[:40])
            except Exception as again:
                log.warning(
                    "chat message retry bookkeeping failed",
                    extra={"message_id": str(message_id), "error": type(again).__name__},
                )
    return counts


async def chat_order_housekeeping(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    now = utc_now()
    async with system_session("chat orders: expiry and retention") as db:
        expired = await service.expire_due(db, now)
        purged = await service.purge_expired_data(db, now)
    result = {"expired": expired, **purged}
    log.info("chat order housekeeping finished", extra=result)
    return result
