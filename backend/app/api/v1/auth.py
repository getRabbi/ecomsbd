"""Authentication endpoints.

Master spec section 39::

    POST /v1/auth/otp/request
    POST /v1/auth/otp/verify
    POST /v1/auth/refresh
    POST /v1/auth/logout

Plus ``POST /v1/auth/select-tenant`` for the case where one phone number owns
more than one shop — the master spec's data model allows it, and picking
silently would let a seller book against the wrong shop.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import AuthServiceDep, CurrentPrincipal, client_ip
from app.api.v1.schemas import (
    LogoutPayload,
    OtpRequestPayload,
    OtpRequestResponse,
    OtpVerifyPayload,
    RefreshPayload,
    SelectTenantPayload,
    SessionResponse,
    TenantSummaryResponse,
)
from app.auth.service import DeviceInfo, SignInResult
from app.common.phone import normalize_bd_phone

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_session_response(result: SignInResult) -> SessionResponse:
    return SessionResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in_seconds=result.expires_in_seconds,
        session_id=result.session_id,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        is_new_user=result.is_new_user,
        needs_onboarding=result.needs_onboarding,
        tenants=[
            TenantSummaryResponse(
                id=t.id, name=t.name, role=t.role, onboarding_complete=t.onboarding_complete
            )
            for t in result.tenants
        ],
    )


@router.post(
    "/otp/request",
    response_model=OtpRequestResponse,
    summary="Request a one-time code",
)
async def request_otp(
    payload: OtpRequestPayload,
    request: Request,
    auth: AuthServiceDep,
) -> OtpRequestResponse:
    """Send a verification code to a Bangladeshi mobile number.

    The response is the same shape whether or not the number already has an
    account, so this endpoint cannot be used to enumerate registered sellers.
    """
    phone = normalize_bd_phone(payload.phone)
    result = await auth.request_otp(
        phone_e164=phone.e164,
        masked_phone=phone.masked,
        phone_last4=phone.last4,
        client_ip=client_ip(request),
    )
    return OtpRequestResponse(
        challenge_id=result.challenge_id,
        masked_phone=result.masked_phone,
        expires_in_seconds=result.expires_in_seconds,
        resend_available_in_seconds=result.resend_available_in_seconds,
        debug_code=result.debug_code,
    )


@router.post(
    "/otp/verify",
    response_model=SessionResponse,
    summary="Verify a one-time code and start a session",
)
async def verify_otp(
    payload: OtpVerifyPayload,
    request: Request,
    auth: AuthServiceDep,
) -> SessionResponse:
    result = await auth.verify_otp(
        challenge_id=payload.challenge_id,
        code=payload.code,
        device=DeviceInfo(
            install_id=payload.device.install_id,
            platform=payload.device.platform,
            app_version=payload.device.app_version,
            os_version=payload.device.os_version,
            model=payload.device.model,
            push_token=payload.device.push_token,
            user_agent=request.headers.get("user-agent"),
        ),
        client_ip=client_ip(request),
    )
    return _to_session_response(result)


@router.post("/refresh", response_model=SessionResponse, summary="Rotate the session tokens")
async def refresh(
    payload: RefreshPayload,
    request: Request,
    auth: AuthServiceDep,
) -> SessionResponse:
    """Exchange a refresh token for a new pair.

    The presented token is single-use. Presenting one twice revokes the whole
    session: the only realistic causes are a captured token or a badly broken
    client, and neither should keep receiving credentials.
    """
    result = await auth.refresh(refresh_token=payload.refresh_token, client_ip=client_ip(request))
    return _to_session_response(result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke this session, or every session",
)
async def logout(
    payload: LogoutPayload,
    principal: CurrentPrincipal,
    auth: AuthServiceDep,
) -> None:
    await auth.logout(session_id=principal.session_id, all_devices=payload.all_devices)


@router.post(
    "/select-tenant",
    response_model=SessionResponse,
    summary="Point this session at one of the caller's shops",
)
async def select_tenant(
    payload: SelectTenantPayload,
    principal: CurrentPrincipal,
    auth: AuthServiceDep,
) -> SessionResponse:
    """Bind the session to a shop and re-issue tokens.

    Membership is verified server-side; the client's claim about which shop it
    may use is never trusted.
    """
    result = await auth.bind_session_tenant(
        session_id=principal.session_id, tenant_id=payload.tenant_id
    )
    return _to_session_response(result)


__all__ = ["router"]
