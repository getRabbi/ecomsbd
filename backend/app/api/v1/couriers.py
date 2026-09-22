"""Courier account endpoints.

Master spec section 39; brief sections 3, 4, 5, 50.

The shape of this module is decided by one rule: **a credential goes in and
never comes out.** There is no endpoint that returns an API key, no field on any
response model that could hold one, and the single serialiser every route uses
is :meth:`~app.couriers.models.CourierAccount.public_view`, so there is one
place to audit rather than one per route.

Permissions are checked server-side on every route. ``COURIER_CREDENTIAL_MANAGE``
is an OWNER-only permission, which is what keeps a packer — who can book —
away from the keys that do the booking.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps import (
    BookingRecoveryDep,
    CourierAccountsDep,
    CourierBookingDep,
    CourierReturnsDep,
    DbSession,
    FeatureFlagsDep,
    PaymentSyncDep,
    Principal,
    require_permission,
)
from app.common.feature_flags import COURIER_PROVIDER_FLAGS
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.couriers.accounts import ConnectRequest
from app.couriers.capabilities import load_manifest
from app.couriers.credentials import spec_for
from app.couriers.events import recent_events_for
from app.tenants.roles import Permission

router = APIRouter(prefix="/couriers", tags=["couriers"])

Booker = Annotated[Principal, Depends(require_permission(Permission.ORDER_BOOK))]
CredentialManager = Annotated[
    Principal, Depends(require_permission(Permission.COURIER_CREDENTIAL_MANAGE))
]


class CourierConnectPayload(BaseModel):
    """The one request body in this file that carries secrets.

    Values are constrained but not pattern-matched: no provider states a key
    format, and rejecting a valid key because we guessed its shape would be
    worse than passing it to the provider and letting the provider decide.

    Two ways in, because there are two generations of client:

    *   ``api_key``/``secret_key`` — what V1 sends, and still exactly right for
        Steadfast.
    *   ``credentials`` — a map keyed by the field names the provider declares
        in ``connect_form`` (``client_id``/``client_secret`` for Pathao). A
        client that renders the form from the declaration posts it back the
        same way, so adding a provider does not change this model.

    Whichever arrives, the provider's declaration decides which value goes in
    which encrypted slot.
    """

    api_key: str | None = Field(default=None, min_length=1, max_length=300)
    secret_key: str | None = Field(default=None, min_length=1, max_length=300)
    #: Provider-declared credential fields, by name.
    credentials: dict[str, str] = Field(default_factory=dict)
    #: Non-secret provider settings — ``sandbox``, ``store_id``. Echoed back to
    #: the seller, so a secret must never be put here.
    config: dict[str, Any] = Field(default_factory=dict)
    #: The provider's webhook signing secret, where it has one. Stored
    #: encrypted; there is no endpoint that reads it back.
    webhook_secret: str | None = Field(default=None, max_length=300)
    label: str | None = Field(default=None, max_length=80)


class CourierStoreResponse(BaseModel):
    """A pickup store on the provider's side."""

    provider_store_id: str
    name: str
    address: str | None = None


class SelectStorePayload(BaseModel):
    provider_store_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)


class WebhookSetupResponse(BaseModel):
    """What a seller needs to wire a provider callback up, and nothing more.

    The callback URL contains this account's routing token, so it is shown only
    to someone who may already manage the credentials. The secret is never
    returned — ``secret_configured`` says whether one is stored, which is the
    only thing a settings screen actually needs to render.
    """

    provider: str
    supported: bool
    callback_url: str | None = None
    secret_configured: bool = False
    #: Why this matters for this provider, in the seller's terms.
    help_en: str | None = None
    help_bn: str | None = None


class CourierAccountResponse(BaseModel):
    """What the client gets back. Note what is absent.

    No ``api_key``, no ``secret_key``, no ciphertext, no key version. The
    masked identifier is the only credential-derived value, and it is the last
    four characters of the key — enough to recognise which account is
    connected, useless to anyone who obtains it.
    """

    id: str
    provider: str
    label: str | None
    status: str
    connected: bool
    needs_reconnect: bool
    masked_identifier: str | None
    last_verified_at: str | None
    last_validation_result: str | None
    last_validation_message: str | None
    capabilities: dict[str, str]
    #: The provider's own reported account balance. Labelled separately from
    #: anything ecomsbd calculates, and never added to the COD outstanding
    #: total: they measure different things (brief section 19).
    reported_balance_paisa: int | None
    reported_balance_at: str | None
    #: Non-secret provider settings: the chosen pickup store, the sandbox flag.
    config: dict[str, Any] = Field(default_factory=dict)
    #: Whether a webhook secret is stored for this account. Never the secret.
    webhook_configured: bool = False


class ConnectionTestResponse(BaseModel):
    """The outcome of checking credentials.

    ``result`` is four-valued on purpose. ``PROVIDER_UNAVAILABLE`` and
    ``UNKNOWN`` mean "we did not find out" and must render differently from
    ``INVALID`` — telling a seller their working key is wrong because the
    courier had a bad minute is how trust in the connection screen is lost.
    """

    result: str
    message: str
    account: CourierAccountResponse | None = None


def _response(view: dict[str, Any]) -> CourierAccountResponse:
    return CourierAccountResponse(**view)


@router.get(
    "/accounts",
    response_model=list[CourierAccountResponse],
    summary="Courier accounts connected to this shop",
)
async def list_accounts(
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> list[CourierAccountResponse]:
    rows = await accounts.list_accounts()
    return [_response(row.public_view()) for row in rows]


@router.post(
    "/accounts/{provider}/connect",
    response_model=ConnectionTestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a courier account",
)
async def connect_account(
    provider: str,
    payload: CourierConnectPayload,
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> ConnectionTestResponse:
    """Validate and store credentials.

    Validated first: a mistyped key never becomes a saved account that quietly
    fails every booking. A rejection returns ``INVALID_COURIER_CREDENTIALS`` and
    stores nothing.
    """
    primary, secondary = _resolve_credential_slots(provider, payload)
    account, outcome = await accounts.connect(
        ConnectRequest(
            provider=provider,
            api_key=primary,
            secret_key=secondary,
            label=payload.label,
            config=payload.config,
            webhook_secret=payload.webhook_secret,
        )
    )
    return ConnectionTestResponse(
        result=str(outcome.result),
        message=outcome.message,
        account=_response(account.public_view()),
    )


def _resolve_credential_slots(provider: str, payload: CourierConnectPayload) -> tuple[str, str]:
    """Decide which submitted value fills which encrypted slot.

    The provider's own declaration answers this, so the mapping lives in one
    place instead of being re-derived by every client and every route.
    """
    spec = spec_for(provider)
    if spec is not None and payload.credentials:
        primary = (payload.credentials.get(spec.primary) or "").strip()
        secondary = (payload.credentials.get(spec.secondary) or "").strip()
        if not primary or not secondary:
            primary_field = spec.field_named(spec.primary)
            secondary_field = spec.field_named(spec.secondary)
            raise ValidationError(
                f"{provider} needs both "
                f"{primary_field.label_en if primary_field else spec.primary} and "
                f"{secondary_field.label_en if secondary_field else spec.secondary}",
                details={"provider": provider},
            )
        return primary, secondary

    # The V1 path, still exactly right for a two-key provider.
    if not payload.api_key or not payload.secret_key:
        raise ValidationError(
            "Courier credentials are required",
            details={"provider": provider},
        )
    return payload.api_key, payload.secret_key


class BookableCourierResponse(BaseModel):
    """Whether one courier can take a booking from this shop right now.

    ``reason`` is a stable code, not a sentence: the mobile and web clients
    branch on it and each renders its own copy in the seller's language. Every
    value is derived from the capability manifest, the credential declaration,
    the account's state and provider health — never from a provider name, so
    the booking UI stays provider-independent.
    """

    provider: str
    display_name: str
    bookable: bool
    #: ``NOT_ENABLED``, ``NO_VERIFIED_CREATE``, ``NOT_CONNECTED``,
    #: ``NEEDS_RECONNECT``, ``NEEDS_PICKUP_STORE`` or ``PROVIDER_UNAVAILABLE``.
    reason: str | None = None
    #: Whether this courier needs a pickup store at all, so the client can
    #: offer the fix rather than only reporting the problem.
    requires_store: bool = False
    store_name: str | None = None
    #: Whether the booking form may offer a delivery-type choice for this
    #: courier. Read by the booking sheet so it never checks a provider name.
    supports_delivery_type: bool = False


@router.get(
    "/bookable",
    response_model=list[BookableCourierResponse],
    summary="Couriers this shop can book with right now",
)
async def bookable_couriers(
    principal: Booker,
    accounts: CourierAccountsDep,
    flags: FeatureFlagsDep,
) -> list[BookableCourierResponse]:
    """The courier picker's source of truth.

    Answers with every courier that *could* be a booking option, each marked
    bookable or not with the first thing a seller would have to fix. A courier
    with nothing to connect — manual mode, or one with no verified contract —
    is absent entirely rather than listed as broken.

    ``ORDER_BOOK`` rather than ``COURIER_CREDENTIAL_MANAGE``: an operator who
    books parcels needs to know which couriers are available, without being
    given access to the keys that do the booking.
    """
    enabled = {
        provider: await flags.is_enabled(flag, tenant_id=principal.tenant_id)
        for provider, flag in COURIER_PROVIDER_FLAGS.items()
    }
    rows = await accounts.bookable_couriers(enabled=enabled)
    return [BookableCourierResponse(**row.as_dict()) for row in rows]


@router.get(
    "/accounts/{provider}/stores",
    response_model=list[CourierStoreResponse],
    summary="Pickup stores on a connected courier account",
)
async def list_stores(
    provider: str,
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> list[CourierStoreResponse]:
    """List the provider's pickup stores for this shop.

    Only meaningful for a provider whose bookings require one — Pathao's
    ``store_id`` is mandatory on every create and it publishes no default, so a
    connected Pathao account is not a bookable one until a store is chosen.
    A provider without stores returns an empty list rather than an error.
    """
    account = await accounts.for_provider(provider)
    if account is None or not account.has_credentials:
        raise NotFoundError("No courier account is connected for that provider")

    adapter = accounts.adapter_for(provider)
    if adapter is None:
        raise NotFoundError(f"{provider} is not a courier ecomsbd can connect to")

    result = await adapter.list_stores(accounts.credentials_for(account))
    if not isinstance(result, list) or not result:
        # ``Unavailable`` is falsy and carries its reason. A provider with no
        # store concept is not an error state.
        return []
    return [
        CourierStoreResponse(
            provider_store_id=store.provider_store_id,
            name=store.name,
            address=store.address,
        )
        for store in result
    ]


@router.post(
    "/accounts/{provider}/store",
    response_model=CourierAccountResponse,
    summary="Choose the pickup store bookings are made from",
)
async def select_store(
    provider: str,
    payload: SelectStorePayload,
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> CourierAccountResponse:
    """Record which pickup store this shop books from.

    Stored as account config, not as a credential: it is not secret, it is
    shown back to the seller, and every booking reads it.
    """
    account = await accounts.select_store(
        provider,
        provider_store_id=payload.provider_store_id,
        name=payload.name,
    )
    return _response(account.public_view())


@router.get(
    "/accounts/{provider}/webhook",
    response_model=WebhookSetupResponse,
    summary="Callback URL to paste into the provider's panel",
)
async def webhook_setup(
    provider: str,
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> WebhookSetupResponse:
    """The callback URL for this shop, and whether a secret is stored.

    For Pathao this is not optional housekeeping: Pathao publishes no status
    lookup, so a parcel's state only ever advances when a callback arrives.
    """
    spec = spec_for(provider)
    if spec is None or not spec.uses_webhook:
        return WebhookSetupResponse(provider=provider, supported=False)

    account = await accounts.for_provider(provider)
    if account is None or not account.has_credentials:
        raise NotFoundError("No courier account is connected for that provider")

    token = await accounts.ensure_webhook_token(account)
    settings = get_settings()
    base = (settings.public_base_url or "").rstrip("/")
    return WebhookSetupResponse(
        provider=provider,
        supported=True,
        callback_url=f"{base}/v1/webhooks/couriers/{provider}/{token}" if base else None,
        secret_configured=bool(account.webhook_secret_encrypted),
        help_en=spec.webhook_help_en,
        help_bn=spec.webhook_help_bn,
    )


@router.post(
    "/accounts/{provider}/test",
    response_model=ConnectionTestResponse,
    summary="Re-check stored credentials",
)
async def test_account(
    provider: str,
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> ConnectionTestResponse:
    """Ask the provider whether the stored credentials still work.

    Uses the safest documented read the provider offers — for Steadfast,
    ``GET /get_balance``. No parcel is created to test a key.
    """
    outcome = await accounts.test_connection(provider)
    account = await accounts.for_provider(provider)
    return ConnectionTestResponse(
        result=str(outcome.result),
        message=outcome.message,
        account=_response(account.public_view()) if account else None,
    )


@router.delete(
    "/accounts/{provider}",
    response_model=CourierAccountResponse,
    summary="Disconnect a courier account",
)
async def disconnect_account(
    provider: str,
    principal: CredentialManager,
    accounts: CourierAccountsDep,
) -> CourierAccountResponse:
    """Erase the stored credentials, keep the account row.

    The row survives because parcels booked with this account reference it, and
    an audit trail that loses the account a booking was made with cannot answer
    the only question worth asking after an incident.
    """
    account = await accounts.disconnect(provider)
    return _response(account.public_view())


class ProviderEvidenceResponse(BaseModel):
    """What is known about a provider's integration, and what is not.

    Served to the settings screen so the app can be honest about gaps — "status
    arrives by polling because this courier documents no webhook" reads very
    differently from a status section that is simply empty.
    """

    provider: str
    documentation_version: str | None
    documentation_source: str | None
    verified_at: str | None
    capabilities: dict[str, str]
    unknowns: dict[str, str]
    blockers: list[str]
    manual_fallback: str | None


@router.get(
    "/providers/{provider}/evidence",
    response_model=ProviderEvidenceResponse,
    summary="What a provider's documentation does and does not say",
)
async def provider_evidence(
    provider: str,
    principal: CredentialManager,
) -> ProviderEvidenceResponse:
    manifest = load_manifest(provider)
    if manifest is None:
        raise NotFoundError("No integration manifest for that provider")
    return ProviderEvidenceResponse(
        provider=manifest.provider,
        documentation_version=manifest.documentation_version,
        documentation_source=manifest.documentation_source,
        verified_at=manifest.verified_at.isoformat() if manifest.verified_at else None,
        capabilities={str(k): str(v) for k, v in manifest.capabilities.items()},
        unknowns=dict(manifest.unknowns),
        blockers=list(manifest.blockers),
        manual_fallback=manifest.manual_fallback,
    )


# --------------------------------------------------------------- booking --


class BookOrderPayload(BaseModel):
    """Optional extras a seller may attach to one booking.

    Only fields the provider's documentation actually defines. There is no
    weight, no parcel size and no insurance value here, because Steadfast V1
    documents none of them, and a field the courier ignores is a field that
    misleads the seller who filled it in.
    """

    note: str | None = Field(default=None, max_length=400)
    item_description: str | None = Field(default=None, max_length=400)
    #: 0 = home delivery, 1 = point delivery / hub pick-up. The only two values
    #: the documentation defines.
    delivery_type: int | None = Field(default=None, ge=0, le=1)


class BulkBookPayload(BaseModel):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=400)


class BookingItemResponse(BaseModel):
    order_id: str
    consignment_id: str | None
    merchant_reference: str
    outcome: str
    tracking_code: str | None = None
    error_code: str | None = None
    message: str | None = None


class BookingReportResponse(BaseModel):
    """What a booking call did.

    ``ambiguous`` is a first-class count, not an error. The client renders it
    as "we are checking with the courier — do not book again", and offers no
    retry affordance for those orders (brief section 32).
    """

    provider: str
    batch_id: str | None
    booked: int
    ambiguous: int
    failed: int
    items: list[BookingItemResponse]


@router.post(
    "/orders/{order_id}/book",
    response_model=BookingReportResponse,
    summary="Book one order with a courier",
)
async def book_order(
    order_id: uuid.UUID,
    payload: BookOrderPayload,
    principal: Booker,
    booking: CourierBookingDep,
    provider: str = "steadfast",
) -> BookingReportResponse:
    """Create one parcel at the provider.

    Returns a report rather than raising for a provider-side outcome: a refusal
    and an unconfirmed result are both things the seller must see, and an
    exception would flatten them into "something went wrong".
    """
    report = await booking.book(
        order_id,
        provider=provider,
        note=payload.note,
        item_description=payload.item_description,
        delivery_type=payload.delivery_type,
    )
    return BookingReportResponse(**report.as_dict())


@router.post(
    "/orders/book-bulk",
    response_model=BookingReportResponse,
    summary="Book several orders with a courier",
)
async def book_orders_bulk(
    payload: BulkBookPayload,
    principal: Booker,
    booking: CourierBookingDep,
    provider: str = "steadfast",
) -> BookingReportResponse:
    """Book a selection of orders.

    Chunked below the provider's documented maximum, with one booking attempt
    persisted per order before its chunk is sent. A chunk whose answer is lost
    marks its own orders unconfirmed and is never resent.
    """
    report = await booking.book_bulk(payload.order_ids, provider=provider, note=payload.note)
    return BookingReportResponse(**report.as_dict())


class BookingAttemptResponse(BaseModel):
    """One attempt at a parcel, and what became of it."""

    id: str
    attempt_number: int
    state: str
    merchant_reference: str
    provider_consignment_id: str | None
    tracking_code: str | None
    error_code: str | None
    recovery_attempts: int
    recovery_note: str | None
    started_at: str
    completed_at: str | None


class CourierEventResponse(BaseModel):
    """One observation of what the provider said.

    ``observed_at`` is when ecomsbd asked, not when the courier acted:
    Steadfast's status response carries no timestamp, and presenting an
    observation time as an event time would invent a history.
    """

    kind: str
    source: str
    raw_status: str | None
    normalized_status: str | None
    status_undocumented: bool
    observed_at: str
    last_seen_at: str
    observation_count: int


class ConsignmentTrackingResponse(BaseModel):
    attempts: list[BookingAttemptResponse]
    events: list[CourierEventResponse]


@router.get(
    "/consignments/{consignment_id}/tracking",
    response_model=ConsignmentTrackingResponse,
    summary="A parcel's booking attempts and provider observations",
)
async def consignment_tracking(
    consignment_id: uuid.UUID,
    principal: Booker,
    recovery: BookingRecoveryDep,
    db: DbSession,
) -> ConsignmentTrackingResponse:
    attempts = await recovery.attempts_for(consignment_id)
    events = await recent_events_for(db, consignment_id)
    return ConsignmentTrackingResponse(
        attempts=[
            BookingAttemptResponse(
                id=str(attempt.id),
                attempt_number=attempt.attempt_number,
                state=attempt.state,
                merchant_reference=attempt.merchant_reference,
                provider_consignment_id=attempt.provider_consignment_id,
                tracking_code=attempt.tracking_code,
                error_code=attempt.error_code,
                recovery_attempts=attempt.recovery_attempts,
                recovery_note=attempt.recovery_note,
                started_at=attempt.started_at.isoformat(),
                completed_at=attempt.completed_at.isoformat() if attempt.completed_at else None,
            )
            for attempt in attempts
        ],
        events=[
            CourierEventResponse(
                kind=event.kind,
                source=event.source,
                raw_status=event.raw_status,
                normalized_status=event.normalized_status,
                status_undocumented=event.status_undocumented,
                observed_at=event.observed_at.isoformat(),
                last_seen_at=event.last_seen_at.isoformat(),
                observation_count=event.observation_count,
            )
            for event in events
        ],
    )


class ManualResolutionPayload(BaseModel):
    """A person's decision about a booking the provider never confirmed.

    ``parcel_exists=False`` is the only path in the whole system that makes an
    order bookable again after an ambiguous create, so it requires a reason and
    is audited with who said so.
    """

    parcel_exists: bool
    reason: str = Field(min_length=3, max_length=400)
    provider_consignment_id: str | None = Field(default=None, max_length=120)
    tracking_code: str | None = Field(default=None, max_length=120)


@router.post(
    "/booking-attempts/{attempt_id}/resolve",
    summary="Record a person's decision about an unconfirmed booking",
)
async def resolve_booking_attempt(
    attempt_id: uuid.UUID,
    payload: ManualResolutionPayload,
    principal: CredentialManager,
    recovery: BookingRecoveryDep,
) -> dict[str, str]:
    result = await recovery.resolve_manually(
        attempt_id,
        parcel_exists=payload.parcel_exists,
        reason=payload.reason,
        provider_consignment_id=payload.provider_consignment_id,
        tracking_code=payload.tracking_code,
    )
    return {
        "attempt_id": str(result.attempt_id),
        "consignment_id": str(result.consignment_id),
        "outcome": str(result.outcome),
    }


# --------------------------------------------------------------- returns --


class ReturnRequestPayload(BaseModel):
    """Why the parcel should come back.

    ``reason`` is optional because the provider's documentation says it is.
    ecomsbd asks for it anyway in the UI — a return report is only worth acting
    on if its reasons are real — but an empty one never blocks the request.
    """

    reason: str | None = Field(default=None, max_length=400)


class ReturnRequestResponse(BaseModel):
    id: str
    consignment_id: str
    state: str
    provider_return_id: str | None
    provider_status: str | None
    #: Seller-facing. For an unconfirmed request this is the Bangla copy that
    #: tells them not to send another.
    message: str


@router.post(
    "/consignments/{consignment_id}/return",
    response_model=ReturnRequestResponse,
    summary="Ask the courier to return a parcel",
)
async def request_return(
    consignment_id: uuid.UUID,
    payload: ReturnRequestPayload,
    principal: Annotated[Principal, Depends(require_permission(Permission.ORDER_CANCEL))],
    returns: CourierReturnsDep,
) -> ReturnRequestResponse:
    """Request a return, once.

    A second request for the same parcel is refused with
    ``RETURN_ALREADY_REQUESTED`` — including while an earlier one is still
    unconfirmed, because a lost answer does not mean the courier did not get it,
    and a parcel collected twice is a charge the seller cannot undo.
    """
    result = await returns.request_return(consignment_id, reason=payload.reason)
    return ReturnRequestResponse(
        id=str(result.request_id),
        consignment_id=str(result.consignment_id),
        state=str(result.state),
        provider_return_id=result.provider_return_id,
        provider_status=result.provider_status,
        message=result.message,
    )


@router.get(
    "/consignments/{consignment_id}/returns",
    response_model=list[ReturnRequestResponse],
    summary="Return requests raised for a parcel",
)
async def list_returns(
    consignment_id: uuid.UUID,
    principal: Booker,
    returns: CourierReturnsDep,
) -> list[ReturnRequestResponse]:
    rows = await returns.for_consignment(consignment_id)
    return [
        ReturnRequestResponse(
            id=str(row.id),
            consignment_id=str(row.consignment_id),
            state=row.state,
            provider_return_id=row.provider_return_id,
            provider_status=row.provider_status,
            message=row.reason or "",
        )
        for row in rows
    ]


class ProviderPaymentResponse(BaseModel):
    """A provider payment as the Money screen shows it.

    ``schema_unverified`` is surfaced rather than hidden: Steadfast documents no
    response schema for its payments endpoints, so the field names behind these
    numbers are inferred. A seller comparing this against their courier portal
    deserves to know that.
    """

    id: str
    provider_payment_id: str
    provider_reference: str | None
    sync_state: str
    total_paisa: int | None
    paid_at: str | None
    consignment_count: int | None
    payout_id: str | None
    first_seen_at: str
    last_seen_at: str
    error_message: str | None
    observed_fields: list[str]
    schema_unverified: bool = True


@router.get(
    "/payments",
    response_model=list[ProviderPaymentResponse],
    summary="Payments synced from the courier",
)
async def list_provider_payments(
    principal: Annotated[Principal, Depends(require_permission(Permission.MONEY_VIEW))],
    payments: PaymentSyncDep,
    provider: str = "steadfast",
    limit: int = 50,
) -> list[ProviderPaymentResponse]:
    rows = await payments.payments(provider=provider, limit=min(limit, 200))
    return [
        ProviderPaymentResponse(
            id=str(row.id),
            provider_payment_id=row.provider_payment_id,
            provider_reference=row.provider_reference,
            sync_state=row.sync_state,
            total_paisa=row.total_paisa,
            paid_at=row.paid_at.isoformat() if row.paid_at else None,
            consignment_count=row.consignment_count,
            payout_id=str(row.payout_id) if row.payout_id else None,
            first_seen_at=row.first_seen_at.isoformat(),
            last_seen_at=row.last_seen_at.isoformat(),
            error_message=row.error_message,
            observed_fields=list(row.observed_fields or []),
        )
        for row in rows
    ]


@router.post(
    "/payments/sync",
    summary="Sync payments from the courier now",
)
async def sync_provider_payments(
    principal: Annotated[Principal, Depends(require_permission(Permission.MONEY_RECONCILE))],
    payments: PaymentSyncDep,
    provider: str = "steadfast",
) -> dict[str, object]:
    """Run the payment sync on demand.

    Idempotent: a payment already imported is recognised by its provider id and
    is not imported again, so pressing this twice cannot double a settled total.
    """
    report = await payments.sync(provider=provider)
    return report.as_dict()


__all__ = ["router"]
