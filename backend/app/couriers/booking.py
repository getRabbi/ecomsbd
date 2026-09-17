"""Booking parcels with a courier provider.

Brief sections 6, 7, 8, 11, 12, 40 and 51; master spec sections 10.2, 11, 36.

This module contains the two or three decisions that decide whether this whole
integration is safe, so they are stated plainly.

**1. The merchant reference is created and committed before the call goes out.**

    prepare  ->  COMMIT  ->  HTTP  ->  apply outcome  ->  COMMIT

The commit in the middle is deliberate and load-bearing. If the process dies
during the provider call, the booking attempt row — carrying the exact invoice
that was sent — has already survived. Without it, a parcel could exist at the
courier with nothing on our side that knows its reference, which is a parcel
nobody can ever reconcile. It also means no database transaction is held open
across a network call, so a slow provider cannot exhaust the connection pool.

**2. An ambiguous create is never retried, and never becomes a failure.**

``BookingOutcome.UNKNOWN`` maps to ``ConsignmentStatus.BOOKING_UNKNOWN`` and
stops. Recovery, in :mod:`app.couriers.recovery`, asks the provider about the
invoice. Collapsing ``UNKNOWN`` into ``FAILED`` and letting the seller press
the button again is how a shop ships two parcels and pays for both.

**3. Stock moves when the provider confirms, not when we ask.**

A create that timed out has not taken anything off the shelf. Decrementing on
``BOOKING_UNKNOWN`` would make the stock ledger disagree with the shelf in
exactly the case where a person has to go and count.

Bulk booking adds one more rule, from brief section 12: a bulk request that
times out is **not resent**. Every item in it becomes independently ambiguous
and is reconciled by its own invoice. Resending a 50-item batch after a timeout
is 50 potential duplicate parcels.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.money import Money
from app.common.provider_health import ProviderHealthService, ProviderKind
from app.consignments.models import Consignment, ConsignmentStatus
from app.consignments.service import ConsignmentService
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import CredentialVault
from app.couriers.accounts import CourierAccountService
from app.couriers.adapter import (
    BookingOutcome,
    BookingRequest,
    BookingResult,
    CourierAdapter,
)
from app.couriers.capabilities import Capability
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import (
    BookingAttemptState,
    CourierAccount,
    CourierBookingAttempt,
    CourierEvent,
    CourierEventKind,
    CourierEventSource,
    CourierRawPayload,
    RawPayloadKind,
    payload_hash,
)
from app.customers.models import Customer
from app.customers.service import CUSTOMER_PHONE_CONTEXT
from app.orders.models import Order, OrderItem, OrderStatus

__all__ = [
    "BookingReport",
    "CourierBookingService",
    "ItemBookingReport",
    "merchant_reference_for",
]

log = get_logger(__name__)


def merchant_reference_for(order_number: str, sequence: int) -> str:
    """The stable invoice sent to the provider (brief section 7).

    ``CP-20260909-0042`` for the first parcel of an order, ``CP-20260909-0042-2``
    for a second one — if the first was cancelled and the seller ships again.

    Three properties, all required:

    *   **Stable across retries.** Every attempt at the *same* parcel sends the
        same string. Generating a fresh one on retry would make the provider's
        "invoice must be unique" constraint useless to us, because a duplicate
        parcel would carry a different reference and look like a different
        order.
    *   **Never reused.** The sequence suffix means a replacement parcel gets a
        new reference, so a courier statement naming ``…-0042`` is
        unambiguously about the first parcel forever.
    *   **Inside the documented character set.** Letters, digits, hyphens and
        underscores only. Order numbers already satisfy this; the check in
        ``validate_invoice`` enforces it before anything is persisted.
    """
    base = order_number.strip()
    return base if sequence <= 1 else f"{base}-{sequence}"


@dataclass(frozen=True, slots=True)
class ItemBookingReport:
    """What happened to one order in a booking request."""

    order_id: uuid.UUID
    consignment_id: uuid.UUID | None
    merchant_reference: str
    outcome: str
    provider_consignment_id: str | None = None
    tracking_code: str | None = None
    #: Seller-facing, already mapped through the error catalogue. Never the
    #: provider's own words.
    error_code: str | None = None
    message: str | None = None

    @property
    def is_booked(self) -> bool:
        return self.outcome == str(ConsignmentStatus.BOOKED)

    @property
    def is_ambiguous(self) -> bool:
        return self.outcome == str(ConsignmentStatus.BOOKING_UNKNOWN)


@dataclass(slots=True)
class BookingReport:
    """The result of one booking call, single or bulk."""

    provider: str
    items: list[ItemBookingReport] = field(default_factory=list)
    batch_id: uuid.UUID | None = None

    @property
    def booked(self) -> int:
        return sum(1 for item in self.items if item.is_booked)

    @property
    def ambiguous(self) -> int:
        return sum(1 for item in self.items if item.is_ambiguous)

    @property
    def failed(self) -> int:
        return len(self.items) - self.booked - self.ambiguous

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "batch_id": str(self.batch_id) if self.batch_id else None,
            "booked": self.booked,
            "ambiguous": self.ambiguous,
            "failed": self.failed,
            "items": [
                {
                    "order_id": str(item.order_id),
                    "consignment_id": str(item.consignment_id) if item.consignment_id else None,
                    "merchant_reference": item.merchant_reference,
                    "outcome": item.outcome,
                    "tracking_code": item.tracking_code,
                    "error_code": item.error_code,
                    "message": item.message,
                }
                for item in self.items
            ],
        }


@dataclass(slots=True)
class _Prepared:
    """One order, reserved and ready to send."""

    order: Order
    consignment: Consignment
    attempt: CourierBookingAttempt
    request: BookingRequest
    #: The provider-shaped, masked payload, captured when the attempt was
    #: prepared. Kept rather than rebuilt so the evidence row records exactly
    #: what was described at send time.
    redacted_request: dict[str, object] = field(default_factory=dict)


class CourierBookingService:
    """Books parcels, safely."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        accounts: CourierAccountService,
        consignments: ConsignmentService,
        vault: CredentialVault,
        settings: Settings | None = None,
        health: ProviderHealthService | None = None,
    ) -> None:
        self._db = session
        self._accounts = accounts
        self._consignments = consignments
        # Customer phone numbers are encrypted at rest. Booking is one of the
        # few legitimate reasons to read one, and it is deliberately *not*
        # audited as a reveal: the number goes to a courier, not to a person's
        # screen, and an audit row per booking would bury the reveals that
        # matter (master spec section 101).
        self._vault = vault
        self._settings = settings or get_settings()
        self._health = health or ProviderHealthService(session)

    # ------------------------------------------------------------- single --

    async def book(
        self,
        order_id: uuid.UUID,
        *,
        provider: str = "steadfast",
        note: str | None = None,
        item_description: str | None = None,
        delivery_type: int | None = None,
    ) -> BookingReport:
        """Book one order with a courier.

        Returns a report rather than raising for a provider-side outcome: a
        failed booking and an ambiguous one are both results the seller needs
        to see, and an exception would collapse them into "something went
        wrong".
        """
        account, adapter = await self._require_account(provider)

        prepared = await self._prepare(
            order_id,
            account=account,
            adapter=adapter,
            provider=provider,
            note=note,
            item_description=item_description,
            delivery_type=delivery_type,
        )

        # The attempt — and the invoice on it — is durable from here on. See
        # the module docstring for why this commit is not optional.
        await self._db.commit()

        credentials = self._accounts.credentials_for(account)
        started = utc_now()
        try:
            result = await adapter.create_consignment(
                credentials, prepared.request, prepared.attempt.merchant_reference
            )
        except Exception as exc:
            # An exception escaping the adapter is not evidence that nothing
            # happened. It is treated exactly like a lost answer.
            log.error(
                "courier booking raised",
                extra={
                    "provider": provider,
                    "operation": "create_consignment",
                    "error": type(exc).__name__,
                },
            )
            result = BookingResult(
                outcome=BookingOutcome.UNKNOWN,
                error_code="UNEXPECTED_ERROR",
                error_message=type(exc).__name__,
            )

        await self._record_health(provider, result, started)
        item = await self._apply(prepared, result, provider=provider, account=account)
        return BookingReport(provider=provider, items=[item])

    # --------------------------------------------------------------- bulk --

    async def book_bulk(
        self,
        order_ids: list[uuid.UUID],
        *,
        provider: str = "steadfast",
        note: str | None = None,
    ) -> BookingReport:
        """Book several orders, in chunks, safely.

        Each chunk is prepared and committed before it is sent, so a crash
        between chunks leaves every already-sent item recoverable and every
        unsent one untouched.

        A chunk that fails ambiguously marks **its own items** ambiguous and
        moves on to the next chunk. It is never resent (brief section 12).
        """
        if not order_ids:
            return BookingReport(provider=provider)

        account, adapter = await self._require_account(provider)
        chunk_size = self._chunk_size(adapter)
        batch_id = uuid.uuid4()
        report = BookingReport(provider=provider, batch_id=batch_id)

        # Deduplicate while preserving order: a client that sends the same
        # order twice in one selection must not get two parcels for it.
        unique_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for order_id in order_ids:
            if order_id not in seen:
                seen.add(order_id)
                unique_ids.append(order_id)

        pending: list[_Prepared] = []
        for order_id in unique_ids:
            try:
                pending.append(
                    await self._prepare(
                        order_id,
                        account=account,
                        adapter=adapter,
                        provider=provider,
                        note=note,
                        item_description=None,
                        delivery_type=None,
                        is_bulk=True,
                        batch_id=batch_id,
                    )
                )
            except (ValidationError, ConflictError, NotFoundError) as exc:
                # Rejected before anything was reserved. Nothing to undo, and
                # it must not stop the rest of the selection.
                report.items.append(
                    ItemBookingReport(
                        order_id=order_id,
                        consignment_id=None,
                        merchant_reference="",
                        outcome=str(ConsignmentStatus.NOT_BOOKED),
                        error_code=str(getattr(exc, "code", ErrorCode.VALIDATION_ERROR)),
                        message=str(exc),
                    )
                )

        await self._db.commit()

        credentials = self._accounts.credentials_for(account)
        for index in range(0, len(pending), chunk_size):
            chunk = pending[index : index + chunk_size]
            started = utc_now()
            try:
                results = await adapter.create_bulk(credentials, [item.request for item in chunk])
            except Exception as exc:
                log.error(
                    "courier bulk booking raised",
                    extra={
                        "provider": provider,
                        "operation": "create_bulk",
                        "error": type(exc).__name__,
                        "chunk_size": len(chunk),
                    },
                )
                results = [
                    BookingResult(
                        outcome=BookingOutcome.UNKNOWN,
                        error_code="UNEXPECTED_ERROR",
                        error_message=type(exc).__name__,
                    )
                    for _ in chunk
                ]

            if not isinstance(results, list) or len(results) != len(chunk):
                # The adapter could not answer per item — an unsupported
                # capability, or a malformed response. Every item we sent is
                # therefore ambiguous, because we cannot say which ones landed.
                results = [
                    BookingResult(
                        outcome=BookingOutcome.UNKNOWN,
                        error_code=str(ErrorCode.BOOKING_AMBIGUOUS),
                        error_message="The courier's bulk response could not be matched to the batch",
                    )
                    for _ in chunk
                ]

            for prepared, result in zip(chunk, results, strict=True):
                await self._record_health(provider, result, started)
                report.items.append(
                    await self._apply(prepared, result, provider=provider, account=account)
                )
            await self._db.commit()

        return report

    # ------------------------------------------------------------ prepare --

    async def _prepare(
        self,
        order_id: uuid.UUID,
        *,
        account: CourierAccount,
        adapter: CourierAdapter,
        provider: str,
        note: str | None,
        item_description: str | None,
        delivery_type: int | None,
        is_bulk: bool = False,
        batch_id: uuid.UUID | None = None,
    ) -> _Prepared:
        """Reserve a consignment and a booking attempt for one order.

        Everything that can fail locally fails here, before the attempt is
        durable and before anything reaches the provider: a cancelled order, a
        parcel already in flight, an unreachable phone number, an invoice that
        breaks the provider's documented character set.
        """
        order = await self._lock_order(order_id)

        if order.order_status in (OrderStatus.CANCELLED, OrderStatus.COMPLETED):
            raise ConflictError(
                "That order is finished and cannot be booked",
                code=ErrorCode.ORDER_NOT_BOOKABLE,
                details={"status": order.status},
            )

        consignment = await self._existing_live_consignment(order_id)
        if consignment is not None and consignment.consignment_status not in (
            ConsignmentStatus.NOT_BOOKED,
            ConsignmentStatus.FAILED,
        ):
            # Includes BOOKING and BOOKING_UNKNOWN. A parcel whose outcome we
            # are unsure of must never be re-sent from here; recovery owns it.
            record_metric(
                CourierMetric.DUPLICATE_PREVENTED,
                provider=provider,
                state=consignment.status,
            )
            raise ConflictError(
                _duplicate_message(consignment.consignment_status),
                code=(
                    ErrorCode.BOOKING_AMBIGUOUS
                    if consignment.consignment_status.is_ambiguous
                    else ErrorCode.ORDER_NOT_BOOKABLE
                ),
                details={
                    "consignment_id": str(consignment.id),
                    "status": consignment.status,
                },
            )

        request = await self._booking_request(
            order,
            note=note,
            item_description=item_description,
            account=account,
        )

        if consignment is None:
            sequence = await self._next_consignment_sequence(order_id, provider)
            reference = merchant_reference_for(order.order_number, sequence)
            consignment = Consignment(
                order_id=order_id,
                provider=provider,
                merchant_reference=reference,
                status=str(ConsignmentStatus.BOOKING),
                cod_amount_paisa=order.cod_amount_paisa,
                courier_account_id=account.id,
            )
            self._db.add(consignment)
            await self._db.flush()
        else:
            # Retrying a booking that failed safely. The reference is reused
            # *because* nothing was created under it — the provider told us so.
            consignment.status = str(ConsignmentStatus.BOOKING)
            consignment.provider = provider
            consignment.courier_account_id = account.id

        request = _with_reference(request, consignment.merchant_reference)
        # The adapter describes its own payload and validates it locally, so
        # this method never has to know which provider it is preparing for.
        # It raises ValueError when the provider would refuse the booking
        # itself — before an attempt row exists and before anything is sent.
        preview = adapter.describe_booking(request, consignment.merchant_reference)

        consignment.booking_attempt_count += 1
        attempt = CourierBookingAttempt(
            consignment_id=consignment.id,
            order_id=order_id,
            provider=provider,
            courier_account_id=account.id,
            merchant_reference=consignment.merchant_reference,
            attempt_number=consignment.booking_attempt_count,
            state=str(BookingAttemptState.PENDING),
            is_bulk=is_bulk,
            batch_id=batch_id,
            requested_cod_taka=preview.cod_taka,
            cod_residual_paisa=preview.cod_residual_paisa,
            provider_phone_masked=preview.recipient_phone_masked,
            started_at=utc_now(),
        )
        self._db.add(attempt)
        await self._db.flush()

        if delivery_type is not None:
            consignment.metadata_json = {
                **(consignment.metadata_json or {}),
                "delivery_type": delivery_type,
            }

        return _Prepared(
            order=order,
            consignment=consignment,
            attempt=attempt,
            request=request,
            redacted_request=preview.redacted_payload,
        )

    # -------------------------------------------------------------- apply --

    async def _apply(
        self,
        prepared: _Prepared,
        result: BookingResult,
        *,
        provider: str,
        account: CourierAccount,
    ) -> ItemBookingReport:
        """Turn a provider result into domain state.

        Three branches, and the difference between the last two is the entire
        safety model.
        """
        attempt = prepared.attempt
        now = utc_now()

        raw_payload = await self._store_raw(
            provider=provider,
            account=account,
            kind=(
                RawPayloadKind.BULK_BOOKING_RESPONSE
                if attempt.is_bulk
                else RawPayloadKind.BOOKING_RESPONSE
            ),
            request_payload=prepared.redacted_request,
            response_payload=(
                dict(result.consignment.raw) if result.consignment is not None else None
            ),
            correlation_id=result.provider_request_id,
        )
        attempt.raw_payload_id = raw_payload.id
        attempt.correlation_id = result.provider_request_id
        attempt.completed_at = now

        if result.outcome is BookingOutcome.BOOKED and result.consignment is not None:
            return await self._apply_booked(prepared, result, provider=provider, now=now)

        if result.outcome is BookingOutcome.UNKNOWN:
            return await self._apply_unknown(prepared, result, provider=provider, now=now)

        return await self._apply_failed(prepared, result, provider=provider, now=now)

    async def _apply_booked(
        self, prepared: _Prepared, result: BookingResult, *, provider: str, now: datetime
    ) -> ItemBookingReport:
        consignment = prepared.consignment
        attempt = prepared.attempt
        assert result.consignment is not None  # noqa: S101 - narrowed by the caller

        provider_consignment = result.consignment
        consignment.status = str(ConsignmentStatus.BOOKED)
        consignment.provider_consignment_id = provider_consignment.provider_consignment_id
        consignment.tracking_code = provider_consignment.tracking_code
        consignment.provider_raw_status = provider_consignment.raw_status
        consignment.booked_at = now
        consignment.last_status_at = now
        consignment.provider_status_at = now
        consignment.next_poll_at = now + _fresh_poll_delay(self._settings)

        attempt.state = str(BookingAttemptState.SUCCEEDED)
        attempt.provider_consignment_id = provider_consignment.provider_consignment_id
        attempt.tracking_code = provider_consignment.tracking_code

        await self._db.flush()

        # Only now does the parcel physically exist, so only now does stock
        # move and a receivable open.
        await self._consignments.fulfil_dispatch(consignment, order=prepared.order, occurred_at=now)
        await self._record_event(
            consignment,
            provider=provider,
            kind=CourierEventKind.BOOKING,
            source=CourierEventSource.BOOKING_CALL,
            raw_status=provider_consignment.raw_status,
            raw_payload_id=attempt.raw_payload_id,
            correlation_id=attempt.correlation_id,
        )

        await record_audit(
            self._db,
            AuditAction.ORDER_BOOKED,
            entity_type="consignment",
            entity_id=consignment.id,
            context={
                "provider": provider,
                "merchant_reference": attempt.merchant_reference,
                "provider_consignment_id": provider_consignment.provider_consignment_id,
                "attempt_number": attempt.attempt_number,
            },
        )
        record_metric(CourierMetric.CREATE_SUCCESS, provider=provider)
        return ItemBookingReport(
            order_id=prepared.order.id,
            consignment_id=consignment.id,
            merchant_reference=attempt.merchant_reference,
            outcome=str(ConsignmentStatus.BOOKED),
            provider_consignment_id=provider_consignment.provider_consignment_id,
            tracking_code=provider_consignment.tracking_code,
        )

    async def _apply_unknown(
        self, prepared: _Prepared, result: BookingResult, *, provider: str, now: datetime
    ) -> ItemBookingReport:
        """The answer was lost. The parcel may exist.

        Nothing moves: no stock, no receivable, no provider identity. The
        parcel sits in ``BOOKING_UNKNOWN`` with its invoice, and recovery asks
        the provider about that invoice. The seller is told not to rebook, and
        the client is given no retry affordance for this state.
        """
        consignment = prepared.consignment
        attempt = prepared.attempt

        consignment.status = str(ConsignmentStatus.BOOKING_UNKNOWN)
        consignment.last_status_at = now

        attempt.state = str(BookingAttemptState.UNKNOWN)
        attempt.error_code = result.error_code
        attempt.error_message = (result.error_message or "")[:400] or None
        attempt.next_recovery_at = now + _recovery_delay(self._settings, 0)
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.BOOKING_UNKNOWN_RECORDED,
            entity_type="consignment",
            entity_id=consignment.id,
            context={
                "provider": provider,
                "merchant_reference": attempt.merchant_reference,
                "attempt_number": attempt.attempt_number,
                "error_code": result.error_code,
                "is_bulk": attempt.is_bulk,
            },
        )
        record_metric(CourierMetric.CREATE_AMBIGUOUS, provider=provider)
        log.warning(
            "courier booking outcome unknown",
            extra={
                "provider": provider,
                "operation": "create_consignment",
                "merchant_reference": attempt.merchant_reference,
                "error_code": result.error_code,
            },
        )
        return ItemBookingReport(
            order_id=prepared.order.id,
            consignment_id=consignment.id,
            merchant_reference=attempt.merchant_reference,
            outcome=str(ConsignmentStatus.BOOKING_UNKNOWN),
            error_code=str(ErrorCode.BOOKING_AMBIGUOUS),
            message=("Booking result নিশ্চিত হয়নি — আবার বুক করবেন না। আগের চেষ্টা যাচাই করা হচ্ছে।"),
        )

    async def _apply_failed(
        self, prepared: _Prepared, result: BookingResult, *, provider: str, now: datetime
    ) -> ItemBookingReport:
        """The provider answered and refused. Nothing exists; retrying is safe."""
        consignment = prepared.consignment
        attempt = prepared.attempt

        consignment.status = str(ConsignmentStatus.NOT_BOOKED)
        consignment.last_status_at = now

        attempt.state = str(BookingAttemptState.FAILED)
        attempt.error_code = result.error_code
        attempt.error_message = (result.error_message or "")[:400] or None
        await self._db.flush()

        record_metric(
            CourierMetric.CREATE_FAILED, provider=provider, reason=result.error_code or "unknown"
        )
        return ItemBookingReport(
            order_id=prepared.order.id,
            consignment_id=consignment.id,
            merchant_reference=attempt.merchant_reference,
            outcome=str(ConsignmentStatus.NOT_BOOKED),
            error_code=result.error_code,
            message=result.error_message,
        )

    # ---------------------------------------------------------- internals --

    async def _require_account(self, provider: str) -> tuple[CourierAccount, CourierAdapter]:
        account = await self._accounts.for_provider(provider)
        if account is None or not account.has_credentials:
            raise ConflictError(
                f"No {provider} account is connected. Use manual courier mode, "
                "or connect the courier in Settings.",
                code=ErrorCode.COURIER_ACCOUNT_NEEDS_RECONNECT,
                details={"provider": provider},
            )
        if not account.is_usable:
            raise ConflictError(
                "That courier account needs to be reconnected before it can book.",
                code=ErrorCode.COURIER_ACCOUNT_NEEDS_RECONNECT,
                details={"provider": provider, "status": account.status},
            )

        if not await self._health.allows(
            provider, capability=str(Capability.CREATE_SINGLE), tenant_id=account.tenant_id
        ):
            # The breaker is open. Refusing here is safer than sending into a
            # provider we already know is failing: every attempt that fails
            # ambiguously is another parcel nobody can account for.
            raise ConflictError(
                "The courier is not accepting bookings right now. Your orders are "
                "unchanged — try again shortly, or use manual courier mode.",
                code=ErrorCode.COURIER_PROVIDER_UNAVAILABLE,
                details={"provider": provider},
            )

        adapter = self._accounts.adapter_for(provider)
        if adapter is None:
            raise ValidationError(f"{provider} has no courier adapter")
        return account, adapter

    def _chunk_size(self, adapter: CourierAdapter) -> int:
        """The provider's own batch size, from the adapter.

        Each provider's ceiling — and its blast radius when a batch fails
        ambiguously — is its own, so this no longer falls back to Steadfast's
        setting for every courier.
        """
        return max(1, int(adapter.bulk_chunk_size))

    async def _lock_order(self, order_id: uuid.UUID) -> Order:
        """Load an order, taking a row lock where the backend offers one.

        The lock serialises the check-and-set that follows: two taps of Book
        arriving together must not both see "no live consignment". Correctness
        does not rest on the lock alone — the consignment's own ``BOOKING``
        state is durable and the second request sees it — but the lock closes
        the window rather than leaving it to a unique-constraint race.
        """
        stmt = sa.select(Order).where(Order.id == order_id)
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            stmt = stmt.with_for_update()
        order = (await self._db.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found")
        return order

    async def _existing_live_consignment(self, order_id: uuid.UUID) -> Consignment | None:
        """The parcel already attached to this order, if any.

        ``CANCELLED`` is excluded so a cancelled parcel can be replaced;
        everything else — including ``BOOKING_UNKNOWN`` — counts as live, which
        is what stops a second booking while the first is unresolved.
        """
        result = await self._db.execute(
            sa.select(Consignment)
            .where(
                Consignment.order_id == order_id,
                Consignment.status != str(ConsignmentStatus.CANCELLED),
            )
            .order_by(Consignment.created_at.desc())
        )
        return result.scalars().first()

    async def _next_consignment_sequence(self, order_id: uuid.UUID, provider: str) -> int:
        """1 for the first parcel of an order, 2 for a replacement, and so on.

        Counts every consignment ever created for the order, including
        cancelled ones, so a reference is never reused (brief section 7).
        """
        count = await self._db.scalar(
            sa.select(sa.func.count())
            .select_from(Consignment)
            .where(Consignment.order_id == order_id)
        )
        return int(count or 0) + 1

    async def _booking_request(
        self,
        order: Order,
        *,
        note: str | None,
        item_description: str | None,
        account: CourierAccount | None = None,
    ) -> BookingRequest:
        """Build the normalized booking input from the order.

        The phone comes from the order's customer in canonical E.164 and stays
        that way; the provider-format transform happens in the adapter, once,
        on the way out (brief section 6).

        ``store_reference`` carries the pickup store the seller chose on the
        courier account, for the providers that require one — Pathao rejects
        every create without it. It is passed as a *generic* field rather than
        read from the account inside the adapter, so this method stays free of
        provider branches and a second provider needing a pickup store costs
        nothing here.
        """
        customer = await self._db.get(Customer, order.customer_id) if order.customer_id else None
        phone = self._recipient_phone(customer)
        if not phone:
            raise ValidationError(
                "That order has no delivery phone number, so it cannot be booked",
                details={"order_id": str(order.id)},
            )
        address = (order.delivery_address_normalized or order.delivery_address_raw or "").strip()
        if not address:
            raise ValidationError(
                "That order has no delivery address, so it cannot be booked",
                code=ErrorCode.ADDRESS_REJECTED,
                details={"order_id": str(order.id)},
            )

        items = await self._db.execute(
            sa.select(sa.func.coalesce(sa.func.sum(OrderItem.quantity), 0)).where(
                OrderItem.order_id == order.id
            )
        )
        quantity = int(items.scalar_one() or 0)

        return BookingRequest(
            order_id=order.id,
            # Filled in by the caller once the consignment exists.
            merchant_reference="",
            recipient_name=(
                order.customer_name or (customer.name if customer else "") or "Customer"
            ),
            recipient_phone_e164=phone,
            recipient_address=address,
            cod_amount=Money(order.cod_amount_paisa),
            item_description=item_description or f"{quantity} item(s)",
            item_quantity=max(1, quantity),
            note=note or order.note,
            store_reference=_store_reference_of(account),
        )

    def _recipient_phone(self, customer: Customer | None) -> str | None:
        """The canonical E.164 number to deliver to.

        Only the customer record holds it: the order keeps a masked copy and a
        search hash, neither of which can be dialled. Returning ``None`` for an
        order with no customer is correct — that booking is refused before
        anything reaches the provider, rather than sent with a blank field the
        courier would reject after the parcel exists.
        """
        if customer is None:
            return None
        return self._vault.decrypt(customer.phone_enc, context=CUSTOMER_PHONE_CONTEXT)

    async def _store_raw(
        self,
        *,
        provider: str,
        account: CourierAccount,
        kind: RawPayloadKind,
        request_payload: dict | None,
        response_payload: dict | None,
        correlation_id: str | None,
        endpoint: str | None = None,
        http_status: int | None = None,
        schema_undocumented: bool = False,
    ) -> CourierRawPayload:
        """Persist provider evidence.

        ``request_payload`` must already be redacted — the caller passes
        ``CreateOrderRequest.redacted()``, which masks the phone and drops the
        email. Request *headers* are never stored at all, because that is where
        the API key is.
        """
        row = CourierRawPayload(
            provider=provider,
            courier_account_id=account.id,
            kind=str(kind),
            endpoint=endpoint,
            http_status=http_status,
            correlation_id=correlation_id,
            request_payload=request_payload,
            response_payload=response_payload,
            payload_sha256=payload_hash(response_payload or request_payload or {}),
            schema_undocumented=schema_undocumented,
            received_at=utc_now(),
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def _record_event(
        self,
        consignment: Consignment,
        *,
        provider: str,
        kind: CourierEventKind,
        source: CourierEventSource,
        raw_status: str | None,
        raw_payload_id: uuid.UUID | None,
        correlation_id: str | None,
    ) -> CourierEvent | None:
        """Append an immutable observation, or bump the one that already exists."""
        from app.couriers.events import record_courier_event

        return await record_courier_event(
            self._db,
            provider=provider,
            kind=kind,
            source=source,
            consignment=consignment,
            raw_status=raw_status,
            raw_payload_id=raw_payload_id,
            correlation_id=correlation_id,
        )

    async def _record_health(self, provider: str, result: BookingResult, started: datetime) -> None:
        latency_ms = int((utc_now() - started).total_seconds() * 1000)
        capability = str(Capability.CREATE_SINGLE)
        if result.outcome is BookingOutcome.BOOKED:
            await self._health.record_success(
                ProviderKind.COURIER, provider, capability=capability, latency_ms=latency_ms
            )
        else:
            await self._health.record_failure(
                ProviderKind.COURIER,
                provider,
                capability=capability,
                error_code=result.error_code,
                latency_ms=latency_ms,
                is_auth_failure=result.error_code == str(ErrorCode.INVALID_COURIER_CREDENTIALS),
            )


def _with_reference(request: BookingRequest, reference: str) -> BookingRequest:
    from dataclasses import replace

    return replace(request, merchant_reference=reference)


def _duplicate_message(status: ConsignmentStatus) -> str:
    if status is ConsignmentStatus.BOOKING_UNKNOWN:
        return (
            "The previous booking for this order has not been confirmed yet. "
            "Do not book it again — we are checking with the courier."
        )
    if status is ConsignmentStatus.BOOKING:
        return "A booking for this order is already in progress."
    return "That order already has a parcel out."


def _fresh_poll_delay(settings: Settings):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return timedelta(minutes=settings.courier_poll_interval_fresh_minutes)


def _recovery_delay(settings: Settings, attempts: int):  # type: ignore[no-untyped-def]
    """Exponential backoff between recovery attempts, capped.

    Starts short because a lost answer is often resolvable within a minute, and
    caps because asking hourly forever is neither useful nor polite.
    """
    from datetime import timedelta

    seconds = min(
        settings.courier_recovery_initial_delay_seconds * (2**attempts),
        settings.courier_recovery_max_delay_seconds,
    )
    return timedelta(seconds=seconds)


def _store_reference_of(account: CourierAccount | None) -> str | None:
    """The pickup store chosen on a courier account, if it has one.

    Returns ``None`` for a provider with no store concept, which is the normal
    case and not an error. A provider that *requires* one and has none set
    fails in its own adapter, before anything is sent — which is where the
    requirement is documented, rather than here.
    """
    if account is None:
        return None
    store_id = (account.metadata_json or {}).get("store_id")
    if store_id is None:
        return None
    text = str(store_id).strip()
    return text or None
