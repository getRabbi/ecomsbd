"""IVR confirmation calls (V4 WP3.2).

The dispatcher takes a due ``CALL_QUEUED`` row and rings the customer through
the deployment's :class:`~app.voice.transport.VoiceTransport`; the provider's
callbacks then say what happened. Three rules shape everything here:

*   **Record before ringing.** The attempt row and ``CALL_QUEUED → CALLING``
    commit in one transaction *before* the provider is asked, with the
    idempotency key ``confirmation:{id}:{attempt}``. A crash after that leaves
    a ``CALLING`` row, never an unrecorded call.
*   **Ambiguous means the seller, never a second call.** A timeout, a 5xx, an
    exception mid-request (``UNKNOWN``), or a ``CALLING`` row with no final
    callback after :data:`~app.confirmations.service.CALL_TIMEOUT`, goes to
    ``SELLER_REVIEW``. Only a certain no-answer is called again (30 minutes
    later, by :func:`app.confirmations.jobs.follow_up_confirmations`).
*   **The customer is protected first.** No call in the shop's quiet hours, at
    most :data:`CALLS_PER_CUSTOMER_PER_DAY` calls to one customer per Dhaka day
    across all of the shop's orders (the seller's own calls count), and no call
    for an order that already left ``DRAFT``.

Voice is "on" for a shop (:func:`voice_gate`) only with the
``voice_confirmation_enabled`` flag, a configured transport, its platform
account switched on, the provider's breaker closed and the shop's intro audio
(WP3.3: a version the seller played and turned on, made for the shop's current
name, and on the provider where it needs uploads). While it is off, ``CALL_QUEUED`` rows stay in the seller's manual call
queue (WP2.3), so nothing waits unseen.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.confirmations.models import (
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationState,
    OrderConfirmation,
)
from app.confirmations.service import ConfirmationService
from app.confirmations.state_machine import Cause
from app.core.clock import DHAKA, utc_now
from app.core.config import get_settings
from app.core.logging import get_logger
from app.orders.models import Order, OrderStatus
from app.voice.models import (
    CallbackState,
    VoiceCallbackDelivery,
    VoiceCallEvent,
    VoiceEventResult,
    VoiceProviderConnection,
)
from app.voice.transport import (
    DEFAULT_DTMF_OPTIONS,
    DtmfAction,
    VoiceAvailabilityState,
    VoiceCall,
    VoiceCallOutcome,
    VoiceCallResult,
    VoiceEvent,
    VoiceEventKind,
    VoiceTransport,
    build_voice_transport,
)

if TYPE_CHECKING:
    from app.customers.service import CustomerService
    from app.orders.service import OrderService

__all__ = [
    "CALLBACK_MAX_BYTES",
    "CALLS_PER_CUSTOMER_PER_DAY",
    "CallbackResult",
    "IvrCalls",
    "IvrStatus",
    "PreparedCall",
    "VoiceGate",
    "client_ref",
    "has_voice_credit",
    "intro_audio_ref",
    "place",
    "receive_callback",
    "voice_gate",
    "voice_transport",
]

log = get_logger("app.confirmations.calls")

S = ConfirmationState

#: Confirmation calls one customer may get from one shop per Asia/Dhaka day.
CALLS_PER_CUSTOMER_PER_DAY = 3
#: Largest callback body stored; anything bigger is refused unread.
CALLBACK_MAX_BYTES = 64 * 1024

_CLIENT_REF = re.compile(r"^confirmation:([0-9a-fA-F-]{36}):([0-9]{1,4})$")
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class IvrStatus(StrEnum):
    """``confirmation_attempts.status`` for an IVR call."""

    STARTED = "STARTED"
    PLACED = "PLACED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    CALLBACK = "CALLBACK"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    #: The provider reported the call failed after placing it.
    FAILED = "FAILED"
    #: Answered and hung up without a key the script offers.
    NO_KEY = "NO_KEY"
    #: The provider may have rung; nobody is called again.
    UNKNOWN = "UNKNOWN"
    # Certainly not placed:
    NOT_PLACED = "NOT_PLACED"
    REJECTED = "REJECTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


#: Still waiting for the provider to say how the call went.
_LIVE = frozenset({IvrStatus.STARTED, IvrStatus.PLACED, IvrStatus.RINGING, IvrStatus.ANSWERED})
#: Never rang the customer: not counted against their daily calls.
_NOT_PLACED = frozenset({IvrStatus.NOT_PLACED, IvrStatus.REJECTED, IvrStatus.NOT_CONFIGURED})
_RESULT_STATUS = {
    VoiceCallOutcome.FAILED: IvrStatus.NOT_PLACED,
    VoiceCallOutcome.REJECTED: IvrStatus.REJECTED,
    VoiceCallOutcome.NOT_CONFIGURED: IvrStatus.NOT_CONFIGURED,
}
_ENDED_STATUS = {
    VoiceEventKind.NO_ANSWER: IvrStatus.NO_ANSWER,
    VoiceEventKind.BUSY: IvrStatus.BUSY,
    VoiceEventKind.FAILED: IvrStatus.FAILED,
    VoiceEventKind.COMPLETED: IvrStatus.NO_KEY,
}
_DTMF = {option.digit: option.action for option in DEFAULT_DTMF_OPTIONS}
_ANSWER = {
    DtmfAction.CONFIRM: (IvrStatus.CONFIRMED, S.CONFIRMED, Cause.DTMF_CONFIRM),
    DtmfAction.CANCEL: (IvrStatus.CANCELLED, S.CANCELLED, Cause.DTMF_CANCEL),
    DtmfAction.CALLBACK: (IvrStatus.CALLBACK, S.SELLER_REVIEW, Cause.DTMF_CALLBACK),
}


def voice_transport() -> VoiceTransport:
    """The deployment's voice transport (tests replace this)."""
    return build_voice_transport(get_settings())


def client_ref(confirmation_id: uuid.UUID, attempt_no: int) -> str:
    return f"confirmation:{confirmation_id}:{attempt_no}"


async def intro_audio_ref(db: AsyncSession, tenant_id: uuid.UUID, *, provider: str) -> str | None:
    """The shop's intro as ``provider`` plays it (WP3.3): the version the
    seller listened to and turned on, made for the shop's current name.
    ``None`` = nothing to play, so voice stays off for the shop."""
    from app.voice.intro import IntroAudioService
    from app.voice.manifest import available_voice_manifests

    manifest = available_voice_manifests(get_settings()).get(provider)
    return await IntroAudioService(db).call_ref(tenant_id, provider=provider, manifest=manifest)


async def has_voice_credit(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """WP3.4 meters calls per shop. Until it does, a shop with voice on may call."""
    return True


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VoiceGate:
    on: bool
    provider: str
    #: Why it is off: a machine code.
    reason: str | None = None
    audio_ref: str | None = None
    #: The two recordings around a spoken amount; only with ``dynamic_audio``.
    amount_prompts: tuple[str, str] | None = None


async def voice_gate(
    db: AsyncSession, tenant_id: uuid.UUID, *, transport: VoiceTransport | None = None
) -> VoiceGate:
    """Whether the IVR may call this shop's customers right now.

    Read-only (the breaker is peeked, not probed), so the seller's call queue
    can ask it on every load.
    """
    from app.common.feature_flags import FeatureFlagService, FlagKey
    from app.common.provider_health import ProviderHealth, scope_key_for
    from app.voice.service import CALL_CAPABILITY

    if not await FeatureFlagService(db).is_enabled(
        FlagKey.VOICE_CONFIRMATION_ENABLED, tenant_id=tenant_id
    ):
        return VoiceGate(False, "", "FLAG_OFF")
    voice = transport or voice_transport()
    state = voice.availability().state
    if state is not VoiceAvailabilityState.AVAILABLE:
        return VoiceGate(False, voice.name, str(state))
    enabled = await db.scalar(
        sa.select(VoiceProviderConnection.enabled).where(
            VoiceProviderConnection.provider == voice.name
        )
    )
    if not enabled:
        return VoiceGate(False, voice.name, "CONNECTION_OFF")
    health = await db.scalar(
        sa.select(ProviderHealth).where(
            ProviderHealth.provider == voice.name,
            ProviderHealth.capability == CALL_CAPABILITY,
            ProviderHealth.scope_key == scope_key_for(None),
        )
    )
    if health is not None and not health.allows_request():
        return VoiceGate(False, voice.name, "BREAKER_OPEN")
    audio = await intro_audio_ref(db, tenant_id, provider=voice.name)
    if audio is None:
        return VoiceGate(False, voice.name, "NO_INTRO_AUDIO")
    from app.voice.intro import amount_prompts
    from app.voice.manifest import available_voice_manifests

    settings = get_settings()
    prompts = amount_prompts(available_voice_manifests(settings).get(voice.name), settings)
    return VoiceGate(True, voice.name, None, audio, prompts)


# --------------------------------------------------------------------------- #
# Calls
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PreparedCall:
    """A call recorded and committed, about to be placed."""

    tenant_id: uuid.UUID
    confirmation_id: uuid.UUID
    attempt_id: uuid.UUID
    provider: str
    call: VoiceCall


class Skip(StrEnum):
    """Why a due row was not called this tick."""

    TAKEN = "TAKEN"  # another worker has it, or it moved
    ORDER_NOT_DRAFT = "ORDER_NOT_DRAFT"  # stays queued; never called
    BREAKER_OPEN = "BREAKER_OPEN"  # stays queued; the shop's queue shows it
    NO_NUMBER = "NO_NUMBER"  # → SELLER_REVIEW
    NO_CREDIT = "NO_CREDIT"  # → SELLER_REVIEW
    CALL_LIMIT = "CALL_LIMIT"  # → SELLER_REVIEW


async def place(transport: VoiceTransport, call: VoiceCall) -> tuple[VoiceCallResult, int]:
    """Ask the provider. An exception mid-request may still have dialled, so
    it reads as ``UNKNOWN``. Returns the result and the latency in ms."""
    started = time.monotonic()
    try:
        result = await transport.place_call(call)
    except Exception as exc:
        log.warning(
            "voice call raised; treated as ambiguous",
            extra={**call.redacted(), "error": type(exc).__name__},
        )
        result = VoiceCallResult(VoiceCallOutcome.UNKNOWN, transport.name, detail="TRANSPORT_ERROR")
    return result, int((time.monotonic() - started) * 1000)


class IvrCalls:
    """One tenant's IVR calls. Every method expects the tenant's session."""

    def __init__(
        self, session: AsyncSession, *, orders: OrderService, customers: CustomerService
    ) -> None:
        self._db = session
        self._customers = customers
        self._confirmations = ConfirmationService(session, orders=orders)

    # ---------------------------------------------------------- dispatch ---

    async def prepare(
        self, confirmation_id: uuid.UUID, *, gate: VoiceGate, now: datetime
    ) -> PreparedCall | Skip:
        """Transaction 1: lock the row, check the customer's guards, record the
        attempt and move to ``CALLING``. The caller commits before placing."""
        from app.common.provider_health import ProviderHealthService
        from app.customers.models import Customer
        from app.voice.service import CALL_CAPABILITY

        row = await self._db.scalar(
            sa.select(OrderConfirmation)
            .where(
                OrderConfirmation.id == confirmation_id,
                OrderConfirmation.state == str(S.CALL_QUEUED),
                sa.or_(
                    OrderConfirmation.next_action_at.is_(None),
                    OrderConfirmation.next_action_at <= now,
                ),
            )
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return Skip.TAKEN
        order = await self._db.get(Order, row.order_id)
        if order is None or order.deleted_at is not None or order.status != OrderStatus.DRAFT:
            return Skip.ORDER_NOT_DRAFT
        customer = (
            await self._db.get(Customer, order.customer_id)
            if order.customer_id is not None
            else None
        )
        phone: str | None = None
        if customer is not None:
            try:
                phone = self._customers.phone_number(customer).e164
            except Exception:
                phone = None
        if phone is None:
            await self._to_seller(row, Cause.VOICE_UNAVAILABLE, now)
            return Skip.NO_NUMBER
        if await self.calls_today(order.customer_id, now) >= CALLS_PER_CUSTOMER_PER_DAY:
            await self._to_seller(row, Cause.CALL_LIMIT, now)
            return Skip.CALL_LIMIT
        if not await has_voice_credit(self._db, row.tenant_id):
            await self._to_seller(row, Cause.VOICE_UNAVAILABLE, now)
            return Skip.NO_CREDIT
        if not await ProviderHealthService(self._db).allows(
            gate.provider, capability=CALL_CAPABILITY
        ):
            return Skip.BREAKER_OPEN
        if gate.audio_ref is None:  # the gate is on only with audio
            raise RuntimeError("voice gate on without intro audio")

        attempt = await self._confirmations.add_attempt(
            row, AttemptChannel.IVR, provider=gate.provider, now=now
        )
        await self._confirmations.transition(row, S.CALLING, Cause.DIALING, now=now)
        from app.voice.intro import call_audio

        call = VoiceCall(
            to_e164=phone,
            client_ref=client_ref(row.id, attempt.attempt_no),
            **call_audio(
                gate.audio_ref, cod_paisa=row.cod_paisa_snapshot, prompts=gate.amount_prompts
            ),
        )
        return PreparedCall(row.tenant_id, row.id, attempt.id, gate.provider, call)

    async def calls_today(self, customer_id: uuid.UUID | None, now: datetime) -> int:
        """Calls that may have rung this customer today (Dhaka), on any of
        the shop's orders: IVR and the seller's own."""
        if customer_id is None:
            return 0
        local = now.astimezone(DHAKA)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        count = await self._db.scalar(
            sa.select(sa.func.count(ConfirmationAttempt.id))
            .join(OrderConfirmation, OrderConfirmation.id == ConfirmationAttempt.confirmation_id)
            .join(Order, Order.id == OrderConfirmation.order_id)
            .where(
                Order.customer_id == customer_id,
                ConfirmationAttempt.channel.in_(
                    [str(AttemptChannel.IVR), str(AttemptChannel.MANUAL_CALL)]
                ),
                ConfirmationAttempt.started_at >= day_start,
                ConfirmationAttempt.status.not_in([str(status) for status in _NOT_PLACED]),
            )
        )
        return int(count or 0)

    async def record_placed(
        self,
        prepared: PreparedCall,
        result: VoiceCallResult,
        *,
        now: datetime,
        latency_ms: int | None = None,
    ) -> None:
        """Transaction 2: what the provider said, and the provider's health."""
        from app.api.deps import get_vault
        from app.voice.service import VoiceConnectionService

        row = await self._db.scalar(
            sa.select(OrderConfirmation)
            .where(OrderConfirmation.id == prepared.confirmation_id)
            .with_for_update()
        )
        attempt = await self._db.get(ConfirmationAttempt, prepared.attempt_id)
        if row is None or attempt is None:  # cascade-protected; never expected
            raise RuntimeError("the prepared call's rows are gone")
        await VoiceConnectionService(self._db, get_vault(), {}).record_result(
            prepared.provider, result, latency_ms=latency_ms
        )
        if result.provider_ref and attempt.provider_ref is None:
            attempt.provider_ref = result.provider_ref[:120]
        outcome = result.outcome
        if outcome in (VoiceCallOutcome.PLACED, VoiceCallOutcome.DUPLICATE):
            if attempt.status == IvrStatus.STARTED:
                attempt.status = str(IvrStatus.PLACED)
            await self._db.flush()
            if attempt.provider_ref:
                await self._replay_unmatched(attempt, now)
            return
        attempt.ended_at = attempt.ended_at or now
        current = attempt.status in _LIVE
        if outcome is VoiceCallOutcome.UNKNOWN:
            if current:
                attempt.status = str(IvrStatus.UNKNOWN)
            cause = Cause.CALL_UNKNOWN
        else:
            if current:
                attempt.status = str(_RESULT_STATUS[outcome])
            cause = Cause.CALL_FAILED
        await self._db.flush()
        if self._is_current(row, attempt):
            await self._confirmations.transition(row, S.SELLER_REVIEW, cause, now=now)
        log.info(
            "voice call not placed cleanly",
            extra={
                "confirmation_id": str(row.id),
                "attempt_no": attempt.attempt_no,
                "outcome": str(outcome),
                "detail": result.detail,
            },
        )

    async def reconcile(self, row: OrderConfirmation, *, now: datetime) -> bool:
        """A locked ``CALLING`` row past its deadline with no final callback:
        ambiguous, so the seller decides."""
        if row.confirmation_state is not S.CALLING or (
            row.next_action_at is not None and now < row.next_action_at
        ):
            return False
        attempt = await self._db.scalar(
            sa.select(ConfirmationAttempt).where(
                ConfirmationAttempt.confirmation_id == row.id,
                ConfirmationAttempt.attempt_no == row.attempt_count,
            )
        )
        if attempt is not None and attempt.status in _LIVE:
            attempt.status = str(IvrStatus.UNKNOWN)
            attempt.ended_at = attempt.ended_at or now
            await self._db.flush()
        await self._confirmations.transition(row, S.SELLER_REVIEW, Cause.CALL_UNKNOWN, now=now)
        return True

    async def _to_seller(self, row: OrderConfirmation, cause: Cause, now: datetime) -> None:
        await self._confirmations.transition(row, S.SELLER_REVIEW, cause, now=now)

    @staticmethod
    def _is_current(row: OrderConfirmation, attempt: ConfirmationAttempt) -> bool:
        return (
            row.confirmation_state is S.CALLING
            and attempt.channel == AttemptChannel.IVR
            and attempt.attempt_no == row.attempt_count
        )

    # --------------------------------------------------------- callbacks ---

    async def apply(
        self, attempt: ConfirmationAttempt, event: VoiceEvent, *, now: datetime
    ) -> VoiceEventResult:
        """One verified, new event for this attempt. Locks the confirmation."""
        row = await self._db.scalar(
            sa.select(OrderConfirmation)
            .where(OrderConfirmation.id == attempt.confirmation_id)
            .with_for_update()
        )
        if event.duration_seconds is not None:
            attempt.duration_seconds = event.duration_seconds
        kind = event.kind
        if event.is_terminal:
            attempt.ended_at = attempt.ended_at or event.occurred_at or now
        if row is None or not (self._is_current(row, attempt) and attempt.status in _LIVE):
            # The call already ended or went to the seller: recorded, ignored.
            if kind is VoiceEventKind.DTMF:
                attempt.dtmf = attempt.dtmf or event.digit
            await self._db.flush()
            return VoiceEventResult.LATE
        if kind in (VoiceEventKind.RINGING, VoiceEventKind.ANSWERED):
            if kind is VoiceEventKind.ANSWERED or attempt.status != IvrStatus.ANSWERED:
                attempt.status = str(kind)
            await self._db.flush()
            return VoiceEventResult.RECORDED
        if kind is VoiceEventKind.DTMF:
            action = _DTMF.get(event.digit or "")
            if action is None:  # a key the script does not offer
                await self._db.flush()
                return VoiceEventResult.IGNORED
            status, target, cause = _ANSWER[action]
            attempt.dtmf = event.digit
            attempt.status = str(status)
            await self._db.flush()
            await self._confirmations.transition(row, target, cause, now=now)
            return VoiceEventResult.APPLIED
        # The call ended with no key we act on.
        attempt.status = str(_ENDED_STATUS[kind])
        await self._db.flush()
        await self._confirmations.transition(row, S.NO_ANSWER, Cause.CALL_NO_ANSWER, now=now)
        return VoiceEventResult.APPLIED

    async def _replay_unmatched(self, attempt: ConfirmationAttempt, now: datetime) -> None:
        """Events that arrived before we stored the provider's reference."""
        waiting = (
            await self._db.scalars(
                sa.select(VoiceCallEvent)
                .where(
                    VoiceCallEvent.provider == attempt.provider,
                    VoiceCallEvent.provider_ref == attempt.provider_ref,
                    VoiceCallEvent.result == str(VoiceEventResult.UNMATCHED),
                )
                .order_by(VoiceCallEvent.created_at, VoiceCallEvent.id)
                .with_for_update()
            )
        ).all()
        for stored in waiting:
            result = await self.apply(attempt, _event_of(stored), now=now)
            stored.result = str(result)
            stored.tenant_id = attempt.tenant_id
            stored.attempt_id = attempt.id
        await self._db.flush()


def _event_of(stored: VoiceCallEvent) -> VoiceEvent:
    return VoiceEvent(
        kind=VoiceEventKind(stored.kind),
        provider_ref=stored.provider_ref,
        client_ref=stored.client_ref,
        digit=stored.digit,
        occurred_at=stored.occurred_at,
        duration_seconds=stored.duration_seconds,
    )


def services(db: AsyncSession) -> IvrCalls:
    from app.api.deps import get_hasher, get_vault
    from app.customers.service import CustomerService
    from app.orders.service import OrderService

    customers = CustomerService(db, hasher=get_hasher(), vault=get_vault())
    return IvrCalls(db, orders=OrderService(db, customers=customers), customers=customers)


# --------------------------------------------------------------------------- #
# The callback route's work
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CallbackResult:
    status_code: int
    state: str
    events: int = 0
    applied: int = 0


async def receive_callback(
    provider: str, headers: Mapping[str, str], body: bytes
) -> CallbackResult:
    """Store, verify, parse, deduplicate and apply one provider callback.

    The body is stored first, whatever it is. Only a body the deployment's
    transport verifies is parsed; an unknown provider or a missing secret is
    refused like a bad signature (the verifier refuses everything then).
    """
    from app.db.session import system_session

    transport = voice_transport()
    if (
        provider != transport.name
        or not _PROVIDER.fullmatch(provider)
        or transport.availability().state is VoiceAvailabilityState.NOT_CONFIGURED
    ):
        # Not a provider this deployment calls through: nothing is stored,
        # so the route cannot be used to fill the table.
        return CallbackResult(404, "PROVIDER_NOT_ACTIVE")
    now = utc_now()
    digest = hashlib.sha256(body).hexdigest()
    async with system_session("voice callback: store") as db:
        delivery = await db.scalar(
            sa.select(VoiceCallbackDelivery).where(
                VoiceCallbackDelivery.provider == provider,
                VoiceCallbackDelivery.body_sha256 == digest,
            )
        )
        if delivery is not None:
            delivery.delivery_count += 1
            if delivery.state == CallbackState.PROCESSED:
                return CallbackResult(200, "DUPLICATE", delivery.event_count)
        else:
            delivery = VoiceCallbackDelivery(
                provider=provider,
                state=str(CallbackState.RECEIVED),
                raw_body=body.decode("utf-8", errors="replace"),
                body_sha256=digest,
                header_names=sorted({name.lower() for name in headers}),
                received_at=now,
            )
            db.add(delivery)
        await db.flush()
        delivery_id = delivery.id

    if not await transport.verify_callback(headers, body):
        await _mark(delivery_id, CallbackState.REJECTED, "SIGNATURE_INVALID")
        return CallbackResult(401, str(CallbackState.REJECTED))
    try:
        events = transport.parse_callback(body)
    except ValueError:
        await _mark(delivery_id, CallbackState.MALFORMED, "BODY_MALFORMED")
        return CallbackResult(400, str(CallbackState.MALFORMED))

    applied = 0
    for event in events:
        if await _ingest(provider, event, delivery_id, now) is VoiceEventResult.APPLIED:
            applied += 1
    await _mark(delivery_id, CallbackState.PROCESSED, None, events=len(events))
    return CallbackResult(200, str(CallbackState.PROCESSED), len(events), applied)


async def _mark(
    delivery_id: uuid.UUID, state: CallbackState, reason: str | None, *, events: int = 0
) -> None:
    from app.db.session import system_session

    async with system_session("voice callback: outcome") as db:
        delivery = await db.get(VoiceCallbackDelivery, delivery_id)
        if delivery is not None:
            delivery.state = str(state)
            delivery.reason = reason
            delivery.event_count = events
            delivery.processed_at = utc_now()


async def _find_attempt(provider: str, event: VoiceEvent) -> tuple[uuid.UUID, uuid.UUID] | None:
    """``(tenant_id, attempt_id)`` of our call, across shops: by our own
    reference when the provider echoes it, else by the provider's."""
    from app.db.session import system_session

    ivr = (
        ConfirmationAttempt.channel == str(AttemptChannel.IVR),
        ConfirmationAttempt.provider == provider,
    )
    async with system_session("voice callback: find the call") as db:
        match = _CLIENT_REF.fullmatch(event.client_ref or "")
        if match is not None:
            try:
                confirmation_id = uuid.UUID(match.group(1))
            except ValueError:
                confirmation_id = None
            if confirmation_id is not None:
                found = (
                    await db.execute(
                        sa.select(ConfirmationAttempt.tenant_id, ConfirmationAttempt.id).where(
                            *ivr,
                            ConfirmationAttempt.confirmation_id == confirmation_id,
                            ConfirmationAttempt.attempt_no == int(match.group(2)),
                        )
                    )
                ).first()
                if found is not None:
                    return found[0], found[1]
        found = (
            await db.execute(
                sa.select(ConfirmationAttempt.tenant_id, ConfirmationAttempt.id)
                .where(*ivr, ConfirmationAttempt.provider_ref == event.provider_ref)
                .limit(1)
            )
        ).first()
        return (found[0], found[1]) if found is not None else None


def _event_key(event: VoiceEvent) -> str:
    """The dedupe key: the kind, or ``DTMF:<digit>`` for a key press."""
    if event.kind is VoiceEventKind.DTMF:
        return f"{event.kind}:{event.digit}"
    return str(event.kind)


def _event_row(
    provider: str, event: VoiceEvent, delivery_id: uuid.UUID, result: VoiceEventResult
) -> VoiceCallEvent:
    return VoiceCallEvent(
        provider=provider,
        provider_ref=event.provider_ref[:120],
        event_key=_event_key(event),
        kind=str(event.kind),
        digit=event.digit,
        client_ref=(event.client_ref or None) and str(event.client_ref)[:80],
        occurred_at=event.occurred_at,
        duration_seconds=event.duration_seconds,
        result=str(result),
        delivery_id=delivery_id,
    )


def _same_event(provider: str, event: VoiceEvent) -> Any:
    return sa.select(VoiceCallEvent).where(
        VoiceCallEvent.provider == provider,
        VoiceCallEvent.provider_ref == event.provider_ref[:120],
        VoiceCallEvent.event_key == _event_key(event),
    )


async def _ingest(
    provider: str, event: VoiceEvent, delivery_id: uuid.UUID, now: datetime
) -> VoiceEventResult | None:
    """One event: ``None`` when it was already seen."""
    from app.core.context import ActorType, RequestContext, clear_context, set_context
    from app.db.session import session_scope, system_session

    found = await _find_attempt(provider, event)
    if found is None:
        async with system_session("voice callback: unmatched event") as db:
            if await db.scalar(_same_event(provider, event)) is not None:
                return None
            db.add(_event_row(provider, event, delivery_id, VoiceEventResult.UNMATCHED))
        return VoiceEventResult.UNMATCHED

    tenant_id, attempt_id = found
    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="voice_callback",
        )
    )
    try:
        async with session_scope() as db:
            attempt = await db.get(ConfirmationAttempt, attempt_id)
            if attempt is None:
                return None
            calls = services(db)
            # Lock the confirmation first, so a concurrent copy of this event
            # waits here and then finds the stored row.
            await db.scalar(
                sa.select(OrderConfirmation.id)
                .where(OrderConfirmation.id == attempt.confirmation_id)
                .with_for_update()
            )
            stored = await db.scalar(_same_event(provider, event))
            if stored is not None and stored.result != VoiceEventResult.UNMATCHED:
                return None
            result = await calls.apply(attempt, event, now=now)
            if stored is None:
                stored = _event_row(provider, event, delivery_id, result)
                db.add(stored)
            stored.result = str(result)
            stored.tenant_id = tenant_id
            stored.attempt_id = attempt.id
            await db.flush()
        log.info(
            "voice event",
            extra={
                "provider": provider,
                "kind": str(event.kind),
                "result": str(result),
                "attempt_id": str(attempt_id),
            },
        )
        return result
    finally:
        clear_context(token)
