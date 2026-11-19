"""The manual call queue (V4 WP2.3).

Until Phase 3 brings the IVR, every confirmation the policy wants a call for
lands with the seller: the queue lists ``SELLER_REVIEW`` rows and, while voice
is off, ``CALL_QUEUED`` ones, highest COD first. The seller taps a row, the
phone dials (``tel:``; no call-log permission), and on return the seller says
what the customer said.

The state machine has no move for a manual call, so the seller's own call is
recorded as a ``MANUAL_CALL`` attempt and the answer goes through the moves
the seller already has: a queued row first goes ``CALL_QUEUED → SELLER_REVIEW``
(``VOICE_UNAVAILABLE``), then ``SELLER_CONFIRM`` / ``SELLER_CANCEL``. A
"Dhoreni" (no answer) closes the attempt and leaves the row where it is.

Dialing reveals the customer's number to a staff member, so ``call_now`` only
answers for an order that is in the queue, honours the shop's quiet hours
unless the seller overrides them (audited), writes a phone-reveal audit entry
per call and is rate-limited per user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.confirmations.models import (
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationMethod,
    ConfirmationState,
    OrderConfirmation,
)
from app.confirmations.service import ConfirmationService, latest_for_order, open_for_order
from app.confirmations.state_machine import Cause
from app.core.clock import DHAKA, utc_now
from app.core.errors import ConflictError, RateLimitedError
from app.orders.models import Order

if TYPE_CHECKING:
    from app.customers.service import CustomerService
    from app.orders.service import OrderService

__all__ = [
    "CALLS_PER_WINDOW",
    "AttemptStatus",
    "ManualCallService",
    "ManualOutcome",
    "QueueEntry",
    "QuietHours",
    "queue",
    "queue_states",
    "voice_available",
]

S = ConfirmationState

#: Numbers one user may dial from the queue per :data:`CALL_WINDOW_SECONDS`.
#: Far above a person working a queue; it stops a script reading the shop's
#: customer numbers out through the call button.
CALLS_PER_WINDOW = 30
CALL_WINDOW_SECONDS = 600
#: The audit reason on the phone reveal that dialing makes.
CALL_REVEAL_REASON = "confirmation_call"


class ManualOutcome(StrEnum):
    """What the seller says the customer answered."""

    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    NO_ANSWER = "NO_ANSWER"


class AttemptStatus(StrEnum):
    """``confirmation_attempts.status`` for the seller's own call."""

    STARTED = "STARTED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    NO_ANSWER = "NO_ANSWER"


def voice_available() -> bool:
    """Whether the IVR can place calls. Always ``False`` until Phase 3 (WP3.1)
    adds the ``VoiceTransport``; then queued rows leave the manual queue."""
    return False


def queue_states() -> tuple[ConfirmationState, ...]:
    if voice_available():
        return (S.SELLER_REVIEW,)
    return (S.SELLER_REVIEW, S.CALL_QUEUED)


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True, slots=True)
class QuietHours:
    """The shop's no-call window, Asia/Dhaka wall time. ``start > end`` wraps
    midnight (the default is 21:00–09:00); ``start == end`` is no window."""

    start: str
    end: str

    @classmethod
    def from_rules(cls, rules: Any) -> QuietHours:
        return cls(start=str(rules["quiet_start"]), end=str(rules["quiet_end"]))

    def active(self, now: datetime) -> bool:
        start, end = _minutes(self.start), _minutes(self.end)
        local = now.astimezone(DHAKA)
        moment = local.hour * 60 + local.minute
        if start == end:
            return False
        if start < end:
            return start <= moment < end
        return moment >= start or moment < end

    def ends_at(self, now: datetime) -> datetime | None:
        """When the current window closes (UTC), or ``None`` outside it."""
        if not self.active(now):
            return None
        local = now.astimezone(DHAKA)
        end = _minutes(self.end)
        closes = local.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if closes <= local:
            closes += timedelta(days=1)
        return closes.astimezone(UTC)

    def as_dict(self, now: datetime) -> dict[str, Any]:
        ends = self.ends_at(now)
        return {
            "start": self.start,
            "end": self.end,
            "active": ends is not None,
            "ends_at": ends.isoformat() if ends is not None else None,
        }


@dataclass(frozen=True, slots=True)
class QueueEntry:
    confirmation: OrderConfirmation
    order: Order
    #: The seller's latest call on this confirmation, if any.
    last_call: ConfirmationAttempt | None
    manual_calls: int


async def queue(db: AsyncSession, *, limit: int = 100) -> list[QueueEntry]:
    """The shop's call queue: COD high to low, then the oldest order first."""
    rows = (
        await db.execute(
            sa.select(OrderConfirmation, Order)
            .join(Order, Order.id == OrderConfirmation.order_id)
            .where(
                OrderConfirmation.state.in_([str(state) for state in queue_states()]),
                Order.deleted_at.is_(None),
            )
            .order_by(
                Order.cod_amount_paisa.desc(),
                Order.created_at.asc(),
                OrderConfirmation.id.asc(),
            )
            .limit(limit)
        )
    ).all()
    calls: dict[uuid.UUID, list[ConfirmationAttempt]] = {}
    if rows:
        attempts = (
            await db.scalars(
                sa.select(ConfirmationAttempt)
                .where(
                    ConfirmationAttempt.confirmation_id.in_([row.id for row, _ in rows]),
                    ConfirmationAttempt.channel == str(AttemptChannel.MANUAL_CALL),
                )
                .order_by(ConfirmationAttempt.attempt_no)
            )
        ).all()
        for attempt in attempts:
            calls.setdefault(attempt.confirmation_id, []).append(attempt)
    return [
        QueueEntry(
            confirmation=row,
            order=order,
            last_call=calls[row.id][-1] if calls.get(row.id) else None,
            manual_calls=len(calls.get(row.id, [])),
        )
        for row, order in rows
    ]


@dataclass(frozen=True, slots=True)
class CallStarted:
    confirmation: OrderConfirmation
    attempt: ConfirmationAttempt
    #: The number to dial. Returned once, to the caller; never stored or logged.
    phone: str
    quiet_override: bool


class ManualCallService:
    """The seller's side of the call queue."""

    def __init__(
        self, session: AsyncSession, *, orders: OrderService, customers: CustomerService
    ) -> None:
        self._db = session
        self._orders = orders
        self._customers = customers
        self._confirmations = ConfirmationService(session, orders=orders)

    async def call_now(
        self, order_id: uuid.UUID, *, actor_id: uuid.UUID, override_quiet: bool = False
    ) -> CallStarted:
        """Open a ``MANUAL_CALL`` attempt and hand back the number to dial."""
        from app.common.cache import RateLimiter
        from app.delivery_rules.service import DeliveryRulesService

        order = await self._orders.get(order_id)  # 404 for another shop's order
        row = await self._queued(order)
        now = utc_now()
        quiet = QuietHours.from_rules(await DeliveryRulesService(self._db).get(order.tenant_id))
        ends = quiet.ends_at(now)
        if ends is not None and not override_quiet:
            raise ConflictError(
                "It is the shop's quiet hours; call later or override",
                message_bn="এখন দোকানের কল না করার সময়; পরে কল করুন বা ওভাররাইড দিন",
                details={
                    "code": "CONFIRMATION_QUIET_HOURS",
                    "quiet_start": quiet.start,
                    "quiet_end": quiet.end,
                    "ends_at": ends.isoformat(),
                },
            )
        if order.customer_id is None:
            raise ConflictError(
                "This order has no customer number to call",
                message_bn="এই অর্ডারে কল করার মতো নম্বর নেই",
                details={"code": "CUSTOMER_PHONE_MISSING"},
            )
        limited = await RateLimiter().hit(
            "confirmation-call",
            str(actor_id),
            limit=CALLS_PER_WINDOW,
            window_seconds=CALL_WINDOW_SECONDS,
        )
        if not limited.allowed:
            raise RateLimitedError(retry_after_seconds=limited.retry_after_seconds)

        await self._take_from_voice_queue(row, actor_id=actor_id, now=now)
        attempt = await self._open_call(row)
        if attempt is None:  # dialing again before saying how the last call went
            attempt = await self._confirmations.add_attempt(
                row, AttemptChannel.MANUAL_CALL, now=now
            )
        override = ends is not None
        await record_audit(
            self._db,
            AuditAction.CONFIRMATION_CALL_STARTED,
            entity_type="order_confirmation",
            entity_id=row.id,
            reason="quiet hours overridden" if override else None,
            context={
                "order_id": str(order.id),
                "attempt_no": attempt.attempt_no,
                "quiet_override": override,
            },
        )
        phone = await self._customers.reveal_phone(order.customer_id, reason=CALL_REVEAL_REASON)
        await self._db.flush()
        return CallStarted(row, attempt, phone, override)

    async def record(
        self, order_id: uuid.UUID, outcome: ManualOutcome, *, actor_id: uuid.UUID
    ) -> OrderConfirmation:
        """The seller's answer: confirm, cancel, or nobody picked up."""
        order = await self._orders.get(order_id)
        if outcome is not ManualOutcome.NO_ANSWER:
            done = await self._already(order, outcome)
            if done is not None:  # a retried tap after the answer landed
                return done
        row = await self._queued(order)
        now = utc_now()
        await self._take_from_voice_queue(row, actor_id=actor_id, now=now)
        call = await self._open_call(row)
        if outcome is ManualOutcome.NO_ANSWER:
            if call is None:  # the seller dialed from outside the app
                call = await self._confirmations.add_attempt(
                    row, AttemptChannel.MANUAL_CALL, now=now
                )
            self._close(call, AttemptStatus.NO_ANSWER, now)
            await self._db.flush()
            return row
        method = ConfirmationMethod.MANUAL_CALL if call is not None else ConfirmationMethod.SELLER
        if call is not None:
            self._close(call, AttemptStatus(str(outcome)), now)
        confirmed = outcome is ManualOutcome.CONFIRMED
        return await self._confirmations.transition(
            row,
            S.CONFIRMED if confirmed else S.CANCELLED,
            Cause.SELLER_CONFIRM if confirmed else Cause.SELLER_CANCEL,
            now=now,
            actor_id=actor_id,
            seller_method=method,
        )

    # ----------------------------------------------------------- helpers ---

    async def _queued(self, order: Order) -> OrderConfirmation:
        row = await open_for_order(self._db, order.id, lock=True)
        if row is None or row.confirmation_state not in queue_states():
            raise ConflictError(
                "This order is not waiting in the call queue",
                message_bn="এই অর্ডারটি কল তালিকায় নেই",
                details={
                    "code": "CONFIRMATION_NOT_IN_QUEUE",
                    "state": row.state if row is not None else None,
                },
            )
        return row

    async def _already(self, order: Order, outcome: ManualOutcome) -> OrderConfirmation | None:
        if await open_for_order(self._db, order.id) is not None:
            return None
        latest = await latest_for_order(self._db, order.id)
        if latest is None or latest.state != str(outcome) or latest.resolved_by is None:
            return None
        return latest

    async def _take_from_voice_queue(
        self, row: OrderConfirmation, *, actor_id: uuid.UUID, now: datetime
    ) -> None:
        if row.confirmation_state is S.CALL_QUEUED:
            await self._confirmations.transition(
                row, S.SELLER_REVIEW, Cause.VOICE_UNAVAILABLE, now=now, actor_id=actor_id
            )

    async def _open_call(self, row: OrderConfirmation) -> ConfirmationAttempt | None:
        return await self._db.scalar(
            sa.select(ConfirmationAttempt)
            .where(
                ConfirmationAttempt.confirmation_id == row.id,
                ConfirmationAttempt.channel == str(AttemptChannel.MANUAL_CALL),
                ConfirmationAttempt.ended_at.is_(None),
            )
            .order_by(ConfirmationAttempt.attempt_no.desc())
            .limit(1)
        )

    @staticmethod
    def _close(call: ConfirmationAttempt, status: AttemptStatus, now: datetime) -> None:
        call.status = str(status)
        call.ended_at = now
