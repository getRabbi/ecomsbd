"""Asking a courier to return a parcel.

Brief sections 20 and 28.

A return request is an **external side effect that costs money** — a courier
collects a parcel and charges for it. Steadfast's V1 documentation gives the
endpoint and the fields and says nothing at all about idempotency, so the
duplicate protection has to be ours:

*   a deterministic idempotency key, unique per shop, so two taps of the same
    button collide in the database rather than at the courier;
*   a row lock plus an active-request check, so two devices in the same second
    cannot both get past it;
*   a lost answer becomes ``UNKNOWN`` and **blocks** a second request, exactly
    as an ambiguous booking does. The answer being lost does not mean the
    request was not received.

The other rule is brief section 28's: this module never touches stock. A
completed return request is the provider saying it finished *its* task. Whether
the parcel came back and its units went on the shelf is a delivery outcome, and
it goes through the existing return workflow in
:class:`~app.consignments.service.ConsignmentService`, which restores stock,
closes the receivable and snapshots profit together.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.couriers.accounts import CourierAccountService
from app.couriers.capabilities import Capability, load_manifest
from app.couriers.events import record_courier_event
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import (
    CourierEventKind,
    CourierEventSource,
    CourierRawPayload,
    CourierReturnRequest,
    RawPayloadKind,
    ReturnRequestState,
    payload_hash,
)
from app.couriers.steadfast.contract import ReturnRequestStatus
from app.couriers.steadfast.errors import SteadfastError
from app.couriers.steadfast.mapping import map_return_status

__all__ = ["CourierReturnService", "ReturnRequestResult"]

log = get_logger(__name__)

#: Provider status -> ecomsbd's own view of the request. Kept separate from the
#: parcel's state on purpose: "the courier accepted my return request" and "the
#: parcel is back on my shelf" are different facts, and the second one is not
#: this table's to assert.
_PROVIDER_STATE: dict[str, ReturnRequestState] = {
    str(ReturnRequestStatus.PENDING): ReturnRequestState.REQUESTED,
    str(ReturnRequestStatus.APPROVED): ReturnRequestState.ACKNOWLEDGED,
    str(ReturnRequestStatus.PROCESSING): ReturnRequestState.IN_PROGRESS,
    str(ReturnRequestStatus.COMPLETED): ReturnRequestState.COMPLETED,
    str(ReturnRequestStatus.CANCELLED): ReturnRequestState.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class ReturnRequestResult:
    """What asking for a return produced."""

    request_id: uuid.UUID
    consignment_id: uuid.UUID
    state: ReturnRequestState
    provider_return_id: str | None
    provider_status: str | None
    message: str

    @property
    def is_ambiguous(self) -> bool:
        return self.state is ReturnRequestState.UNKNOWN


class CourierReturnService:
    """Requests returns, and keeps their provider state in step."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        accounts: CourierAccountService,
    ) -> None:
        self._db = session
        self._accounts = accounts

    # ------------------------------------------------------------ requesting --

    async def request_return(
        self,
        consignment_id: uuid.UUID,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> ReturnRequestResult:
        """Ask the provider to return one parcel.

        Refuses rather than duplicating whenever an earlier request for this
        parcel is still live — including one whose answer was lost. A courier
        collecting the same parcel twice is a charge the seller did not agree
        to and cannot undo.
        """
        consignment = await self._lock_consignment(consignment_id)

        manifest = load_manifest(consignment.provider)
        if manifest is None or not manifest.supports(Capability.RETURNS):
            raise ConflictError(
                f"{consignment.provider} does not support return requests through its API. "
                "Contact the courier directly.",
                details={"provider": consignment.provider},
            )

        if consignment.consignment_status.is_ambiguous:
            # The parcel's own existence is unconfirmed. Asking a courier to
            # return a parcel we are not sure it has is not a question it can
            # answer usefully.
            raise ConflictError(
                "This parcel's booking has not been confirmed yet, so a return "
                "cannot be requested.",
                code=ErrorCode.BOOKING_AMBIGUOUS,
                details={"consignment_id": str(consignment_id)},
            )

        existing = await self._active_request(consignment_id)
        if existing is not None:
            record_metric(
                CourierMetric.RETURN_DUPLICATE_PREVENTED,
                provider=consignment.provider,
                state=existing.state,
            )
            raise ConflictError(
                _already_requested_message(existing.request_state),
                code=ErrorCode.RETURN_ALREADY_REQUESTED,
                details={
                    "return_request_id": str(existing.id),
                    "state": existing.state,
                },
            )

        account = await self._accounts.usable_account(consignment.provider)
        if account is None:
            raise ConflictError(
                "That courier account is not connected, so a return cannot be requested.",
                code=ErrorCode.COURIER_ACCOUNT_NEEDS_RECONNECT,
                details={"provider": consignment.provider},
            )
        adapter = self._accounts.adapter_for(consignment.provider)
        if adapter is None:
            raise ValidationError(f"{consignment.provider} has no courier adapter")

        reference_kind, reference_value = _reference_for(consignment)
        key = idempotency_key or _idempotency_key(consignment_id, reference_value)

        # Persisted before the call, for the same reason a booking attempt is:
        # if the answer is lost, the row is what stops a second request.
        request = CourierReturnRequest(
            provider=consignment.provider,
            courier_account_id=account.id,
            consignment_id=consignment_id,
            reference_kind=reference_kind,
            reference_value=reference_value,
            state=str(ReturnRequestState.UNKNOWN),
            reason=reason,
            idempotency_key=key,
            requested_by=current_context().user_id,
            requested_at=utc_now(),
        )
        self._db.add(request)
        await self._db.flush()
        await self._db.commit()

        credentials = self._accounts.credentials_for(account)
        try:
            record = await adapter.create_return_request(  # type: ignore[attr-defined]
                credentials,
                **{reference_kind: reference_value},
                reason=reason,
            )
        except SteadfastError as exc:
            # The request may have been received. It stays UNKNOWN, which
            # blocks a second one, and a person resolves it if it never
            # settles. Retrying here is how a parcel gets collected twice.
            request.state = str(ReturnRequestState.UNKNOWN)
            await self._db.flush()
            log.warning(
                "return request outcome unknown",
                extra={
                    "provider": consignment.provider,
                    "operation": "create_return_request",
                    "error_kind": str(exc.kind),
                    "consignment_id": str(consignment_id),
                },
            )
            return ReturnRequestResult(
                request_id=request.id,
                consignment_id=consignment_id,
                state=ReturnRequestState.UNKNOWN,
                provider_return_id=None,
                provider_status=None,
                message=("রিটার্ন রিকোয়েস্টের ফলাফল নিশ্চিত হয়নি — আবার পাঠাবেন না। আমরা যাচাই করছি।"),
            )

        raw = await self._store_raw(consignment, account.id, record.raw)
        request.raw_payload_id = raw.id
        request.provider_return_id = record.provider_return_id
        request.provider_status = record.status
        request.state = str(_PROVIDER_STATE.get(record.status or "", ReturnRequestState.REQUESTED))
        request.last_synced_at = utc_now()
        await self._db.flush()

        await self._apply_to_parcel(consignment, request)

        await record_courier_event(
            self._db,
            provider=consignment.provider,
            kind=CourierEventKind.RETURN,
            source=CourierEventSource.BOOKING_CALL,
            consignment=consignment,
            raw_status=record.status,
            raw_payload_id=raw.id,
        )
        await record_audit(
            self._db,
            AuditAction.COURIER_RETURN_REQUESTED,
            entity_type="consignment",
            entity_id=consignment_id,
            reason=reason,
            context={
                "provider": consignment.provider,
                "provider_return_id": record.provider_return_id,
                "reference_kind": reference_kind,
                "provider_status": record.status,
            },
        )
        record_metric(CourierMetric.RETURN_REQUESTED, provider=consignment.provider)
        return ReturnRequestResult(
            request_id=request.id,
            consignment_id=consignment_id,
            state=request.request_state,
            provider_return_id=record.provider_return_id,
            provider_status=record.status,
            message="The courier has accepted the return request.",
        )

    # -------------------------------------------------------------- syncing --

    async def sync_open_requests(self, *, provider: str = "steadfast", limit: int = 100) -> int:
        """Refresh return requests that have not reached a terminal state.

        Returns how many changed. Uses the documented single-lookup endpoint per
        request rather than the list endpoint, because the list endpoint's
        response schema is undocumented and paging through an unknown shape to
        find one id is more guesswork for less certainty.
        """
        account = await self._accounts.usable_account(provider)
        if account is None:
            return 0
        adapter = self._accounts.adapter_for(provider)
        if adapter is None:
            return 0

        open_states = [str(state) for state in ReturnRequestState if state.blocks_a_new_request]
        rows = list(
            (
                await self._db.execute(
                    sa.select(CourierReturnRequest)
                    .where(
                        CourierReturnRequest.provider == provider,
                        CourierReturnRequest.state.in_(open_states),
                        CourierReturnRequest.provider_return_id.is_not(None),
                    )
                    .order_by(CourierReturnRequest.requested_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0

        credentials = self._accounts.credentials_for(account)
        changed = 0
        for request in rows:
            try:
                call = await adapter.client.get_return_request(  # type: ignore[attr-defined]
                    credentials, str(request.provider_return_id)
                )
            except SteadfastError:
                # Unreachable right now. The request keeps its state and is
                # asked about again next run.
                continue

            record = call.value
            if not record.status or record.status == request.provider_status:
                request.last_synced_at = utc_now()
                continue

            request.provider_status = record.status
            request.state = str(_PROVIDER_STATE.get(record.status, ReturnRequestState.IN_PROGRESS))
            request.last_synced_at = utc_now()
            changed += 1

            consignment = await self._db.get(Consignment, request.consignment_id)
            if consignment is not None:
                await self._apply_to_parcel(consignment, request)
                await record_courier_event(
                    self._db,
                    provider=provider,
                    kind=CourierEventKind.RETURN,
                    source=CourierEventSource.POLL,
                    consignment=consignment,
                    raw_status=record.status,
                    correlation_id=call.correlation_id,
                )
        await self._db.flush()
        return changed

    # ------------------------------------------------------------- reading --

    async def for_consignment(self, consignment_id: uuid.UUID) -> list[CourierReturnRequest]:
        result = await self._db.execute(
            sa.select(CourierReturnRequest)
            .where(CourierReturnRequest.consignment_id == consignment_id)
            .order_by(CourierReturnRequest.requested_at.asc())
        )
        return list(result.scalars().all())

    # ----------------------------------------------------------- internals --

    async def _apply_to_parcel(
        self, consignment: Consignment, request: CourierReturnRequest
    ) -> None:
        """Move the parcel's *visible* status to match the return request.

        Visible only. A completed return request does not restore stock or
        close a receivable here — that is a delivery outcome, and it goes
        through :class:`~app.consignments.service.ConsignmentService` so the
        stock movement, the receivable and the profit snapshot happen together
        (brief section 28).
        """
        target = map_return_status(request.provider_status)
        if target is None:
            return
        if consignment.consignment_status.is_terminal:
            return
        if consignment.consignment_status is target:
            return
        consignment.status = str(target)
        consignment.last_status_at = utc_now()
        await self._db.flush()

    async def _lock_consignment(self, consignment_id: uuid.UUID) -> Consignment:
        stmt = sa.select(Consignment).where(Consignment.id == consignment_id)
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            stmt = stmt.with_for_update()
        consignment = (await self._db.execute(stmt)).scalar_one_or_none()
        if consignment is None:
            raise NotFoundError("Consignment not found")
        return consignment

    async def _active_request(self, consignment_id: uuid.UUID) -> CourierReturnRequest | None:
        """An earlier request that forbids a new one.

        ``UNKNOWN`` counts. A lost answer may well have created a request at the
        provider, and asking again is how a parcel gets collected twice.
        """
        blocking = [str(state) for state in ReturnRequestState if state.blocks_a_new_request]
        result = await self._db.execute(
            sa.select(CourierReturnRequest)
            .where(
                CourierReturnRequest.consignment_id == consignment_id,
                CourierReturnRequest.state.in_(blocking),
            )
            .order_by(CourierReturnRequest.requested_at.desc())
        )
        return result.scalars().first()

    async def _store_raw(
        self, consignment: Consignment, account_id: uuid.UUID, payload: dict
    ) -> CourierRawPayload:
        row = CourierRawPayload(
            provider=consignment.provider,
            courier_account_id=account_id,
            kind=str(RawPayloadKind.RETURN_RESPONSE),
            endpoint="/create_return_request",
            response_payload=payload,
            payload_sha256=payload_hash(payload),
            received_at=utc_now(),
        )
        self._db.add(row)
        await self._db.flush()
        return row


def _reference_for(consignment: Consignment) -> tuple[str, str]:
    """Which of the three documented references to send, and its value.

    Exactly one goes out. The document says "consignment id **or** invoice
    **or** tracking code" and does not say which wins if several are sent, so
    sending several would leave the choice to the provider.
    """
    if consignment.provider_consignment_id:
        return "consignment_id", consignment.provider_consignment_id
    if consignment.tracking_code:
        return "tracking_code", consignment.tracking_code
    return "invoice", consignment.merchant_reference


def _idempotency_key(consignment_id: uuid.UUID, reference: str) -> str:
    """Deterministic per parcel, so a repeated tap collides here.

    Not time-based: a key that changed per request would let the second tap
    through, which is the exact thing this prevents.
    """
    material = f"return:{consignment_id}:{reference}"
    return hashlib.sha256(material.encode()).hexdigest()[:64]


def _already_requested_message(state: ReturnRequestState) -> str:
    if state is ReturnRequestState.UNKNOWN:
        return (
            "A return request was already sent for this parcel and the courier's "
            "answer was not confirmed. Do not send another — we are checking."
        )
    return "A return has already been requested for this parcel."
