"""FastAPI dependencies.

The important one is :func:`get_principal`. It verifies the access token,
confirms the session is still live in the database, and then **installs the
tenant into the ambient context**. Everything downstream — every ORM query in
the request — is filtered against that value by :mod:`app.db.tenancy`, so an
endpoint cannot read another tenant's data even if it forgets to filter.

All dependencies are ``async`` on purpose. FastAPI runs synchronous
dependencies in a thread pool with a *copied* context, so a ``ContextVar`` set
in a sync dependency would not reach the endpoint, and tenant scoping would
quietly fall back to "no tenant".
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, replace
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession
from app.auth.oidc import IdTokenVerifier, build_apple_verifier, build_google_verifier
from app.auth.otp_providers import OtpSender, build_otp_provider
from app.auth.passwords import PasswordHasher
from app.auth.service import AuthService
from app.auth.supabase import SupabaseVerifier, resolve_session, resolve_user
from app.billing.models import DistributionChannel
from app.billing.providers.registry import BillingProviderRegistry, build_registry, resolve_channel
from app.billing.service import BillingService
from app.common.cache import RateLimiter, get_cache
from app.common.feature_flags import FeatureFlagService
from app.consignments.service import ConsignmentService
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.context import ActorType, RequestContext, clear_context, current_context, set_context
from app.core.errors import AuthenticationError, ErrorCode, ForbiddenError
from app.core.security import CredentialVault, SecretHasher, TokenService
from app.couriers.accounts import CourierAccountService
from app.couriers.booking import CourierBookingService
from app.couriers.payments import PaymentSyncService
from app.couriers.recovery import BookingRecoveryService
from app.couriers.registry import (
    CourierAdapterRegistry,
    get_courier_registry,
    reset_courier_registry,
)
from app.couriers.returns import CourierReturnService
from app.couriers.status_sync import StatusSyncService
from app.db.session import session_scope
from app.entitlements.service import EntitlementService
from app.money.service import ReceivableService
from app.notifications.transport import EmailTransport, build_email_transport
from app.tenants.roles import Permission, TenantRole, has_permission
from app.users.models import User

__all__ = [
    "AppleVerifierDep",
    "BillingServiceDep",
    "BookingRecoveryDep",
    "CourierAccountsDep",
    "CourierBookingDep",
    "CourierRegistryDep",
    "CourierReturnsDep",
    "CurrentPrincipal",
    "DbSession",
    "DistributionChannelDep",
    "GoogleVerifierDep",
    "PaymentSyncDep",
    "Principal",
    "ProviderRegistryDep",
    "SettingsDep",
    "StatusSyncDep",
    "get_auth_service",
    "get_db",
    "get_principal",
    "get_tenant_principal",
    "require_permission",
]


# --------------------------------------------------------------------------- #
# Singletons
# --------------------------------------------------------------------------- #

_hasher: SecretHasher | None = None
_vault: CredentialVault | None = None
_tokens: TokenService | None = None
_otp_sender: OtpSender | None = None
_passwords: PasswordHasher | None = None
_email_transport: EmailTransport | None = None
_google_verifier: IdTokenVerifier | None = None
_apple_verifier: IdTokenVerifier | None = None
_supabase_verifier: SupabaseVerifier | None = None


def get_supabase_verifier(settings: Settings) -> SupabaseVerifier:
    global _supabase_verifier
    if _supabase_verifier is None:
        _supabase_verifier = SupabaseVerifier(settings)
    return _supabase_verifier


async def get_app_settings() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_hasher(settings: Settings | None = None) -> SecretHasher:
    global _hasher
    if _hasher is None:
        _hasher = SecretHasher(settings or get_settings())
    return _hasher


def get_vault(settings: Settings | None = None) -> CredentialVault:
    global _vault
    if _vault is None:
        _vault = CredentialVault(settings or get_settings())
    return _vault


def get_token_service(settings: Settings | None = None) -> TokenService:
    global _tokens
    if _tokens is None:
        _tokens = TokenService(settings or get_settings())
    return _tokens


def get_otp_sender(settings: Settings | None = None) -> OtpSender:
    global _otp_sender
    if _otp_sender is None:
        _otp_sender = build_otp_provider(settings or get_settings())
    return _otp_sender


def reset_singletons() -> None:
    """Drop cached service instances. Used when settings change in tests."""
    global _hasher, _vault, _tokens, _otp_sender, _registry, _supabase_verifier
    _supabase_verifier = None
    global _passwords, _email_transport, _google_verifier, _apple_verifier
    _hasher = _vault = _tokens = _otp_sender = None
    _registry = None
    # The OIDC verifiers hold a JWKS cache and, more importantly, the audience
    # list they were built from. A stale one would keep verifying against the
    # previous test's client ids.
    _passwords = _email_transport = _google_verifier = _apple_verifier = None
    # The courier registry holds live HTTP connection pools, so a stale one
    # between tests would keep a fake transport installed for the next test —
    # or, worse, a real one.
    reset_courier_registry()


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


async def get_db() -> AsyncIterator[AsyncSession]:
    """A tenant-scoped session for the request, committed on success."""
    async with session_scope() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller for this request."""

    user: User
    session: AuthSession
    tenant_id: uuid.UUID | None
    role: TenantRole | None

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def session_id(self) -> uuid.UUID:
        return self.session.id

    def require_tenant(self) -> uuid.UUID:
        if self.tenant_id is None:
            raise ForbiddenError(
                "This session is not attached to a shop yet",
                code=ErrorCode.FORBIDDEN,
            )
        return self.tenant_id

    def can(self, permission: Permission) -> bool:
        return self.role is not None and has_permission(self.role, permission)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def get_optional_principal(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
) -> AsyncIterator[Principal | None]:
    """Resolve the caller if a valid token is present; otherwise ``None``.

    Yields rather than returns so the ambient context can be restored after the
    response is produced.
    """
    token = _bearer_token(request)
    if token is None:
        yield None
        return

    user: User | None
    session_row: AuthSession | None
    if settings.supabase_auth_active:
        verifier = get_supabase_verifier(settings)
        claims = await verifier.verify(token)
        user = await resolve_user(db, verifier, token, claims)
        session_row = await resolve_session(db, claims, user)
        auth = await get_auth_service(db, settings, await get_rate_limiter())
        memberships = await auth.list_memberships(user.id)
        if session_row.tenant_id is None and len(memberships) == 1:
            session_row.tenant_id = memberships[0].id
        tenant_id = session_row.tenant_id
        membership = next((m for m in memberships if m.id == tenant_id), None)
        if tenant_id is not None and membership is None:
            raise ForbiddenError("Shop membership is no longer active")
        role = TenantRole(membership.role) if membership else None
    else:
        legacy_claims = get_token_service(settings).decode_access_token(token)

        # The token being valid is not enough: a revoked session must stop working
        # immediately, which means checking the database on every request. The
        # access token is short-lived precisely so this stays a cheap primary-key read.
        session_row = await db.get(AuthSession, legacy_claims.session_id)
        if session_row is None or not session_row.is_usable():
            raise AuthenticationError(
                "This session is no longer valid", code=ErrorCode.SESSION_REVOKED
            )
        if session_row.user_id != legacy_claims.user_id:
            raise AuthenticationError(
                "Token does not match its session", code=ErrorCode.INVALID_TOKEN
            )

        user = await db.get(User, legacy_claims.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("This account is not active", code=ErrorCode.SESSION_REVOKED)

        # The tenant comes from the session row, not the token: rebinding a session
        # to a different shop must take effect without waiting for token expiry.
        tenant_id = session_row.tenant_id

        # And so does the role, for exactly the same reason. Reading it from the
        # token claim instead left this path inconsistent with the sentence
        # above: a session bound to a shop *after* its token was minted carried
        # `role=None` and therefore no permissions at all, and rebinding to a
        # shop where the caller holds a different role kept the old one until
        # the token expired. The Supabase path already resolves role from
        # membership; this now matches it, so local development behaves the way
        # production does.
        role = None
        if tenant_id is not None:
            auth = await get_auth_service(db, settings, await get_rate_limiter())
            memberships = await auth.list_memberships(user.id)
            membership = next((m for m in memberships if m.id == tenant_id), None)
            if membership is None:
                raise ForbiddenError("Shop membership is no longer active")
            role = TenantRole(membership.role)

    principal = Principal(user=user, session=session_row, tenant_id=tenant_id, role=role)

    context_token = set_context(
        replace(
            current_context(),
            tenant_id=tenant_id,
            user_id=user.id,
            session_id=session_row.id,
            actor_type=ActorType.USER,
            role=str(role) if role else None,
            device_id=session_row.device_id,
        )
    )
    session_row.last_seen_at = utc_now()
    try:
        yield principal
    finally:
        clear_context(context_token)


async def get_principal(
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> Principal:
    """Require an authenticated caller."""
    if principal is None:
        raise AuthenticationError("Authentication is required")
    return principal


async def get_tenant_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Require an authenticated caller attached to a shop."""
    principal.require_tenant()
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
TenantPrincipal = Annotated[Principal, Depends(get_tenant_principal)]


def require_permission(
    permission: Permission,
) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Dependency factory enforcing a role permission server-side."""

    async def _dependency(
        principal: Annotated[Principal, Depends(get_tenant_principal)],
    ) -> Principal:
        if not principal.can(permission):
            raise ForbiddenError(f"Your role does not allow {permission}")
        return principal

    return _dependency


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #


async def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_cache())


def get_password_hasher() -> PasswordHasher:
    global _passwords
    if _passwords is None:
        _passwords = PasswordHasher()
    return _passwords


def get_email_transport(settings: Settings | None = None) -> EmailTransport:
    global _email_transport
    if _email_transport is None:
        _email_transport = build_email_transport(settings or get_settings())
    return _email_transport


def get_google_verifier(settings: Settings | None = None) -> IdTokenVerifier:
    """Google's identity-token verifier.

    A process-wide singleton so the JWKS cache is shared: a per-request
    verifier would fetch Google's key set on every sign-in, putting an outbound
    request on the login path and making Google's availability ours.
    """
    global _google_verifier
    if _google_verifier is None:
        _google_verifier = build_google_verifier(settings or get_settings())
    return _google_verifier


def get_apple_verifier(settings: Settings | None = None) -> IdTokenVerifier:
    global _apple_verifier
    if _apple_verifier is None:
        _apple_verifier = build_apple_verifier(settings or get_settings())
    return _apple_verifier


async def get_google_verifier_dep(settings: SettingsDep) -> IdTokenVerifier:
    """Injectable wrapper around the Google verifier singleton.

    A dependency rather than a direct call inside the route, so a test can
    substitute a verifier with a seeded key set. Reaching for the factory from
    the route body would make the audience list and the JWKS source
    unsubstitutable, which is exactly what the wrong-audience tests need to
    control.
    """
    return get_google_verifier(settings)


async def get_apple_verifier_dep(settings: SettingsDep) -> IdTokenVerifier:
    return get_apple_verifier(settings)


GoogleVerifierDep = Annotated[IdTokenVerifier, Depends(get_google_verifier_dep)]
AppleVerifierDep = Annotated[IdTokenVerifier, Depends(get_apple_verifier_dep)]


async def get_auth_service(
    db: DbSession,
    settings: SettingsDep,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> AuthService:
    return AuthService(
        db,
        settings=settings,
        hasher=get_hasher(settings),
        vault=get_vault(settings),
        tokens=get_token_service(settings),
        otp_sender=get_otp_sender(settings),
        rate_limiter=limiter,
        passwords=get_password_hasher(),
        email=get_email_transport(settings),
    )


async def get_entitlements(db: DbSession, settings: SettingsDep) -> EntitlementService:
    return EntitlementService(db, settings=settings)


_registry: BillingProviderRegistry | None = None


def get_provider_registry(settings: Settings | None = None) -> BillingProviderRegistry:
    """The billing provider registry.

    Built once per process. Both transport ports are ``None`` in this build:
    no Play service account and no bKash merchant contract exist, so each
    provider reports itself unavailable with its blocker code rather than
    failing at the point of use.
    """
    global _registry
    if _registry is None:
        _registry = build_registry(settings or get_settings())
    return _registry


async def get_billing_registry(settings: SettingsDep) -> BillingProviderRegistry:
    return get_provider_registry(settings)


async def get_distribution_channel(request: Request, settings: SettingsDep) -> DistributionChannel:
    """Which build is calling (master spec section 27.1).

    A client may narrow the channel — declaring itself a Play build — but never
    widen it. See :func:`app.billing.providers.registry.resolve_channel`.
    """
    return resolve_channel(request.headers.get("x-distribution-channel"), settings)


async def get_billing_service(
    db: DbSession,
    settings: SettingsDep,
    registry: Annotated[BillingProviderRegistry, Depends(get_billing_registry)],
) -> BillingService:
    return BillingService(db, settings=settings, registry=registry)


async def get_feature_flags(db: DbSession) -> FeatureFlagService:
    return FeatureFlagService(db)


def get_couriers(settings: Settings | None = None) -> CourierAdapterRegistry:
    """The courier adapter registry.

    Built once per process so the HTTP connection pool is shared: a new client
    per booking would open a new TCP connection per parcel.
    """
    return get_courier_registry(settings or get_settings())


async def get_courier_registry_dep(settings: SettingsDep) -> CourierAdapterRegistry:
    return get_couriers(settings)


async def get_courier_accounts(
    db: DbSession,
    settings: SettingsDep,
    registry: Annotated[CourierAdapterRegistry, Depends(get_courier_registry_dep)],
) -> CourierAccountService:
    return CourierAccountService(db, vault=get_vault(settings), registry=registry)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
EntitlementsDep = Annotated[EntitlementService, Depends(get_entitlements)]
FeatureFlagsDep = Annotated[FeatureFlagService, Depends(get_feature_flags)]
BillingServiceDep = Annotated[BillingService, Depends(get_billing_service)]


async def get_courier_booking(
    db: DbSession,
    settings: SettingsDep,
    accounts: Annotated[CourierAccountService, Depends(get_courier_accounts)],
) -> CourierBookingService:
    return CourierBookingService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=get_vault(settings),
        settings=settings,
    )


async def get_booking_recovery(
    db: DbSession,
    settings: SettingsDep,
    accounts: Annotated[CourierAccountService, Depends(get_courier_accounts)],
) -> BookingRecoveryService:
    return BookingRecoveryService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        settings=settings,
    )


async def get_courier_returns(
    db: DbSession,
    accounts: Annotated[CourierAccountService, Depends(get_courier_accounts)],
) -> CourierReturnService:
    return CourierReturnService(db, accounts=accounts)


async def get_payment_sync(
    db: DbSession,
    settings: SettingsDep,
    accounts: Annotated[CourierAccountService, Depends(get_courier_accounts)],
) -> PaymentSyncService:
    return PaymentSyncService(db, accounts=accounts, settings=settings)


async def get_status_sync(
    db: DbSession,
    settings: SettingsDep,
    accounts: Annotated[CourierAccountService, Depends(get_courier_accounts)],
) -> StatusSyncService:
    return StatusSyncService(
        db,
        accounts=accounts,
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        settings=settings,
    )


CourierAccountsDep = Annotated[CourierAccountService, Depends(get_courier_accounts)]
CourierReturnsDep = Annotated[CourierReturnService, Depends(get_courier_returns)]
PaymentSyncDep = Annotated[PaymentSyncService, Depends(get_payment_sync)]
StatusSyncDep = Annotated[StatusSyncService, Depends(get_status_sync)]
CourierBookingDep = Annotated[CourierBookingService, Depends(get_courier_booking)]
BookingRecoveryDep = Annotated[BookingRecoveryService, Depends(get_booking_recovery)]
CourierRegistryDep = Annotated[CourierAdapterRegistry, Depends(get_courier_registry_dep)]
ProviderRegistryDep = Annotated[BillingProviderRegistry, Depends(get_billing_registry)]
DistributionChannelDep = Annotated[DistributionChannel, Depends(get_distribution_channel)]


def client_ip(request: Request) -> str | None:
    context: RequestContext | None = request.scope.get("ecomsbd_context")
    if context is not None and context.client_ip:
        return context.client_ip
    return request.client.host if request.client else None
