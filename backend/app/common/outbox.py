"""Durable transactional outbox.

Master spec section 41: any domain transaction that must trigger external work
writes the business row **and** an outbox event in the same database
transaction. A worker then claims events with ``FOR UPDATE SKIP LOCKED``.

The point is that "order booked" and "send the tracking SMS" can never disagree.
Either both are committed or neither is; there is no window where the parcel is
booked but the follow-up work was lost because a process died, and no window
where the SMS is sent for an order that rolled back.

Delivery is at-least-once, so every consumer must be idempotent (section 78).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.core.context import current_context
from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.base import Base
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = [
    "OutboxEvent",
    "OutboxStatus",
    "OutboxTopic",
    "claim_batch",
    "enqueue",
    "mark_dead",
    "mark_done",
    "mark_failed",
]

log = get_logger(__name__)

#: Backoff schedule for a failed event, in seconds, indexed by attempt count.
_BACKOFF_SECONDS = (10, 30, 90, 300, 900, 1800, 3600)


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    #: Exhausted retries. Requires operator attention; never silently dropped.
    DEAD = "DEAD"


class OutboxTopic(StrEnum):
    """Known event topics.

    Declared centrally so a consumer and a producer cannot drift apart on a
    string literal. Topics for later phases are listed here as the contract;
    their handlers ship with the phase that owns them.
    """

    # Phase A
    TENANT_CREATED = "tenant.created"
    USER_SIGNED_IN = "user.signed_in"
    AUDIT_RECORDED = "audit.recorded"

    # Commerce core (phase B)
    ORDER_CREATED = "order.created"
    ORDER_STATUS_CHANGED = "order.status_changed"
    IMPORT_COMMITTED = "import.committed"
    #: A large import asked to be committed in the background. Enqueued inside
    #: the request's transaction, so a request that rolls back never leaves an
    #: orphan job behind — which is the whole reason this goes through the
    #: outbox rather than straight to the queue.
    IMPORT_COMMIT_REQUESTED = "import.commit_requested"

    # Later phases (master spec sections 41, 42)
    ORDER_BOOKED = "order.booked"
    BOOKING_UNKNOWN_DETECTED = "booking.unknown_detected"
    CONSIGNMENT_STATUS_CHANGED = "consignment.status_changed"
    COD_RECEIVABLE_ELIGIBLE = "cod.receivable_eligible"
    PAYOUT_IMPORTED = "payout.imported"
    RECONCILIATION_COMPLETED = "reconciliation.completed"
    COD_OVERDUE_DETECTED = "cod.overdue_detected"
    SUBSCRIPTION_RENEWED = "subscription.renewed"
    FRIDAY_SUMMARY_DUE = "summary.friday_due"

    # Integrations Hub two-way sync (V3.2). Public webhook topics of the same name.
    ORDER_UPDATED = "order.updated"
    INVENTORY_CHANGED = "inventory.updated"
    PRODUCT_UPDATED = "product.updated"

    # Automation Pro (V3.4): facts workflows react to. Consumed when written
    # (app.automation.triggers); their outbox handlers are no-ops.
    COD_SETTLED = "cod.settled"
    ALERT_RAISED = "alert.raised"
    INTEGRATION_ISSUE_OPENED = "integration.issue_opened"
    CUSTOMER_REPLIED = "customer.replied"
    FOLLOWUP_COMPLETED = "followup.completed"
    SEGMENT_ENTERED = "customer.segment_entered"

    # Inventory + procurement (V3.5): facts workflows react to. Consumed when
    # written (app.automation.triggers); their outbox handlers are no-ops.
    PURCHASE_ORDER_ORDERED = "purchase_order.ordered"
    PURCHASE_ORDER_RECEIVED = "purchase_order.received"
    STOCK_TRANSFER_COMPLETED = "stock_transfer.completed"

    # Forecasting (V3.6): an item newly predicted to run out within its lead
    # time. Consumed when written (app.automation.triggers); no-op handler.
    STOCKOUT_PREDICTED = "forecast.stockout_predicted"

    # External risk and first-party risk signals (V3.7). Consumed when written
    # (app.automation.triggers); their outbox handlers are no-ops.
    EXTERNAL_RISK_LOOKUP_COMPLETED = "external_risk.lookup_completed"
    EXTERNAL_RISK_UNAVAILABLE = "external_risk.provider_unavailable"


class OutboxEvent(Base):
    """One durable side effect awaiting delivery.

    Not a :class:`~app.db.base.TenantOwned` model: system events legitimately
    have no tenant, and the worker reads across tenants from a system session.
    ``tenant_id`` is still recorded and indexed so an event can be traced back.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        sa.Index("ix_outbox_events_claimable", "status", "available_at"),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_events_dedupe_key"),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    topic: Mapped[str] = mapped_column(sa.String(120), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: Optional producer-supplied key making enqueue itself idempotent.
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=OutboxStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)

    locked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)

    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    def next_available_at(self) -> datetime:
        """Exponential backoff with jitter, so retries do not synchronise."""
        index = min(self.attempts, len(_BACKOFF_SECONDS) - 1)
        base = _BACKOFF_SECONDS[index]
        jitter = random.uniform(0, base * 0.25)  # noqa: S311 - scheduling, not crypto
        return utc_now() + timedelta(seconds=base + jitter)


async def enqueue(
    session: AsyncSession,
    topic: OutboxTopic | str,
    payload: dict[str, Any],
    *,
    tenant_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    available_at: datetime | None = None,
) -> OutboxEvent:
    """Add an event to the outbox **inside the caller's transaction**.

    Deliberately does not commit: the whole guarantee comes from sharing the
    caller's transaction. A caller that commits separately has reintroduced the
    dual-write problem this table exists to remove.
    """
    context = current_context()
    event = OutboxEvent(
        id=new_id(),
        tenant_id=tenant_id if tenant_id is not None else context.tenant_id,
        topic=str(topic),
        payload=payload,
        dedupe_key=dedupe_key,
        available_at=available_at or utc_now(),
        trace_id=context.trace_id,
    )
    session.add(event)
    from app.public_api.webhooks import schedule_event

    await schedule_event(session, event)
    from app.automation.service import schedule_event as schedule_automation

    await schedule_automation(session, event)
    return event


async def claim_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    stale_lock_seconds: int = 300,
) -> list[OutboxEvent]:
    """Atomically claim up to ``batch_size`` due events.

    On PostgreSQL this uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers
    never contend for the same row. SQLite has no such clause; the fallback
    relies on the single-writer lock, which is correct for local development and
    the test suite where only one worker runs.

    Events left ``PROCESSING`` by a crashed worker become claimable again after
    ``stale_lock_seconds``, so a hard kill cannot strand a side effect forever.
    """
    now = utc_now()
    stale_before = now - timedelta(seconds=stale_lock_seconds)

    claimable = sa.or_(
        sa.and_(
            OutboxEvent.status == OutboxStatus.PENDING,
            OutboxEvent.available_at <= now,
        ),
        sa.and_(
            OutboxEvent.status == OutboxStatus.PROCESSING,
            OutboxEvent.locked_at.is_not(None),
            OutboxEvent.locked_at < stale_before,
        ),
    )

    select_stmt = (
        sa.select(OutboxEvent)
        .where(claimable)
        .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
        .limit(batch_size)
    )

    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        select_stmt = select_stmt.with_for_update(skip_locked=True)

    events = list((await session.execute(select_stmt)).scalars().all())
    for event in events:
        event.status = OutboxStatus.PROCESSING
        event.locked_at = now
        event.locked_by = worker_id
    await session.flush()
    return events


async def mark_done(session: AsyncSession, event: OutboxEvent) -> None:
    event.status = OutboxStatus.DONE
    event.processed_at = utc_now()
    event.locked_at = None
    event.locked_by = None
    event.last_error = None
    await session.flush()


async def mark_failed(
    session: AsyncSession, event: OutboxEvent, error: str, *, max_attempts: int
) -> None:
    """Record a failure and schedule a retry, or give up after ``max_attempts``."""
    event.attempts += 1
    event.last_error = error[:2000]
    event.locked_at = None
    event.locked_by = None
    if event.attempts >= max_attempts:
        await mark_dead(session, event, error)
        return
    event.status = OutboxStatus.PENDING
    event.available_at = event.next_available_at()
    await session.flush()


async def mark_dead(session: AsyncSession, event: OutboxEvent, error: str) -> None:
    """Park an event for operator attention. Never deleted."""
    event.status = OutboxStatus.DEAD
    event.last_error = error[:2000]
    event.locked_at = None
    event.locked_by = None
    await session.flush()
    log.error(
        "outbox event exhausted retries",
        extra={
            "outbox_event_id": str(event.id),
            "topic": event.topic,
            "attempts": event.attempts,
        },
    )
