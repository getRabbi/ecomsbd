"""Resolving a booking whose outcome was never confirmed.

Brief sections 9, 12 and 40.

A create call that timed out leaves one question: **does the parcel exist?**
Steadfast documents a way to ask — ``GET /status_by_invoice/{invoice}`` — and
ecomsbd sent an invoice it chose and persisted before the call, so the question
is answerable. That is the whole design.

What the documentation does *not* provide is the other half. It describes no
"not found" response, no error body, and no way to distinguish "there is no
parcel with that invoice" from "the lookup failed". So:

    **Existence can be proven. Absence cannot.**

Everything in this module follows from that asymmetry:

*   A status lookup that returns a ``delivery_status`` proves the parcel
    exists. The booking is recovered.
*   Anything else — a 404, a 500, a timeout, an unreadable body — proves
    nothing. The booking stays ``BOOKING_UNKNOWN`` and is asked again later.
*   After a bounded number of inconclusive attempts it goes to a person, who
    can see the evidence and decide. It is never automatically retried, and
    never automatically abandoned.

Inventing an "absence proven" rule here — treating a 404 as "safe to rebook" —
would be the single most expensive mistake available in this integration, and it
would be invisible until a seller noticed they had paid for two deliveries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import ConsignmentService
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.couriers.accounts import CourierAccountService
from app.couriers.events import record_courier_event
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import (
    BookingAttemptState,
    CourierBookingAttempt,
    CourierEventKind,
    CourierEventSource,
)
from app.couriers.steadfast.errors import SteadfastError
from app.couriers.steadfast.mapping import map_delivery_status
from app.orders.models import Order

__all__ = ["BookingRecoveryService", "RecoveryOutcome", "RecoveryResult"]

log = get_logger(__name__)


class RecoveryOutcome(StrEnum):
    """What one recovery attempt concluded."""

    #: The provider answered about this invoice, so the parcel exists.
    RECOVERED = "RECOVERED"
    #: No answer, or an answer that proves nothing. Ask again later.
    INCONCLUSIVE = "INCONCLUSIVE"
    #: Asked enough times. A person decides now.
    MANUAL_REVIEW = "MANUAL_REVIEW"
    #: The attempt was already resolved. Nothing to do.
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    #: This shop's courier account is gone or needs reconnecting, so we cannot
    #: ask. Distinct from inconclusive: waiting will not help.
    CANNOT_ASK = "CANNOT_ASK"


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    attempt_id: uuid.UUID
    consignment_id: uuid.UUID
    outcome: RecoveryOutcome
    provider_status: str | None = None
    note: str | None = None


class BookingRecoveryService:
    """Asks the provider about invoices whose bookings were never confirmed."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        accounts: CourierAccountService,
        consignments: ConsignmentService,
        settings: Settings | None = None,
    ) -> None:
        self._db = session
        self._accounts = accounts
        self._consignments = consignments
        self._settings = settings or get_settings()

    # ------------------------------------------------------------- finding --

    async def due_attempts(self, *, limit: int = 50) -> list[CourierBookingAttempt]:
        """Attempts whose next recovery check is due.

        Ordered oldest first so a backlog drains in the order it accumulated,
        and locked ``SKIP LOCKED`` on PostgreSQL so two workers running the job
        at once split the work rather than doing it twice.
        """
        now = utc_now()
        stmt = (
            sa.select(CourierBookingAttempt)
            .where(
                CourierBookingAttempt.state == str(BookingAttemptState.UNKNOWN),
                sa.or_(
                    CourierBookingAttempt.next_recovery_at.is_(None),
                    CourierBookingAttempt.next_recovery_at <= now,
                ),
            )
            .order_by(CourierBookingAttempt.started_at.asc())
            .limit(limit)
        )
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list((await self._db.execute(stmt)).scalars().all())

    # ---------------------------------------------------------- recovering --

    async def recover(self, attempt: CourierBookingAttempt) -> RecoveryResult:
        """Ask the provider about one unresolved invoice."""
        if attempt.attempt_state is not BookingAttemptState.UNKNOWN:
            return RecoveryResult(
                attempt_id=attempt.id,
                consignment_id=attempt.consignment_id,
                outcome=RecoveryOutcome.ALREADY_RESOLVED,
            )

        consignment = await self._db.get(Consignment, attempt.consignment_id)
        if consignment is None:
            raise NotFoundError("The parcel this booking attempt belongs to is gone")

        account = await self._accounts.for_provider(attempt.provider)
        if account is None or not account.is_usable:
            # We cannot ask. Recording that, rather than counting it as a
            # failed attempt, keeps the attempt budget for questions we were
            # actually able to put to the provider.
            return await self._record(
                attempt,
                consignment,
                outcome=RecoveryOutcome.CANNOT_ASK,
                note=("The courier account is not connected, so the booking could not be checked."),
                consume_attempt=False,
            )

        adapter = self._accounts.adapter_for(attempt.provider)
        if adapter is None:
            return await self._record(
                attempt,
                consignment,
                outcome=RecoveryOutcome.CANNOT_ASK,
                note="No adapter is registered for this provider.",
                consume_attempt=False,
            )

        credentials = self._accounts.credentials_for(account)
        try:
            call = await adapter.client.status_by_invoice(  # type: ignore[attr-defined]
                credentials, attempt.merchant_reference
            )
        except SteadfastError as exc:
            # Proves nothing. Note in particular that a 404 lands here: the
            # document describes no "not found" body, so a 404 is *not*
            # evidence that the parcel does not exist.
            return await self._record(
                attempt,
                consignment,
                outcome=RecoveryOutcome.INCONCLUSIVE,
                note=(
                    f"The courier did not confirm this invoice "
                    f"({exc.kind}). It is not safe to assume the parcel was "
                    f"not created."
                ),
            )
        except Exception as exc:
            log.error(
                "booking recovery raised",
                extra={
                    "provider": attempt.provider,
                    "operation": "status_by_invoice",
                    "error": type(exc).__name__,
                },
            )
            return await self._record(
                attempt,
                consignment,
                outcome=RecoveryOutcome.INCONCLUSIVE,
                note="The check could not be completed.",
            )

        raw_status = str(call.value.delivery_status)
        return await self._recovered(attempt, consignment, raw_status=raw_status, call=call)

    async def _recovered(
        self,
        attempt: CourierBookingAttempt,
        consignment: Consignment,
        *,
        raw_status: str,
        call: object,
    ) -> RecoveryResult:
        """The provider answered about this invoice, so the parcel exists.

        One thing is deliberately *not* claimed here: the provider's own
        consignment id. ``status_by_invoice`` returns a delivery status and
        nothing else, and Steadfast documents no way to obtain a consignment id
        from an invoice. So the parcel is recovered with its merchant reference
        as the handle — which is enough, because the same by-invoice lookup is
        what keeps polling it afterwards.
        """
        now = utc_now()
        mapping = map_delivery_status(raw_status)

        consignment.status = str(
            mapping.canonical if mapping.canonical is not None else ConsignmentStatus.BOOKED
        )
        consignment.provider_raw_status = raw_status
        consignment.booked_at = consignment.booked_at or attempt.started_at
        consignment.last_status_at = now
        consignment.provider_status_at = now
        consignment.next_poll_at = now + timedelta(
            minutes=self._settings.courier_poll_interval_fresh_minutes
        )
        if mapping.needs_quantity_resolution:
            consignment.needs_quantity_resolution = True

        attempt.state = str(BookingAttemptState.RECOVERED)
        attempt.recovery_attempts += 1
        attempt.last_recovery_at = now
        attempt.next_recovery_at = None
        attempt.completed_at = attempt.completed_at or now
        attempt.recovery_note = (
            f"The courier confirmed a parcel for invoice {attempt.merchant_reference} "
            f"with status '{raw_status}'."
        )
        await self._db.flush()

        # The parcel exists, so now — and only now — the stock leaves the shelf
        # and a receivable opens. This is the same call the successful booking
        # path makes, and it is idempotent.
        order = await self._db.get(Order, attempt.order_id)
        if order is not None:
            await self._consignments.fulfil_dispatch(consignment, order=order, occurred_at=now)

        await record_courier_event(
            self._db,
            provider=attempt.provider,
            kind=CourierEventKind.BOOKING,
            source=CourierEventSource.RECOVERY,
            consignment=consignment,
            raw_status=raw_status,
            correlation_id=getattr(call, "correlation_id", None),
        )
        await record_audit(
            self._db,
            AuditAction.BOOKING_RECOVERED,
            entity_type="consignment",
            entity_id=consignment.id,
            context={
                "provider": attempt.provider,
                "merchant_reference": attempt.merchant_reference,
                "raw_status": raw_status,
                "recovery_attempts": attempt.recovery_attempts,
            },
        )
        record_metric(CourierMetric.UNKNOWN_RECOVERED, provider=attempt.provider)
        log.info(
            "booking recovered from unknown",
            extra={
                "provider": attempt.provider,
                "operation": "recover",
                "merchant_reference": attempt.merchant_reference,
                "raw_status": raw_status,
            },
        )
        return RecoveryResult(
            attempt_id=attempt.id,
            consignment_id=consignment.id,
            outcome=RecoveryOutcome.RECOVERED,
            provider_status=raw_status,
            note=attempt.recovery_note,
        )

    async def _record(
        self,
        attempt: CourierBookingAttempt,
        consignment: Consignment,
        *,
        outcome: RecoveryOutcome,
        note: str,
        consume_attempt: bool = True,
    ) -> RecoveryResult:
        """Record an inconclusive check and schedule the next one.

        ``consume_attempt`` is false when we could not ask at all — a
        disconnected account. Counting that against the attempt budget would
        push a parcel into manual review for a reason that has nothing to do
        with the parcel.
        """
        now = utc_now()
        attempt.last_recovery_at = now
        if consume_attempt:
            attempt.recovery_attempts += 1
        attempt.recovery_note = note[:400]

        final_outcome = outcome
        if (
            consume_attempt
            and attempt.recovery_attempts >= self._settings.courier_recovery_max_attempts
        ):
            # Asked enough. A person looks at it now, with the evidence
            # attached. The parcel stays BOOKING_UNKNOWN — it is still unknown —
            # and the seller is still told not to rebook.
            attempt.state = str(BookingAttemptState.MANUAL_REVIEW)
            attempt.next_recovery_at = None
            final_outcome = RecoveryOutcome.MANUAL_REVIEW
            record_metric(CourierMetric.UNKNOWN_UNRESOLVED, provider=attempt.provider)
            log.warning(
                "booking recovery exhausted; sending to manual review",
                extra={
                    "provider": attempt.provider,
                    "operation": "recover",
                    "merchant_reference": attempt.merchant_reference,
                    "recovery_attempts": attempt.recovery_attempts,
                },
            )
        else:
            attempt.next_recovery_at = now + self._backoff(attempt.recovery_attempts)

        await self._db.flush()
        return RecoveryResult(
            attempt_id=attempt.id,
            consignment_id=consignment.id,
            outcome=final_outcome,
            note=note,
        )

    def _backoff(self, attempts: int) -> timedelta:
        seconds = min(
            self._settings.courier_recovery_initial_delay_seconds * (2**attempts),
            self._settings.courier_recovery_max_delay_seconds,
        )
        return timedelta(seconds=seconds)

    # ------------------------------------------------------- manual review --

    async def resolve_manually(
        self,
        attempt_id: uuid.UUID,
        *,
        parcel_exists: bool,
        reason: str,
        provider_consignment_id: str | None = None,
        tracking_code: str | None = None,
    ) -> RecoveryResult:
        """A person decides what an unresolvable booking meant.

        The only way out of ``MANUAL_REVIEW``, and deliberately so: the
        automatic path can prove existence but never absence, so declaring that
        a parcel was never created is a judgement a human makes — usually after
        looking at the courier's own portal — and it is recorded with who and
        why.

        ``parcel_exists=True`` attaches whatever identity the operator found
        and treats the parcel as booked. ``parcel_exists=False`` abandons the
        attempt and returns the order to bookable, which is the *only* path
        that ever makes a rebooking possible after an ambiguous create.
        """
        if not reason.strip():
            raise ValidationError("A manual booking resolution needs a reason")

        attempt = await self._db.get(CourierBookingAttempt, attempt_id)
        if attempt is None:
            raise NotFoundError("Booking attempt not found")
        if attempt.attempt_state not in (
            BookingAttemptState.MANUAL_REVIEW,
            BookingAttemptState.UNKNOWN,
        ):
            raise ConflictError(
                "That booking attempt has already been resolved",
                details={"state": attempt.state},
            )

        consignment = await self._db.get(Consignment, attempt.consignment_id)
        if consignment is None:
            raise NotFoundError("The parcel this booking attempt belongs to is gone")

        now = utc_now()
        context = current_context()
        attempt.resolved_by = context.user_id
        attempt.last_recovery_at = now
        attempt.next_recovery_at = None
        attempt.recovery_note = reason[:400]

        if parcel_exists:
            attempt.state = str(BookingAttemptState.RECOVERED)
            attempt.provider_consignment_id = provider_consignment_id
            attempt.tracking_code = tracking_code
            consignment.status = str(ConsignmentStatus.BOOKED)
            consignment.provider_consignment_id = provider_consignment_id
            consignment.tracking_code = tracking_code
            consignment.booked_at = consignment.booked_at or attempt.started_at
            consignment.last_status_at = now
            consignment.next_poll_at = now + timedelta(
                minutes=self._settings.courier_poll_interval_fresh_minutes
            )
            await self._db.flush()
            order = await self._db.get(Order, attempt.order_id)
            if order is not None:
                await self._consignments.fulfil_dispatch(consignment, order=order, occurred_at=now)
        else:
            attempt.state = str(BookingAttemptState.ABANDONED)
            # Back to NOT_BOOKED, which is what makes the order bookable again.
            # Nothing else in the system may put a parcel in this position.
            consignment.status = str(ConsignmentStatus.NOT_BOOKED)
            consignment.last_status_at = now
            consignment.next_poll_at = None
            await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.BOOKING_MANUAL_RESOLUTION,
            entity_type="consignment",
            entity_id=consignment.id,
            reason=reason,
            context={
                "provider": attempt.provider,
                "merchant_reference": attempt.merchant_reference,
                "parcel_exists": parcel_exists,
                "attempt_id": str(attempt.id),
            },
        )
        return RecoveryResult(
            attempt_id=attempt.id,
            consignment_id=consignment.id,
            outcome=RecoveryOutcome.RECOVERED if parcel_exists else RecoveryOutcome.MANUAL_REVIEW,
            note=reason,
        )

    async def attempts_for(self, consignment_id: uuid.UUID) -> list[CourierBookingAttempt]:
        result = await self._db.execute(
            sa.select(CourierBookingAttempt)
            .where(CourierBookingAttempt.consignment_id == consignment_id)
            .order_by(CourierBookingAttempt.attempt_number.asc())
        )
        return list(result.scalars().all())
