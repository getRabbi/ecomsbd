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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps import CourierAccountsDep, Principal, require_permission
from app.core.errors import NotFoundError
from app.couriers.accounts import ConnectRequest
from app.couriers.capabilities import load_manifest
from app.tenants.roles import Permission

router = APIRouter(prefix="/couriers", tags=["couriers"])

CredentialManager = Annotated[
    Principal, Depends(require_permission(Permission.COURIER_CREDENTIAL_MANAGE))
]


class CourierConnectPayload(BaseModel):
    """The one request body in this file that carries secrets.

    Both values are constrained but not pattern-matched: the supplied
    documentation states no key format, and rejecting a valid key because we
    guessed its shape would be worse than passing it to the provider and
    letting the provider decide.
    """

    api_key: str = Field(min_length=1, max_length=300)
    secret_key: str = Field(min_length=1, max_length=300)
    label: str | None = Field(default=None, max_length=80)


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
    account, outcome = await accounts.connect(
        ConnectRequest(
            provider=provider,
            api_key=payload.api_key,
            secret_key=payload.secret_key,
            label=payload.label,
        )
    )
    return ConnectionTestResponse(
        result=str(outcome.result),
        message=outcome.message,
        account=_response(account.public_view()),
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


__all__ = ["router"]
