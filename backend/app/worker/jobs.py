"""Background jobs.

Phase A ships one real job — the outbox dispatcher — plus the handler registry
that later phases plug into.

The dispatcher is the piece every future side effect depends on: consignment
polling, unknown-booking reconciliation, payout matching, COD aging, the Friday
summary and SMS all become outbox topics with a handler (master spec sections
41, 42). Handlers are looked up by topic, and a topic with no handler is left
``PENDING`` rather than being marked done, so shipping a producer before its
consumer delays the work instead of losing it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import (
    OutboxEvent,
    OutboxTopic,
    claim_batch,
    mark_done,
    mark_failed,
)
from app.core.config import get_settings
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger, log_duration
from app.db.session import system_session

__all__ = ["HANDLERS", "dispatch_outbox", "register_handler"]

log = get_logger("app.worker")

Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]

#: topic -> handler. Populated by feature modules as they ship.
HANDLERS: dict[str, Handler] = {}


def register_handler(topic: OutboxTopic | str) -> Callable[[Handler], Handler]:
    """Register a handler for an outbox topic."""

    def decorator(func: Handler) -> Handler:
        HANDLERS[str(topic)] = func
        return func

    return decorator


@register_handler(OutboxTopic.TENANT_CREATED)
async def _on_tenant_created(_session: AsyncSession, event: OutboxEvent) -> None:
    """Record shop creation for the activation funnel (master spec section 119).

    Product analytics has no destination configured yet, so this only logs. It
    is a real handler for a real event, not a placeholder for a missing feature.
    """
    log.info(
        "activation: shop created",
        extra={"event": "shop_created", "tenant_id": event.payload.get("tenant_id")},
    )


@register_handler(OutboxTopic.USER_SIGNED_IN)
async def _on_user_signed_in(_session: AsyncSession, event: OutboxEvent) -> None:
    log.info(
        "activation: user signed in",
        extra={
            "event": "otp_verified",
            "is_new_user": event.payload.get("is_new_user"),
        },
    )


@register_handler(OutboxTopic.IMPORT_COMMIT_REQUESTED)
async def _on_import_commit_requested(session: AsyncSession, event: OutboxEvent) -> None:
    """Commit a large import in the worker.

    The request marked the batch COMMITTING and enqueued this inside its own
    transaction, so the job exists if and only if the request committed. The
    commit itself is idempotent — it only ever selects rows still READY or
    WARNING — so a redelivered event finds nothing left to do rather than
    creating a second copy of anything.
    """
    import_id = event.payload.get("import_id")
    if not import_id:
        return

    from app.imports.service import build_import_service

    service = build_import_service(session)
    await service.commit(uuid.UUID(str(import_id)))


async def dispatch_outbox(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Claim and process a batch of outbox events.

    Runs in a system session: the outbox spans tenants by design, and each event
    carries the tenant it belongs to, which is installed into the context before
    its handler runs so handler queries stay scoped.
    """
    settings = get_settings()
    worker_id = str((ctx or {}).get("job_id") or f"worker-{uuid.uuid4().hex[:8]}")

    processed = failed = skipped = 0

    async with system_session("worker: outbox dispatch") as session:
        events = await claim_batch(
            session, worker_id=worker_id, batch_size=settings.outbox_batch_size
        )

        for event in events:
            handler = HANDLERS.get(event.topic)
            if handler is None:
                # Release it so the handler's own release picks it up, instead
                # of discarding a side effect that was promised to a seller.
                event.status = "PENDING"
                event.locked_at = None
                event.locked_by = None
                skipped += 1
                log.warning(
                    "outbox topic has no handler yet",
                    extra={"topic": event.topic, "outbox_event_id": str(event.id)},
                )
                continue

            token = set_context(
                RequestContext(
                    trace_id=event.trace_id or uuid.uuid4().hex,
                    tenant_id=event.tenant_id,
                    actor_type=ActorType.SYSTEM,
                    job_name=f"outbox:{event.topic}",
                )
            )
            try:
                with log_duration(log, "outbox.handle", topic=event.topic):
                    await handler(session, event)
                await mark_done(session, event)
                processed += 1
            except Exception as exc:
                await mark_failed(
                    session,
                    event,
                    f"{type(exc).__name__}: {exc}",
                    max_attempts=settings.outbox_max_attempts,
                )
                failed += 1
            finally:
                clear_context(token)

    return {"processed": processed, "failed": failed, "skipped": skipped}
