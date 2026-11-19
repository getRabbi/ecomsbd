"""Order confirmations (V4 WP2.1).

Starting a confirmation, moving it through :mod:`app.confirmations.state_machine`,
and applying the customer's answer to the order.

Two guards keep the customer's answer from doing more than they agreed to:

*   **The order moves only if nothing changed.** A confirmation snapshots the
    order's ``version`` and COD when it starts. ``CONFIRMED`` moves the order
    ``DRAFT → CONFIRMED`` only while the order is still ``DRAFT`` at that same
    version; otherwise the answer is recorded (``order_outcome``) and the seller
    is asked to look. A decline is applied under the same guard.
*   **A material edit starts over.** Changing the items, or moving COD by more
    than 10% from the snapshot, supersedes the open confirmation and starts a
    new one: the customer has not agreed to the new order.

A customer who declines gets no flag (no risk mark, no block); that is the
seller's call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.outbox import OutboxTopic, enqueue
from app.confirmations import facts as policy_facts
from app.confirmations.models import (
    OPEN_STATES,
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationDecision,
    ConfirmationMethod,
    ConfirmationState,
    OrderConfirmation,
    OrderOutcome,
)
from app.confirmations.policy import PolicyRules, decide
from app.confirmations.state_machine import Cause, ConfirmationStateMachine
from app.core.clock import utc_now
from app.core.context import ActorType
from app.core.errors import ConflictError, NotFoundError
from app.orders.models import Order, OrderStatus

if TYPE_CHECKING:
    from app.orders.service import OrderService

__all__ = [
    "CUSTOMER_CONFIRMATION_ACTOR",
    "CUSTOMER_DECLINED",
    "LINK_TTL",
    "MAX_CALL_ATTEMPTS",
    "RETRY_AFTER",
    "ConfirmationService",
    "is_material_edit",
    "latest_for_order",
    "open_for_order",
]

S = ConfirmationState

#: The audit actor recorded when the customer's own answer moves an order.
CUSTOMER_CONFIRMATION_ACTOR = "system:customer_confirmation"
#: ``Order.cancellation_reason`` when the customer said no.
CUSTOMER_DECLINED = "CUSTOMER_DECLINED"
#: ``Order.cancellation_reason`` when the seller cancelled from the review queue.
SELLER_REVIEW_CANCELLED = "CONFIRMATION_CANCELLED_BY_SELLER"

#: Calls per confirmation (Phase 3 places them; the rule lives here).
MAX_CALL_ATTEMPTS = 2
#: Wait after an unanswered call before the next one.
RETRY_AFTER = timedelta(minutes=30)
#: An SMS confirm link is good for this long (WP2.3).
LINK_TTL = timedelta(hours=6)
#: COD moved by more than this, in basis points of the snapshot, is material.
COD_CHANGE_BPS = 1_000

#: Customer answers: the order moves with the system actor.
_CUSTOMER_CAUSES = frozenset(
    {
        Cause.AUTO_TRUSTED,
        Cause.DTMF_CONFIRM,
        Cause.DTMF_CANCEL,
        Cause.LINK_CONFIRM,
        Cause.LINK_CANCEL,
    }
)
_DECLINES = frozenset({Cause.DTMF_CANCEL, Cause.LINK_CANCEL, Cause.SELLER_CANCEL})


def is_material_edit(confirmation: OrderConfirmation, order: Order, *, items_changed: bool) -> bool:
    """Items changed, or COD moved by more than 10% from the snapshot.

    Measured from the snapshot, not the previous edit, so several small edits
    cannot add up to a different order unnoticed. Integer arithmetic only.
    """
    if items_changed:
        return True
    before = confirmation.cod_paisa_snapshot
    delta = abs(order.cod_amount_paisa - before)
    return delta * 10_000 > before * COD_CHANGE_BPS


async def open_for_order(
    db: AsyncSession, order_id: uuid.UUID, *, lock: bool = False
) -> OrderConfirmation | None:
    stmt = sa.select(OrderConfirmation).where(
        OrderConfirmation.order_id == order_id,
        OrderConfirmation.state.in_([str(state) for state in OPEN_STATES]),
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalars().first()


async def latest_for_order(db: AsyncSession, order_id: uuid.UUID) -> OrderConfirmation | None:
    return (
        (
            await db.execute(
                sa.select(OrderConfirmation)
                .where(OrderConfirmation.order_id == order_id)
                .order_by(OrderConfirmation.created_at.desc(), OrderConfirmation.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


#: What a new order may ask for as it is created (the quick-order sheet's
#: "Confirm & call customer" and "Already confirmed in chat").
CONFIRMATION_MODES = frozenset({"REQUEST", "SELLER_CHAT"})


async def start_for_new_order(
    db: AsyncSession,
    orders: OrderService,
    order: Order,
    mode: str | None,
    *,
    actor_id: uuid.UUID | None,
) -> OrderConfirmation | None:
    """Start the confirmation a new order asked for, if the engine is on.

    With ``confirmation_engine_enabled`` off, or no mode, nothing happens and
    the order stands as a plain order: an offline create replayed after the
    flag changed must still land. Idempotent, like the service calls.
    """
    from app.common.feature_flags import FeatureFlagService, FlagKey

    if mode not in CONFIRMATION_MODES or not await FeatureFlagService(db).is_enabled(
        FlagKey.CONFIRMATION_ENGINE_ENABLED, tenant_id=order.tenant_id
    ):
        return None
    service = ConfirmationService(db, orders=orders)
    if mode == "SELLER_CHAT" and actor_id is not None:
        current = await open_for_order(db, order.id)
        if current is not None:  # asked to call first, then said "already confirmed"
            return current
        return (await service.resolve_in_chat(order.id, actor_id=actor_id)).confirmation
    return (await service.start(order.id, requested_by=actor_id)).confirmation


@dataclass(frozen=True, slots=True)
class Requested:
    confirmation: OrderConfirmation
    #: False when an earlier request already started (or ended) one.
    created: bool


class ConfirmationService:
    """Writes confirmations. The order moves only through ``OrderService``."""

    def __init__(self, session: AsyncSession, *, orders: OrderService) -> None:
        self._db = session
        self._orders = orders

    # ------------------------------------------------------------ start ---

    async def start(self, order_id: uuid.UUID, *, requested_by: uuid.UUID | None) -> Requested:
        """Start a confirmation and let the policy decide what happens next.

        The entry point for every caller: :meth:`request` plus the policy
        (WP2.2) on a ``PENDING`` row it has not decided yet. Idempotent: a
        repeat returns the row as it is, decided once.
        """
        result = await self.request(order_id, requested_by=requested_by)
        row = result.confirmation
        if row.confirmation_state is S.PENDING and row.decision is None:
            await self.decide(row)
        return result

    async def decide(self, row: OrderConfirmation) -> OrderConfirmation:
        """Run the policy on a ``PENDING`` row and move it out of ``PENDING``.

        The decision, its reason code and the facts and thresholds it read are
        stored on the row first, so the outbox event and the "why" display
        carry them.
        """
        from app.delivery_rules.service import DeliveryRulesService

        order = await self._orders.get(row.order_id)
        facts = await policy_facts.collect(self._db, order)
        rules = PolicyRules.from_values(await DeliveryRulesService(self._db).get(order.tenant_id))
        decision, reason = decide(facts, rules)
        row.decision = str(decision)
        row.reason_code = str(reason)
        row.facts = {**facts.as_dict(), "rules": rules.as_dict()}
        await self._db.flush()
        if decision is ConfirmationDecision.AUTO_CONFIRM:
            return await self.transition(row, S.CONFIRMED, Cause.AUTO_TRUSTED)
        if decision is ConfirmationDecision.CALL:
            return await self.transition(row, S.CALL_QUEUED, Cause.DECIDED_CALL)
        return await self.transition(row, S.SELLER_REVIEW, Cause.DECIDED_REVIEW)

    async def request(self, order_id: uuid.UUID, *, requested_by: uuid.UUID | None) -> Requested:
        """Open a ``PENDING`` confirmation for a ``DRAFT`` order, once.

        The bare step: the policy has not run. Callers use :meth:`start`.
        Idempotent per order: a second request (an offline replay, a double
        tap) returns the confirmation that is open, or the one that already
        ended, instead of calling the customer twice.
        """
        order = await self._orders.get(order_id)
        existing = await open_for_order(self._db, order.id) or await latest_for_order(
            self._db, order.id
        )
        if existing is not None:
            return Requested(existing, created=False)
        self._require_draft(order)
        return Requested(await self._start(order, requested_by=requested_by), created=True)

    async def resolve_in_chat(self, order_id: uuid.UUID, *, actor_id: uuid.UUID) -> Requested:
        """ "Already confirmed in chat": the seller says the customer agreed.

        The confirmation is created already ``CONFIRMED`` with method
        ``SELLER_CHAT``; no call or link is made. Idempotent like
        :meth:`request`.
        """
        order = await self._orders.get(order_id)
        current = await open_for_order(self._db, order.id)
        if current is not None:
            raise ConflictError(
                "A confirmation is already running for this order",
                details={"code": "CONFIRMATION_IN_PROGRESS", "state": current.state},
            )
        latest = await latest_for_order(self._db, order.id)
        if latest is not None:
            return Requested(latest, created=False)
        self._require_draft(order)
        now = utc_now()
        row = OrderConfirmation(
            order_id=order.id,
            state=str(S.CONFIRMED),
            method=str(ConfirmationMethod.SELLER_CHAT),
            reason_code=str(ConfirmationMethod.SELLER_CHAT),
            requested_by=actor_id,
            resolved_at=now,
            resolved_by=actor_id,
            order_version=order.version,
            cod_paisa_snapshot=order.cod_amount_paisa,
        )
        self._db.add(row)
        await self._db.flush()
        await self._audit(row, None, S.CONFIRMED, Cause.SELLER_CONFIRM)
        await self._finish(row, Cause.SELLER_CONFIRM, now=now, actor_id=actor_id)
        await self._db.flush()
        return Requested(row, created=True)

    async def _start(
        self,
        order: Order,
        *,
        requested_by: uuid.UUID | None,
        replaces: OrderConfirmation | None = None,
    ) -> OrderConfirmation:
        row = OrderConfirmation(
            order_id=order.id,
            state=str(S.PENDING),
            requested_by=requested_by,
            order_version=order.version,
            cod_paisa_snapshot=order.cod_amount_paisa,
        )
        self._db.add(row)
        await self._db.flush()
        await record_audit(
            self._db,
            AuditAction.CONFIRMATION_REQUESTED,
            entity_type="order_confirmation",
            entity_id=row.id,
            context={
                "order_id": str(order.id),
                "replaces": str(replaces.id) if replaces is not None else None,
            },
        )
        return row

    @staticmethod
    def _require_draft(order: Order) -> None:
        if order.order_status is not OrderStatus.DRAFT:
            raise ConflictError(
                "Only an order that is not confirmed yet can be confirmed by the customer",
                details={"code": "ORDER_NOT_DRAFT", "status": order.status},
            )

    # ------------------------------------------------------------- move ---

    async def transition(
        self,
        confirmation: OrderConfirmation,
        target: ConfirmationState,
        cause: Cause,
        *,
        now: datetime | None = None,
        actor_id: uuid.UUID | None = None,
        seller_method: ConfirmationMethod | None = None,
        next_action_at: datetime | None = None,
    ) -> OrderConfirmation:
        """Move ``confirmation`` (locked by the caller) one step."""
        moment = now or utc_now()
        current = confirmation.confirmation_state
        ConfirmationStateMachine.check(current, target, cause)
        method = (
            ConfirmationStateMachine.method_for(cause, seller_method)
            if target.is_terminal
            else None
        )
        if cause is Cause.RETRY:
            self._check_retry(confirmation, moment)

        confirmation.state = str(target)
        confirmation.next_action_at = self._next_action(target, moment, next_action_at)
        if target.is_terminal:
            confirmation.method = str(method) if method is not None else None
            confirmation.resolved_at = moment
            confirmation.resolved_by = (
                actor_id if cause in (Cause.SELLER_CONFIRM, Cause.SELLER_CANCEL) else None
            )
        await self._db.flush()
        await self._audit(confirmation, current, target, cause)
        await self._finish(confirmation, cause, now=moment, actor_id=actor_id)
        await self._db.flush()  # the session does not autoflush
        return confirmation

    @staticmethod
    def _check_retry(confirmation: OrderConfirmation, now: datetime) -> None:
        if confirmation.attempt_count >= MAX_CALL_ATTEMPTS:
            raise ConflictError(
                "No calls left for this confirmation",
                details={"code": "CONFIRMATION_ATTEMPTS_EXHAUSTED"},
            )
        if confirmation.next_action_at is not None and now < confirmation.next_action_at:
            raise ConflictError(
                "Too early to call again",
                details={
                    "code": "CONFIRMATION_RETRY_TOO_EARLY",
                    "next_action_at": confirmation.next_action_at.isoformat(),
                },
            )

    @staticmethod
    def _next_action(
        target: ConfirmationState, now: datetime, requested: datetime | None
    ) -> datetime | None:
        if target is S.NO_ANSWER:
            return now + RETRY_AFTER
        if target is S.SMS_LINK_SENT:
            return now + LINK_TTL
        if target is S.CALL_QUEUED:
            return requested or now
        return None

    async def add_attempt(
        self,
        confirmation: OrderConfirmation,
        channel: AttemptChannel,
        *,
        provider: str | None = None,
        now: datetime | None = None,
    ) -> ConfirmationAttempt:
        """Record a call or link about to be made (before the provider call)."""
        if confirmation.confirmation_state.is_terminal:
            raise ConflictError(
                "This confirmation has ended",
                details={"code": "CONFIRMATION_CLOSED", "state": confirmation.state},
            )
        confirmation.attempt_count += 1
        attempt = ConfirmationAttempt(
            confirmation_id=confirmation.id,
            attempt_no=confirmation.attempt_count,
            channel=str(channel),
            provider=provider,
            started_at=now or utc_now(),
        )
        self._db.add(attempt)
        await self._db.flush()
        return attempt

    # -------------------------------------------------------- order hooks ---

    async def on_order_edited(self, order: Order, *, items_changed: bool) -> None:
        """A material edit supersedes the open confirmation; a new one starts."""
        current = await open_for_order(self._db, order.id, lock=True)
        if current is None or not is_material_edit(current, order, items_changed=items_changed):
            return
        await self.transition(current, S.SUPERSEDED, Cause.ORDER_EDITED)
        if order.order_status is not OrderStatus.DRAFT:
            return
        successor = await self._start(order, requested_by=current.requested_by, replaces=current)
        current.superseded_by = successor.id
        await self._db.flush()
        await self.decide(successor)

    async def on_order_cancelled(self, order: Order) -> None:
        current = await open_for_order(self._db, order.id, lock=True)
        if current is not None:
            await self.transition(current, S.CANCELLED, Cause.ORDER_CANCELLED)

    # ---------------------------------------------------------- effects ---

    async def _finish(
        self,
        row: OrderConfirmation,
        cause: Cause,
        *,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        state = row.confirmation_state
        if state is S.CONFIRMED:
            await self._apply(row, cause, OrderStatus.CONFIRMED, actor_id=actor_id)
            await self._emit(OutboxTopic.CONFIRMATION_CONFIRMED, row, cause)
        elif state is S.CANCELLED:
            if cause in _DECLINES:
                await self._apply(row, cause, OrderStatus.CANCELLED, actor_id=actor_id)
            await self._emit(OutboxTopic.CONFIRMATION_CANCELLED, row, cause)
        elif state is S.NO_ANSWER:
            await self._emit(OutboxTopic.CONFIRMATION_NO_ANSWER, row, cause)
        elif state is S.SELLER_REVIEW:
            if cause is Cause.DTMF_CALLBACK:
                await self._emit(OutboxTopic.CONFIRMATION_CALLBACK_REQUESTED, row, cause)
            await self._emit(OutboxTopic.CONFIRMATION_REVIEW_NEEDED, row, cause)

    async def _apply(
        self,
        row: OrderConfirmation,
        cause: Cause,
        target: OrderStatus,
        *,
        actor_id: uuid.UUID | None,
    ) -> None:
        order = await self._db.get(Order, row.order_id)
        if order is None:  # the order is RESTRICT-protected; never expected
            raise NotFoundError("Order not found")
        if order.order_status is not OrderStatus.DRAFT:
            row.order_outcome = str(OrderOutcome.ORDER_NOT_DRAFT)
        elif order.version != row.order_version:
            row.order_outcome = str(OrderOutcome.ORDER_CHANGED)
        else:
            row.order_outcome = str(OrderOutcome.APPLIED)
            by_customer = cause in _CUSTOMER_CAUSES
            await self._orders.transition(
                order.id,
                target,
                reason=self._cancel_reason(row) if target is OrderStatus.CANCELLED else None,
                actor_type=ActorType.SYSTEM if by_customer else None,
                actor_id=None if by_customer else actor_id,
                actor_label=CUSTOMER_CONFIRMATION_ACTOR if by_customer else None,
            )
        await self._db.flush()

    @staticmethod
    def _cancel_reason(row: OrderConfirmation) -> str:
        if row.method == ConfirmationMethod.SELLER:
            return SELLER_REVIEW_CANCELLED
        # IVR, SMS link, the seller's own call or the chat: the customer said no.
        return CUSTOMER_DECLINED

    async def _emit(self, topic: OutboxTopic, row: OrderConfirmation, cause: Cause) -> None:
        # Dedupe per confirmation, state and attempt: NO_ANSWER can happen once
        # per call, everything else once per confirmation.
        await enqueue(
            self._db,
            topic,
            self.payload(row, cause),
            dedupe_key=f"{topic}:{row.id}:{row.attempt_count}",
        )

    @staticmethod
    def payload(row: OrderConfirmation, cause: Cause | None = None) -> dict[str, Any]:
        """The outbox payload. Ids and codes only: no phone, name or text."""
        return {
            "confirmation_id": str(row.id),
            "order_id": str(row.order_id),
            "state": row.state,
            "cause": str(cause) if cause is not None else None,
            "method": row.method,
            "decision": row.decision,
            "reason_code": row.reason_code,
            "order_outcome": row.order_outcome,
            "attempt_count": row.attempt_count,
        }

    async def _audit(
        self,
        row: OrderConfirmation,
        current: ConfirmationState | None,
        target: ConfirmationState,
        cause: Cause | None,
    ) -> None:
        await record_audit(
            self._db,
            AuditAction.CONFIRMATION_STATE_CHANGED,
            entity_type="order_confirmation",
            entity_id=row.id,
            context={
                "order_id": str(row.order_id),
                "from": str(current) if current is not None else None,
                "to": str(target),
                "cause": str(cause) if cause is not None else None,
                "method": row.method,
            },
        )
