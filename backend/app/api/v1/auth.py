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

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import (
    AppleVerifierDep,
    AuthServiceDep,
    CurrentPrincipal,
    DbSession,
    GoogleVerifierDep,
    SettingsDep,
    client_ip,
)
from app.api.v1.schemas import (
    AcknowledgedResponse,
    AuthTokenPayload,
    DevicePayload,
    EmailPayload,
    LoginPayload,
    LogoutPayload,
    OtpRequestPayload,
    OtpRequestResponse,
    OtpVerifyPayload,
    PasswordChangePayload,
    PasswordResetPayload,
    ProviderSignInPayload,
    RefreshPayload,
    RegisterPayload,
    RegisterResponse,
    SelectTenantPayload,
    SessionResponse,
    ShopSessionResponse,
    TenantSummaryResponse,
)
from app.auth.service import DeviceInfo, SignInResult
from app.common.phone import normalize_bd_phone
from app.core.config import Settings
from app.core.errors import AuthenticationError, ErrorCode


async def legacy_auth_guard(request: Request, settings: SettingsDep) -> None:
    if settings.supabase_auth_active and request.url.path.rstrip("/").split("/")[-1] not in (
        "logout",
        "select-tenant",
        "device",
    ):
        raise AuthenticationError(
            "Use Supabase Auth", code=ErrorCode.FEATURE_DISABLED, http_status=410
        )


router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(legacy_auth_guard)])


@router.post("/device", status_code=204, summary="Attach this installation to the verified session")
async def attach_device(
    payload: DevicePayload, request: Request, principal: CurrentPrincipal,
    auth: AuthServiceDep, db: DbSession,
) -> None:
    import sqlalchemy as sa

    from app.users.models import User

    # Serialize device upserts for one user; an installation ID has no identity authority.
    await db.execute(sa.select(User.id).where(User.id == principal.user_id).with_for_update())
    device = await auth._upsert_device(user_id=principal.user_id, device=_device(payload, request))
    principal.session.device_id = device.id if device else None


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


def _device(payload: DevicePayload, request: Request) -> DeviceInfo:
    """One place that turns a request into a device description.

    Every sign-in route uses it, so the device and session records a seller
    sees are identical whichever way they signed in.
    """
    return DeviceInfo(
        install_id=payload.install_id,
        platform=payload.platform,
        app_version=payload.app_version,
        os_version=payload.os_version,
        model=payload.model,
        push_token=payload.push_token,
        user_agent=request.headers.get("user-agent"),
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
    response_model=ShopSessionResponse,
    summary="Point this session at one of the caller's shops",
)
async def select_tenant(
    payload: SelectTenantPayload,
    principal: CurrentPrincipal,
    auth: AuthServiceDep,
) -> SessionResponse:
    """Bind the session to a shop using server-verified membership.

    Membership is verified server-side; the client's claim about which shop it
    may use is never trusted.
    """
    result = await auth.bind_session_tenant(
        session_id=principal.session_id, tenant_id=payload.tenant_id
    )
    return _to_session_response(result)


# --------------------------------------------------------------------------- #
# Email + password
# --------------------------------------------------------------------------- #


def _debug_token(settings: Settings, token: str | None) -> str | None:
    """Whether a link token may be returned in the response body.

    Only outside staging and production, and only when no transport could
    actually deliver it. It exists so the flow is testable and demoable before
    an email provider is signed up for; the moment one is configured, or the
    moment this runs anywhere deployed, it returns nothing.
    """
    if token is None or settings.app_env.is_deployed:
        return None
    if settings.email_transport_can_deliver:
        return None
    return token


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account with an email and password",
)
async def register(
    payload: RegisterPayload,
    request: Request,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> RegisterResponse:
    """Register, then sign in.

    The new session is issued immediately rather than withheld until the email
    is verified: a seller who has just typed their details should land in shop
    setup, not in an inbox. Verification gates account *linking*, which is the
    thing that actually needs proof of the address.
    """
    result = await auth.register_with_password(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        device=_device(payload.device, request),
        client_ip=client_ip(request),
    )
    return RegisterResponse(
        session=_to_session_response(result.session),
        email_verification_sent=result.email_verification_sent,
        verification_token=_debug_token(settings, result.verification_token),
    )


@router.post("/login", response_model=SessionResponse, summary="Sign in with email and password")
async def login(
    payload: LoginPayload,
    request: Request,
    auth: AuthServiceDep,
) -> SessionResponse:
    """One refusal for a wrong password and for an address with no account.

    They are also the same *speed*: the service derives a password hash either
    way, so response timing does not answer the question the message declines
    to answer.
    """
    result = await auth.login_with_password(
        email=payload.email,
        password=payload.password,
        device=_device(payload.device, request),
        client_ip=client_ip(request),
    )
    return _to_session_response(result)


@router.post(
    "/email/verify",
    response_model=AcknowledgedResponse,
    summary="Confirm an email address from its link",
)
async def verify_email(
    payload: AuthTokenPayload,
    request: Request,
    auth: AuthServiceDep,
) -> AcknowledgedResponse:
    await auth.verify_email(token=payload.token, client_ip=client_ip(request))
    return AcknowledgedResponse()


@router.post(
    "/email/resend",
    response_model=AcknowledgedResponse,
    summary="Send the verification link again",
)
async def resend_verification(
    payload: EmailPayload,
    request: Request,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> AcknowledgedResponse:
    """Always acknowledges, whether or not the address has an account."""
    token = await auth.resend_email_verification(email=payload.email, client_ip=client_ip(request))
    return AcknowledgedResponse(debug_token=_debug_token(settings, token))


@router.post(
    "/password/forgot",
    response_model=AcknowledgedResponse,
    summary="Start a password reset",
)
async def forgot_password(
    payload: EmailPayload,
    request: Request,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> AcknowledgedResponse:
    """Acknowledges every address identically.

    Reporting "no such account" here would make this endpoint a way to test a
    leaked address list against ecomsbd's sellers, one request at a time.
    """
    token = await auth.request_password_reset(email=payload.email, client_ip=client_ip(request))
    return AcknowledgedResponse(debug_token=_debug_token(settings, token))


@router.post(
    "/password/reset",
    response_model=AcknowledgedResponse,
    summary="Set a new password from a reset link",
)
async def reset_password(
    payload: PasswordResetPayload,
    request: Request,
    auth: AuthServiceDep,
) -> AcknowledgedResponse:
    """Single-use token; every existing session is revoked on success."""
    await auth.reset_password(
        token=payload.token,
        new_password=payload.new_password,
        client_ip=client_ip(request),
    )
    return AcknowledgedResponse()


@router.post(
    "/password/change",
    response_model=AcknowledgedResponse,
    summary="Change the password of the signed-in account",
)
async def change_password(
    payload: PasswordChangePayload,
    principal: CurrentPrincipal,
    auth: AuthServiceDep,
) -> AcknowledgedResponse:
    """Requires the current password even though the caller is signed in.

    A borrowed unlocked phone should not be enough to lock its owner out of
    their own shop.
    """
    await auth.change_password(
        user_id=principal.user_id,
        session_id=principal.session_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return AcknowledgedResponse()


# --------------------------------------------------------------------------- #
# Google and Apple
# --------------------------------------------------------------------------- #


@router.post(
    "/oauth/google",
    response_model=SessionResponse,
    summary="Sign in with a Google identity token",
)
async def google_sign_in(
    payload: ProviderSignInPayload,
    request: Request,
    auth: AuthServiceDep,
    verifier: GoogleVerifierDep,
) -> SessionResponse:
    """The token is verified here; the client's claim about it is not read.

    Signature against Google's published keys, issuer, expiry, and an ``aud``
    matching one of this deployment's configured client ids. A token minted for
    a different application fails the audience check, which is the one that
    matters most and the one most often left out.
    """
    result = await auth.sign_in_with_provider(
        verifier=verifier,
        id_token=payload.id_token,
        device=_device(payload.device, request),
        client_ip=client_ip(request),
    )
    return _to_session_response(result)


@router.post(
    "/oauth/apple",
    response_model=SessionResponse,
    summary="Sign in with an Apple identity token",
)
async def apple_sign_in(
    payload: ProviderSignInPayload,
    request: Request,
    auth: AuthServiceDep,
    verifier: AppleVerifierDep,
) -> SessionResponse:
    """Same server-side verification as Google, against Apple's JWKS.

    Apple sends a name and email only on the *first* authorization, so neither
    is required: ``sub`` is the identity, and a returning seller whose token
    carries nothing else signs in normally.
    """
    result = await auth.sign_in_with_provider(
        verifier=verifier,
        id_token=payload.id_token,
        device=_device(payload.device, request),
        client_ip=client_ip(request),
    )
    return _to_session_response(result)


__all__ = ["router"]
