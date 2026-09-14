"""Authentication service.

Implements the OTP flow from master spec sections 4, 47 and 89.

Notable decisions, each with a reason:

*   **A failed OTP attempt still counts.** The attempt counter increments before
    the code is compared, so an attacker cannot get free guesses by aborting.
*   **Refresh tokens rotate, and reuse is treated as theft.** Presenting a token
    that has already been exchanged revokes the entire session rather than just
    failing, because the only ways that happens are a captured token or a
    seriously broken client.
*   **The tenant lookup during login is the one deliberate cross-tenant read**,
    and it goes through :func:`~app.db.tenancy.allow_cross_tenant` so it appears
    in the logs as an explicit exemption rather than a silent hole.
*   **Enumeration is not prevented at OTP request.** Requesting a code always
    reports success shape; whether a user exists is only revealed after a
    correct code, at which point the caller controls the phone anyway.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import NoReturn

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identities import (
    MAX_EMAIL_LENGTH,
    AuthIdentity,
    AuthProvider,
    AuthToken,
    AuthTokenPurpose,
    normalize_email,
)
from app.auth.models import (
    AuthSession,
    Device,
    DevicePlatform,
    OtpChallenge,
    OtpPurpose,
    RefreshToken,
    RevocationReason,
)
from app.auth.oidc import IdTokenVerifier, OidcIdentity
from app.auth.otp_providers import OtpSender
from app.auth.passwords import PasswordHasher, validate_password
from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.outbox import OutboxTopic, enqueue
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.context import ActorType
from app.core.errors import (
    AppError,
    AuthenticationError,
    ErrorCode,
    RateLimitedError,
    ValidationError,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import (
    CredentialVault,
    SecretHasher,
    TokenService,
    generate_numeric_code,
    generate_opaque_token,
)
from app.db.tenancy import allow_cross_tenant
from app.notifications.transport import EmailMessage, EmailTransport, build_email_transport
from app.tenants.models import Tenant, TenantStatus, TenantUser
from app.users.models import User, UserStatus

__all__ = [
    "AuthService",
    "ChallengeResult",
    "DeviceInfo",
    "RegistrationResult",
    "SignInResult",
]

log = get_logger(__name__)

#: Deliberately permissive. The authority on whether an address exists is
#: whether mail to it arrives, which is what the verification link tests; a
#: stricter pattern here only rejects valid unusual addresses.
_EMAIL_RE = re.compile(r"^[^@\s,;]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,}$")


@lru_cache(maxsize=1)
def _absent_password_hash() -> str:
    """A digest to verify against when no identity was found.

    Its plaintext is random and thrown away, so nothing can ever match it. The
    point is the *work*: without this, a login for an address with no account
    would return before any Argon2id derivation and be measurably faster than a
    wrong password, which is an enumeration oracle no wording in the response
    can close. Computed once, on first use rather than at import, so the cost
    lands on the first login instead of on every process start.
    """
    return PasswordHasher().hash(generate_opaque_token())


#: Context string binding a user's phone ciphertext to its row.
#: AEAD context for a user's phone. Public so the team-invite path, which
#: also creates users, binds the ciphertext the same way.
USER_PHONE_CONTEXT = "user.phone"
_PHONE_CONTEXT = USER_PHONE_CONTEXT
_OTP_PHONE_CONTEXT = "otp.phone"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Client-supplied device description. All fields are advisory."""

    install_id: str | None = None
    platform: DevicePlatform = DevicePlatform.UNKNOWN
    app_version: str | None = None
    os_version: str | None = None
    model: str | None = None
    push_token: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class ChallengeResult:
    """Outcome of requesting an OTP."""

    challenge_id: uuid.UUID
    masked_phone: str
    expires_in_seconds: int
    resend_available_in_seconds: int
    #: Development environments only; ``None`` everywhere else.
    debug_code: str | None = None


@dataclass(frozen=True, slots=True)
class TenantSummary:
    id: uuid.UUID
    name: str
    role: str
    onboarding_complete: bool


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """A new account, plus the verification token for the caller to deliver.

    The token is carried out of the service rather than returned to the client:
    the API layer exposes it only where the transport could not send it and the
    environment is one where saying so out loud is safe.
    """

    session: SignInResult
    verification_token: str | None
    email_verification_sent: bool


@dataclass(frozen=True, slots=True)
class SignInResult:
    """Tokens plus everything the client needs to decide its next screen."""

    access_token: str
    refresh_token: str
    expires_in_seconds: int
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str | None
    is_new_user: bool
    tenants: list[TenantSummary]

    @property
    def needs_onboarding(self) -> bool:
        """True when the client should show shop setup rather than the dashboard."""
        if self.tenant_id is None:
            return True
        return not any(t.id == self.tenant_id and t.onboarding_complete for t in self.tenants)


class AuthService:
    """OTP challenges, sessions and token rotation."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        hasher: SecretHasher,
        vault: CredentialVault,
        tokens: TokenService,
        otp_sender: OtpSender,
        rate_limiter: RateLimiter,
        passwords: PasswordHasher | None = None,
        email: EmailTransport | None = None,
    ) -> None:
        self._db = session
        self._settings = settings
        self._hasher = hasher
        self._vault = vault
        self._tokens = tokens
        self._otp = otp_sender
        self._limits = rate_limiter
        self._passwords = passwords or PasswordHasher()
        # Defaulted rather than required, so every existing construction site -
        # the OTP tests among them - keeps working untouched. The disabled
        # transport records the attempt and sends nothing, which is the correct
        # behaviour for a deployment with no email provider yet.
        self._email = email or build_email_transport(settings)

    async def _persist_then_raise(self, error: AppError) -> NoReturn:
        """Commit security bookkeeping, then raise.

        Failed OTP attempts and reuse-triggered revocations are recorded *and*
        the request fails. Without an explicit commit the enclosing session
        rolls back on the raised error and discards exactly the state that makes
        the limit real: attempt counters would never increment, and a revoked
        session would come back to life.
        """
        await self._db.commit()
        raise error

    # ------------------------------------------------------------- request --

    def _require_phone_otp_login(self) -> None:
        """Refuse the OTP flow when this deployment has it switched off.

        The gate is in the service rather than on the two routes, so a future
        endpoint that issues or verifies a challenge cannot reintroduce the
        path by forgetting a dependency. ``PHONE_OTP_LOGIN_ENABLED`` is boot
        configuration: the production decision is email/password plus Google
        and Apple, and OTP is deferred until an SMS gateway is selected.
        """
        if not self._settings.phone_otp_login_enabled:
            raise AppError(
                "Sign-in by SMS code is not available.",
                code=ErrorCode.FEATURE_DISABLED,
                message_bn="এসএমএস কোড দিয়ে লগইন এখন বন্ধ আছে।",
            )

    async def request_otp(
        self,
        *,
        phone_e164: str,
        masked_phone: str,
        phone_last4: str,
        client_ip: str | None,
        purpose: OtpPurpose = OtpPurpose.LOGIN,
    ) -> ChallengeResult:
        """Issue an OTP challenge, subject to per-phone and per-IP limits."""
        self._require_phone_otp_login()
        settings = self._settings
        phone_hmac = self._hasher.phone_search_hash(phone_e164)
        ip_hash = self._hasher.ip_hash(client_ip)

        await self._enforce_request_limits(phone_hmac=phone_hmac, ip_hash=ip_hash)
        await self._enforce_resend_cooldown(phone_hmac)

        code = generate_numeric_code(settings.otp_length)
        challenge = OtpChallenge(
            phone_search_hmac=phone_hmac,
            phone_enc=self._vault.encrypt(phone_e164, context=_OTP_PHONE_CONTEXT),
            phone_last4=phone_last4,
            code_hash="",  # replaced below once the id is assigned
            purpose=purpose,
            max_attempts=settings.otp_max_attempts,
            expires_at=utc_now() + timedelta(seconds=settings.otp_ttl_seconds),
            delivery_provider=self._otp.name,
            request_ip_hash=ip_hash,
        )
        self._db.add(challenge)
        await self._db.flush()

        # Bound to the challenge id, so a code intercepted for one challenge
        # cannot be replayed against another.
        challenge.code_hash = self._hasher.otp_hash(code, challenge_id=challenge.id)

        delivery = await self._otp.send(phone_e164=phone_e164, code=code, masked_phone=masked_phone)
        challenge.delivery_status = delivery.status
        challenge.delivery_reference = delivery.reference
        challenge.delivery_error = delivery.error

        await record_audit(
            self._db,
            AuditAction.OTP_REQUESTED,
            entity_type="otp_challenge",
            entity_id=challenge.id,
            context={"purpose": str(purpose), "provider": self._otp.name},
            actor_type=ActorType.ANONYMOUS,
            client_ip_hash=ip_hash,
        )
        await self._db.flush()

        if not delivery.delivered:
            raise AppError(
                delivery.error or "Could not deliver the verification code",
                code=ErrorCode.OTP_DELIVERY_FAILED,
            )

        return ChallengeResult(
            challenge_id=challenge.id,
            masked_phone=masked_phone,
            expires_in_seconds=settings.otp_ttl_seconds,
            resend_available_in_seconds=settings.otp_resend_cooldown_seconds,
            debug_code=delivery.debug_code,
        )

    async def _enforce_request_limits(self, *, phone_hmac: str, ip_hash: str | None) -> None:
        settings = self._settings
        phone_result = await self._limits.hit(
            "otp:phone",
            phone_hmac,
            limit=settings.otp_max_requests_per_phone_hour,
            window_seconds=3600,
        )
        if phone_result.exceeded:
            raise RateLimitedError(
                "Too many verification codes requested for this number",
                code=ErrorCode.OTP_RATE_LIMITED,
                retry_after_seconds=phone_result.retry_after_seconds,
            )

        if ip_hash is not None:
            ip_result = await self._limits.hit(
                "otp:ip",
                ip_hash,
                limit=settings.otp_max_requests_per_ip_hour,
                window_seconds=3600,
            )
            if ip_result.exceeded:
                raise RateLimitedError(
                    "Too many verification codes requested from this network",
                    code=ErrorCode.OTP_RATE_LIMITED,
                    retry_after_seconds=ip_result.retry_after_seconds,
                )

    async def _enforce_resend_cooldown(self, phone_hmac: str) -> None:
        cooldown = self._settings.otp_resend_cooldown_seconds
        if cooldown <= 0:
            return
        cutoff = utc_now() - timedelta(seconds=cooldown)
        recent = (
            await self._db.execute(
                sa.select(OtpChallenge.created_at)
                .where(
                    OtpChallenge.phone_search_hmac == phone_hmac,
                    OtpChallenge.created_at > cutoff,
                )
                .order_by(OtpChallenge.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent is not None:
            elapsed = (utc_now() - recent).total_seconds()
            raise RateLimitedError(
                "A code was sent recently",
                code=ErrorCode.OTP_RESEND_TOO_SOON,
                retry_after_seconds=max(1, int(cooldown - elapsed)),
            )

    # -------------------------------------------------------------- verify --

    async def verify_otp(
        self,
        *,
        challenge_id: uuid.UUID,
        code: str,
        device: DeviceInfo,
        client_ip: str | None,
    ) -> SignInResult:
        """Verify a code and start a session."""
        self._require_phone_otp_login()
        ip_hash = self._hasher.ip_hash(client_ip)
        challenge = await self._db.get(OtpChallenge, challenge_id)
        if challenge is None:
            raise AppError("Verification code is not valid", code=ErrorCode.OTP_INVALID)

        if challenge.is_consumed:
            raise AppError("This code was already used", code=ErrorCode.OTP_INVALID)
        if challenge.is_expired():
            raise AppError("This code has expired", code=ErrorCode.OTP_EXPIRED)
        if challenge.is_locked:
            raise AppError(
                "Too many incorrect attempts for this code", code=ErrorCode.OTP_MAX_ATTEMPTS
            )

        # Count the attempt before comparing, so aborting mid-request costs one.
        challenge.attempts += 1
        if challenge.attempts >= challenge.max_attempts:
            challenge.locked_at = utc_now()
        await self._db.flush()

        if not self._hasher.verify_otp(
            code, challenge_id=challenge.id, expected_hash=challenge.code_hash
        ):
            await record_audit(
                self._db,
                AuditAction.OTP_FAILED,
                entity_type="otp_challenge",
                entity_id=challenge.id,
                context={"attempts": challenge.attempts},
                actor_type=ActorType.ANONYMOUS,
                client_ip_hash=ip_hash,
            )
            if challenge.is_locked:
                await self._persist_then_raise(
                    AppError(
                        "Too many incorrect attempts. Request a new code.",
                        code=ErrorCode.OTP_MAX_ATTEMPTS,
                    )
                )
            await self._persist_then_raise(
                AppError(
                    "The verification code is incorrect",
                    code=ErrorCode.OTP_INVALID,
                    details={"attempts_remaining": challenge.attempts_remaining},
                )
            )

        challenge.consumed_at = utc_now()

        phone_e164 = self._vault.decrypt(challenge.phone_enc, context=_OTP_PHONE_CONTEXT)
        user, is_new_user = await self._get_or_create_user(
            phone_e164=phone_e164,
            phone_hmac=challenge.phone_search_hmac,
            phone_last4=challenge.phone_last4,
        )

        if not user.is_active:
            raise AuthenticationError(
                "This account is not active", code=ErrorCode.FORBIDDEN, http_status=403
            )

        memberships = await self._load_memberships(user.id)
        # A single shop is selected automatically; several require an explicit
        # choice so the seller never books against the wrong shop by accident.
        active_tenant = memberships[0] if len(memberships) == 1 else None

        device_row = await self._upsert_device(user_id=user.id, device=device)
        session_row = await self._create_session(
            user=user,
            tenant_id=active_tenant.id if active_tenant else None,
            device=device_row,
            device_info=device,
            ip_hash=ip_hash,
        )
        access_token, refresh_token = await self._issue_tokens(
            session_row, role=active_tenant.role if active_tenant else None
        )

        user.last_login_at = utc_now()

        await record_audit(
            self._db,
            AuditAction.OTP_VERIFIED,
            entity_type="user",
            entity_id=user.id,
            context={"is_new_user": is_new_user, "tenant_count": len(memberships)},
            actor_type=ActorType.USER,
            actor_id=user.id,
            tenant_id=session_row.tenant_id,
            client_ip_hash=ip_hash,
        )
        await enqueue(
            self._db,
            OutboxTopic.USER_SIGNED_IN,
            {"user_id": str(user.id), "is_new_user": is_new_user},
            tenant_id=session_row.tenant_id,
        )
        await self._db.flush()

        return SignInResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_seconds=self._tokens.access_token_ttl_seconds,
            session_id=session_row.id,
            user_id=user.id,
            tenant_id=session_row.tenant_id,
            role=active_tenant.role if active_tenant else None,
            is_new_user=is_new_user,
            tenants=memberships,
        )

    # --------------------------------------------------------------- users --

    async def _get_or_create_user(
        self, *, phone_e164: str, phone_hmac: str, phone_last4: str
    ) -> tuple[User, bool]:
        existing = (
            await self._db.execute(sa.select(User).where(User.phone_search_hmac == phone_hmac))
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

        user = User(
            phone_search_hmac=phone_hmac,
            phone_enc=self._vault.encrypt(phone_e164, context=_PHONE_CONTEXT),
            phone_last4=phone_last4,
            status=UserStatus.ACTIVE,
        )
        self._db.add(user)
        await self._db.flush()
        return user, True

    async def list_memberships(self, user_id: uuid.UUID) -> list[TenantSummary]:
        """Shops this user belongs to. Public entry point for the API layer."""
        return await self._load_memberships(user_id)

    async def _load_memberships(self, user_id: uuid.UUID) -> list[TenantSummary]:
        """Which shops this user belongs to.

        The one query in the system that must read across tenants: at login
        there is no tenant in scope yet, and this is how one is chosen. Scoped
        by ``user_id`` and wrapped in an explicit, logged bypass.
        """
        with allow_cross_tenant("login: resolve tenant memberships for authenticated user"):
            rows = (
                await self._db.execute(
                    sa.select(TenantUser, Tenant)
                    .join(Tenant, Tenant.id == TenantUser.tenant_id)
                    .where(
                        TenantUser.user_id == user_id,
                        TenantUser.is_active.is_(True),
                        Tenant.status == TenantStatus.ACTIVE,
                        Tenant.deleted_at.is_(None),
                    )
                    .order_by(Tenant.created_at)
                )
            ).all()

        return [
            TenantSummary(
                id=tenant.id,
                name=tenant.name,
                role=membership.role,
                onboarding_complete=tenant.onboarding_complete,
            )
            for membership, tenant in rows
        ]

    # ------------------------------------------------------------ sessions --

    async def _upsert_device(self, *, user_id: uuid.UUID, device: DeviceInfo) -> Device | None:
        if not device.install_id:
            return None
        row = (
            await self._db.execute(
                sa.select(Device).where(
                    Device.user_id == user_id, Device.install_id == device.install_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = Device(user_id=user_id, install_id=device.install_id)
            self._db.add(row)

        row.platform = device.platform
        row.app_version = device.app_version
        row.os_version = device.os_version
        row.model = device.model
        row.last_seen_at = utc_now()
        row.revoked_at = None
        if device.push_token and device.push_token != row.push_token:
            # An installation token must not continue notifying a previous seller
            # after the same phone signs into a different account.
            await self._db.execute(
                sa.update(Device)
                .where(Device.push_token == device.push_token, Device.id != row.id)
                .values(push_token=None, push_token_updated_at=utc_now())
            )
            row.push_token = device.push_token
            row.push_token_updated_at = utc_now()
        await self._db.flush()
        return row

    async def _create_session(
        self,
        *,
        user: User,
        tenant_id: uuid.UUID | None,
        device: Device | None,
        device_info: DeviceInfo,
        ip_hash: str | None,
    ) -> AuthSession:
        session_row = AuthSession(
            user_id=user.id,
            tenant_id=tenant_id,
            device_id=device.id if device else None,
            app_version=device_info.app_version,
            user_agent=(device_info.user_agent or "")[:300] or None,
            client_ip_hash=ip_hash,
            expires_at=utc_now() + timedelta(days=self._settings.refresh_token_ttl_days),
        )
        self._db.add(session_row)
        await self._db.flush()
        return session_row

    async def _issue_tokens(self, session_row: AuthSession, *, role: str | None) -> tuple[str, str]:
        if self._settings.supabase_auth_active:
            raise AuthenticationError("Use Supabase Auth", code=ErrorCode.FEATURE_DISABLED)
        access_token = self._tokens.issue_access_token(
            user_id=session_row.user_id,
            session_id=session_row.id,
            tenant_id=session_row.tenant_id,
            role=role,
        )
        raw_refresh = generate_opaque_token()
        self._db.add(
            RefreshToken(
                session_id=session_row.id,
                token_hash=self._hasher.token_hash(raw_refresh),
                expires_at=session_row.expires_at,
            )
        )
        await self._db.flush()
        return access_token, raw_refresh

    # ------------------------------------------------------------- refresh --

    async def refresh(self, *, refresh_token: str, client_ip: str | None) -> SignInResult:
        """Rotate a refresh token, detecting reuse."""
        ip_hash = self._hasher.ip_hash(client_ip)
        token_hash = self._hasher.token_hash(refresh_token)

        row = (
            await self._db.execute(
                sa.select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AuthenticationError(
                "Refresh token is not recognised", code=ErrorCode.INVALID_TOKEN
            )

        session_row = await self._db.get(AuthSession, row.session_id)
        if session_row is None:  # pragma: no cover - FK makes this unreachable
            raise AuthenticationError("Session not found", code=ErrorCode.INVALID_TOKEN)

        if row.is_used:
            # Already exchanged. Either the token was captured or the client is
            # badly broken; both warrant killing the session rather than
            # continuing to hand out credentials.
            await self._revoke_session(
                session_row, reason=RevocationReason.TOKEN_REUSE_DETECTED, ip_hash=ip_hash
            )
            await record_audit(
                self._db,
                AuditAction.REFRESH_TOKEN_REUSE_DETECTED,
                entity_type="auth_session",
                entity_id=session_row.id,
                context={"refresh_token_id": str(row.id)},
                actor_type=ActorType.USER,
                actor_id=session_row.user_id,
                tenant_id=session_row.tenant_id,
                client_ip_hash=ip_hash,
            )
            log.warning(
                "refresh token reuse detected; session revoked",
                extra={"session_id": str(session_row.id)},
            )
            await self._persist_then_raise(
                AuthenticationError(
                    "This session has been revoked for security reasons",
                    code=ErrorCode.SESSION_REVOKED,
                )
            )

        if row.is_revoked or row.is_expired():
            raise AuthenticationError(
                "Refresh token is no longer valid", code=ErrorCode.INVALID_TOKEN
            )
        if not session_row.is_usable():
            raise AuthenticationError(
                "This session has been revoked", code=ErrorCode.SESSION_REVOKED
            )

        user = await self._db.get(User, session_row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("This account is not active", code=ErrorCode.SESSION_REVOKED)

        memberships = await self._load_memberships(user.id)
        role = next((m.role for m in memberships if m.id == session_row.tenant_id), None)

        now = utc_now()
        row.used_at = now
        session_row.last_seen_at = now

        access_token, raw_refresh = self._rotate_refresh_token(session_row, row, role=role)
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.SESSION_REFRESHED,
            entity_type="auth_session",
            entity_id=session_row.id,
            actor_type=ActorType.USER,
            actor_id=user.id,
            tenant_id=session_row.tenant_id,
            client_ip_hash=ip_hash,
        )
        await self._db.flush()

        return SignInResult(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in_seconds=self._tokens.access_token_ttl_seconds,
            session_id=session_row.id,
            user_id=user.id,
            tenant_id=session_row.tenant_id,
            role=role,
            is_new_user=False,
            tenants=memberships,
        )

    def _rotate_refresh_token(
        self, session_row: AuthSession, previous: RefreshToken, *, role: str | None
    ) -> tuple[str, str]:
        raw_refresh = generate_opaque_token()
        # The id is assigned here rather than at flush so the previous token can
        # record what replaced it, which is what makes the chain auditable.
        replacement = RefreshToken(
            id=new_id(),
            session_id=session_row.id,
            token_hash=self._hasher.token_hash(raw_refresh),
            expires_at=session_row.expires_at,
        )
        self._db.add(replacement)
        previous.replaced_by_id = replacement.id
        access_token = self._tokens.issue_access_token(
            user_id=session_row.user_id,
            session_id=session_row.id,
            tenant_id=session_row.tenant_id,
            role=role,
        )
        return access_token, raw_refresh

    # -------------------------------------------------------------- revoke --

    async def _revoke_session(
        self,
        session_row: AuthSession,
        *,
        reason: RevocationReason,
        ip_hash: str | None = None,
    ) -> None:
        now = utc_now()
        session_row.revoked_at = now
        session_row.revoked_reason = reason
        await self._db.execute(
            sa.update(RefreshToken)
            .where(RefreshToken.session_id == session_row.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=str(reason))
        )
        await self._db.flush()

    async def logout(self, *, session_id: uuid.UUID, all_devices: bool = False) -> int:
        """Revoke the current session, or every session for the user."""
        session_row = await self._db.get(AuthSession, session_id)
        if session_row is None:
            return 0

        device_filter = (
            Device.user_id == session_row.user_id
            if all_devices
            else Device.id == session_row.device_id
        )
        await self._db.execute(sa.update(Device).where(device_filter).values(push_token=None))

        if not all_devices:
            await self._revoke_session(session_row, reason=RevocationReason.USER_LOGOUT)
            revoked = 1
        else:
            rows = list(
                (
                    await self._db.execute(
                        sa.select(AuthSession).where(
                            AuthSession.user_id == session_row.user_id,
                            AuthSession.revoked_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                await self._revoke_session(row, reason=RevocationReason.LOGOUT_ALL)
            revoked = len(rows)

        await record_audit(
            self._db,
            AuditAction.SESSION_REVOKED,
            entity_type="auth_session",
            entity_id=session_id,
            context={"all_devices": all_devices, "sessions_revoked": revoked},
            actor_type=ActorType.USER,
            actor_id=session_row.user_id,
            tenant_id=session_row.tenant_id,
        )
        await self._db.flush()
        return revoked

    # -------------------------------------------------------- tenant switch --

    async def bind_session_tenant(
        self, *, session_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> SignInResult:
        """Point an existing session at a shop the user actually belongs to.

        Used after onboarding creates the first shop, and when a user with more
        than one shop chooses which to work in. Membership is re-checked here;
        the client's claim is never trusted.
        """
        session_row = await self._db.get(AuthSession, session_id)
        if session_row is None or not session_row.is_usable():
            raise AuthenticationError("Session is no longer valid", code=ErrorCode.SESSION_REVOKED)

        memberships = await self._load_memberships(session_row.user_id)
        match = next((m for m in memberships if m.id == tenant_id), None)
        if match is None:
            raise AuthenticationError(
                "You are not a member of this shop",
                code=ErrorCode.FORBIDDEN,
                http_status=403,
            )

        session_row.tenant_id = tenant_id
        session_row.last_seen_at = utc_now()

        if self._settings.supabase_auth_active:
            await self._db.flush()
            return SignInResult(
                access_token="",
                refresh_token="",
                expires_in_seconds=0,
                session_id=session_row.id,
                user_id=session_row.user_id,
                tenant_id=tenant_id,
                role=match.role,
                is_new_user=False,
                tenants=memberships,
            )

        # Rotate credentials so the new tenant claim takes effect immediately
        # rather than at the next natural refresh.
        await self._db.execute(
            sa.update(RefreshToken)
            .where(RefreshToken.session_id == session_row.id, RefreshToken.used_at.is_(None))
            .values(revoked_at=utc_now(), revoked_reason=str(RevocationReason.ADMIN_ACTION))
        )
        access_token, raw_refresh = await self._issue_tokens(session_row, role=match.role)
        await self._db.flush()

        return SignInResult(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in_seconds=self._tokens.access_token_ttl_seconds,
            session_id=session_row.id,
            user_id=session_row.user_id,
            tenant_id=tenant_id,
            role=match.role,
            is_new_user=False,
            tenants=memberships,
        )

    # ================================================================= #
    # Email + password, Google, Apple
    # ================================================================= #
    #
    # Every method below ends at the same place: :meth:`_complete_sign_in`,
    # which upserts the device, creates the session and issues the token pair.
    # There is one session system, and adding a provider does not add another.

    async def register_with_password(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
        device: DeviceInfo,
        client_ip: str | None,
    ) -> RegistrationResult:
        """Create an account from an email and a password.

        Registration is **not** enumeration-safe, and that is deliberate: a
        sign-up form has to tell you the address is taken or you cannot finish
        the form. The flows that do not need to say — forgot-password and
        resend-verification — do not.
        """
        self._require_auth_method(self._settings.email_password_auth_enabled, "Email sign-in")
        normalized = self._require_email(email)
        validated = validate_password(password)
        ip_hash = self._hasher.ip_hash(client_ip)

        await self._enforce_login_limits(email=normalized, ip_hash=ip_hash, scope="register")

        existing = await self._find_identity(AuthProvider.PASSWORD, normalized)
        if existing is not None:
            raise AppError(
                "An account already exists for this email. Sign in instead.",
                code=ErrorCode.EMAIL_ALREADY_REGISTERED,
            )

        user = User(status=UserStatus.ACTIVE, display_name=display_name)
        self._db.add(user)
        await self._db.flush()

        identity = AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.PASSWORD,
            provider_subject=normalized,
            normalized_email=normalized,
            email_verified=False,
            password_hash=self._passwords.hash(validated),
            password_updated_at=utc_now(),
        )
        self._db.add(identity)
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.USER_REGISTERED,
            entity_type="user",
            entity_id=user.id,
            context={"provider": str(AuthProvider.PASSWORD)},
            actor_type=ActorType.USER,
            actor_id=user.id,
            client_ip_hash=ip_hash,
        )

        verification, sent = await self._send_email_verification(identity, ip_hash=ip_hash)
        result = await self._complete_sign_in(
            user=user,
            identity=identity,
            device=device,
            ip_hash=ip_hash,
            is_new_user=True,
            audit_action=AuditAction.SESSION_CREATED,
        )
        return RegistrationResult(
            session=result, verification_token=verification, email_verification_sent=sent
        )

    async def login_with_password(
        self,
        *,
        email: str,
        password: str,
        device: DeviceInfo,
        client_ip: str | None,
    ) -> SignInResult:
        """Sign in with an email and a password."""
        self._require_auth_method(self._settings.email_password_auth_enabled, "Email sign-in")
        normalized = normalize_email(email)
        ip_hash = self._hasher.ip_hash(client_ip)

        await self._enforce_login_limits(email=normalized, ip_hash=ip_hash, scope="login")

        identity = await self._find_identity(AuthProvider.PASSWORD, normalized)
        # The hash is verified even when no identity was found, against a
        # throwaway digest. Returning early here would make a missing account
        # measurably faster than a wrong password, which is an enumeration
        # oracle that no amount of careful wording in the response can close.
        stored = identity.password_hash if identity is not None else _absent_password_hash()
        matched = self._passwords.verify(password, stored)

        if identity is None or not matched:
            await record_audit(
                self._db,
                AuditAction.PASSWORD_LOGIN_FAILED,
                entity_type="auth_identity",
                entity_id=identity.id if identity else None,
                context={"reason": "no_identity" if identity is None else "bad_password"},
                actor_type=ActorType.ANONYMOUS,
                client_ip_hash=ip_hash,
            )
            await self._persist_then_raise(
                AuthenticationError(
                    "That email and password do not match an account.",
                    code=ErrorCode.INVALID_CREDENTIALS,
                )
            )

        user = await self._require_active_user(identity.user_id)

        if self._settings.email_verification_required_for_login and not identity.email_verified:
            raise AppError("Verify your email address first.", code=ErrorCode.EMAIL_NOT_VERIFIED)

        # Upgrade the stored digest opportunistically. This is the only moment
        # the plaintext exists, so a cost increase can never be applied later.
        if self._passwords.needs_rehash(identity.password_hash):
            identity.password_hash = self._passwords.hash(password)
            identity.password_updated_at = utc_now()

        return await self._complete_sign_in(
            user=user,
            identity=identity,
            device=device,
            ip_hash=ip_hash,
            is_new_user=False,
            audit_action=AuditAction.SESSION_CREATED,
        )

    async def sign_in_with_provider(
        self,
        *,
        verifier: IdTokenVerifier,
        id_token: str,
        device: DeviceInfo,
        client_ip: str | None,
    ) -> SignInResult:
        """Verify a Google or Apple identity token, then sign in.

        Order matters. The token is verified against the provider's keys
        *before* anything is looked up, so a forged or misdirected token never
        reaches the identity tables at all.
        """
        provider = verifier.provider
        enabled = (
            self._settings.google_auth_enabled
            if provider is AuthProvider.GOOGLE
            else self._settings.apple_auth_enabled
        )
        self._require_auth_method(enabled, f"{provider.title()} sign-in")

        ip_hash = self._hasher.ip_hash(client_ip)
        asserted = await verifier.verify(id_token)

        identity = await self._find_identity(provider, asserted.subject)
        is_new_user = False

        if identity is not None:
            user = await self._require_active_user(identity.user_id)
            # The provider's own view of the address can change between
            # sign-ins; `sub` cannot, which is why it is the key and this is
            # only bookkeeping.
            if asserted.email is not None:
                identity.normalized_email = asserted.email
                # Verification belongs to this address and this assertion;
                # a previous verified address cannot verify a replacement.
                identity.email_verified = asserted.email_verified
        else:
            user, identity, is_new_user = await self._link_or_create(asserted, ip_hash=ip_hash)

        return await self._complete_sign_in(
            user=user,
            identity=identity,
            device=device,
            ip_hash=ip_hash,
            is_new_user=is_new_user,
            audit_action=AuditAction.PROVIDER_SIGN_IN,
            context={"provider": str(provider), "is_new_user": is_new_user},
        )

    async def _link_or_create(
        self, asserted: OidcIdentity, *, ip_hash: str | None
    ) -> tuple[User, AuthIdentity, bool]:
        """Attach a new provider identity to an existing user, or start a new one.

        The linking rule is one line of code and several paragraphs of reason:
        both sides must have **proven** the same address.

        Without that, the pre-hijack attack works. Register
        ``victim@example.com`` with a password, never verify it, and wait. When
        the real owner arrives through Google, an email-only match would hand
        them — and their shop — to the account whose address nobody checked.
        Requiring the existing identity to be verified makes that attack land on
        a new, separate account instead, which is the safe failure.

        The cost is a duplicate user in one case: an unverified squatter holds
        the address and the real owner gets a second account. That is the right
        trade. Merging two established users is not reversible; a duplicate is.
        """
        candidate_email = asserted.linkable_email
        if candidate_email is not None:
            match = await self._find_linkable_identity(candidate_email, asserted.provider)
            if match is not None and await self._already_has_provider(
                match.user_id, asserted.provider
            ):
                # The candidate user already signs in with this provider, under
                # a different subject. Attaching a second one would let anybody
                # who can create a provider account bearing a verified address
                # attach themselves to the user who owns it. The database
                # constraint would stop the write; refusing here means a clean
                # answer and an audit row instead of an integrity error.
                await record_audit(
                    self._db,
                    AuditAction.IDENTITY_LINK_REFUSED,
                    entity_type="auth_identity",
                    entity_id=match.id,
                    context={
                        "provider": str(asserted.provider),
                        "reason": "user_already_has_this_provider",
                    },
                    actor_type=ActorType.ANONYMOUS,
                    client_ip_hash=ip_hash,
                )
                match = None

            if match is not None:
                user = await self._require_active_user(match.user_id)
                identity = AuthIdentity(
                    user_id=user.id,
                    provider=asserted.provider,
                    provider_subject=asserted.subject,
                    normalized_email=asserted.email,
                    email_verified=asserted.email_verified,
                )
                self._db.add(identity)
                await self._db.flush()
                await record_audit(
                    self._db,
                    AuditAction.IDENTITY_LINKED,
                    entity_type="auth_identity",
                    entity_id=identity.id,
                    context={
                        "provider": str(asserted.provider),
                        "linked_to_provider": match.provider,
                        "matched_on": "verified_email",
                    },
                    actor_type=ActorType.USER,
                    actor_id=user.id,
                    client_ip_hash=ip_hash,
                )
                return user, identity, False

            blocked = await self._find_identity_by_email(candidate_email, asserted.provider)
            if blocked is not None:
                # Same address, but unproven on the existing side. Recorded
                # rather than silently ignored: a run of these against one
                # address is what a takeover attempt looks like from in here.
                await record_audit(
                    self._db,
                    AuditAction.IDENTITY_LINK_REFUSED,
                    entity_type="auth_identity",
                    entity_id=blocked.id,
                    context={
                        "provider": str(asserted.provider),
                        "reason": "existing_identity_email_unverified",
                    },
                    actor_type=ActorType.ANONYMOUS,
                    client_ip_hash=ip_hash,
                )

        user = User(status=UserStatus.ACTIVE, display_name=asserted.display_name)
        self._db.add(user)
        await self._db.flush()
        identity = AuthIdentity(
            user_id=user.id,
            provider=asserted.provider,
            provider_subject=asserted.subject,
            normalized_email=asserted.email,
            email_verified=asserted.email_verified,
        )
        self._db.add(identity)
        await self._db.flush()
        await record_audit(
            self._db,
            AuditAction.USER_REGISTERED,
            entity_type="user",
            entity_id=user.id,
            context={"provider": str(asserted.provider)},
            actor_type=ActorType.USER,
            actor_id=user.id,
            client_ip_hash=ip_hash,
        )
        return user, identity, True

    # --------------------------------------------------- email verification --

    async def resend_email_verification(self, *, email: str, client_ip: str | None) -> str | None:
        """Re-send a verification link, saying nothing about whether one was sent."""
        normalized = normalize_email(email)
        ip_hash = self._hasher.ip_hash(client_ip)
        await self._enforce_login_limits(email=normalized, ip_hash=ip_hash, scope="verify")

        identity = await self._find_identity(AuthProvider.PASSWORD, normalized)
        if identity is None or identity.email_verified:
            return None
        token, _sent = await self._send_email_verification(identity, ip_hash=ip_hash)
        return token

    async def verify_email(self, *, token: str, client_ip: str | None) -> uuid.UUID:
        """Consume a verification token and mark the address proven."""
        ip_hash = self._hasher.ip_hash(client_ip)
        row = await self._consume_auth_token(
            token, purpose=AuthTokenPurpose.EMAIL_VERIFICATION, ip_hash=ip_hash
        )
        identity = await self._db.get(AuthIdentity, row.identity_id)
        if identity is None:
            raise AuthenticationError(
                "This verification link is no longer valid.", code=ErrorCode.INVALID_TOKEN
            )

        identity.email_verified = True
        await record_audit(
            self._db,
            AuditAction.EMAIL_VERIFIED,
            entity_type="auth_identity",
            entity_id=identity.id,
            context={"provider": identity.provider},
            actor_type=ActorType.USER,
            actor_id=identity.user_id,
            client_ip_hash=ip_hash,
        )
        await self._db.flush()
        return identity.user_id

    # ---------------------------------------------------------- passwords --

    async def request_password_reset(self, *, email: str, client_ip: str | None) -> str | None:
        """Start a reset, or appear to.

        The response is identical whether or not the address has an account.
        Anything else turns this endpoint into a list of which of a leaked
        address dump are ecomsbd sellers — and sellers are a small, targetable
        population. The caller returns the same body regardless; the token this
        returns is for the transport, never for the response.
        """
        normalized = normalize_email(email)
        ip_hash = self._hasher.ip_hash(client_ip)
        await self._enforce_login_limits(email=normalized, ip_hash=ip_hash, scope="reset")

        identity = await self._find_identity(AuthProvider.PASSWORD, normalized)
        if identity is None:
            return None

        await record_audit(
            self._db,
            AuditAction.PASSWORD_RESET_REQUESTED,
            entity_type="auth_identity",
            entity_id=identity.id,
            context={},
            actor_type=ActorType.ANONYMOUS,
            client_ip_hash=ip_hash,
        )
        raw = await self._issue_auth_token(
            identity,
            purpose=AuthTokenPurpose.PASSWORD_RESET,
            ttl_seconds=self._settings.password_reset_ttl_seconds,
            ip_hash=ip_hash,
        )
        await self._email_reset_link(identity, raw)
        await self._db.flush()
        return raw

    async def reset_password(
        self, *, token: str, new_password: str, client_ip: str | None
    ) -> uuid.UUID:
        """Set a new password from a reset token, and end every existing session.

        Revoking sessions is the point of the flow, not a side effect. A reset
        is what a person does when they believe someone else has their account;
        leaving that someone else signed in on their own device would make the
        reset theatre.
        """
        validated = validate_password(new_password)
        ip_hash = self._hasher.ip_hash(client_ip)
        row = await self._consume_auth_token(
            token, purpose=AuthTokenPurpose.PASSWORD_RESET, ip_hash=ip_hash
        )
        identity = await self._db.get(AuthIdentity, row.identity_id)
        if identity is None:
            raise AuthenticationError(
                "This reset link is no longer valid.", code=ErrorCode.INVALID_TOKEN
            )

        identity.password_hash = self._passwords.hash(validated)
        identity.password_updated_at = utc_now()
        # Completing a reset also proves the address: the link only reachable
        # from that inbox was followed.
        identity.email_verified = True

        revoked = await self._revoke_all_sessions(
            identity.user_id, reason=RevocationReason.ADMIN_ACTION
        )
        await self._invalidate_tokens(
            identity.id, purpose=AuthTokenPurpose.PASSWORD_RESET, keep=row.id
        )

        await record_audit(
            self._db,
            AuditAction.PASSWORD_RESET_COMPLETED,
            entity_type="auth_identity",
            entity_id=identity.id,
            context={"sessions_revoked": revoked},
            actor_type=ActorType.USER,
            actor_id=identity.user_id,
            client_ip_hash=ip_hash,
        )
        await self._db.flush()
        return identity.user_id

    async def change_password(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        current_password: str,
        new_password: str,
    ) -> int:
        """Change a password from inside a session.

        The current password is required even though the caller is already
        authenticated: a borrowed unlocked phone should not be enough to lock
        its owner out of their own shop.

        Every *other* session is revoked. The one doing the changing survives,
        because signing someone out of the screen they are looking at to tell
        them their password changed is a worse experience than the risk it
        removes.
        """
        identity = (
            await self._db.execute(
                sa.select(AuthIdentity).where(
                    AuthIdentity.user_id == user_id,
                    AuthIdentity.provider == AuthProvider.PASSWORD,
                )
            )
        ).scalar_one_or_none()
        if identity is None:
            raise AppError(
                "This account has no password to change.",
                code=ErrorCode.VALIDATION_ERROR,
                details={"field": "current_password"},
            )
        if not self._passwords.verify(current_password, identity.password_hash):
            await record_audit(
                self._db,
                AuditAction.PASSWORD_LOGIN_FAILED,
                entity_type="auth_identity",
                entity_id=identity.id,
                context={"reason": "change_password_current_mismatch"},
                actor_type=ActorType.USER,
                actor_id=user_id,
            )
            await self._persist_then_raise(
                AuthenticationError(
                    "That is not your current password.",
                    code=ErrorCode.INVALID_CREDENTIALS,
                )
            )

        identity.password_hash = self._passwords.hash(validate_password(new_password))
        identity.password_updated_at = utc_now()
        revoked = await self._revoke_all_sessions(
            user_id, reason=RevocationReason.USER_LOGOUT, keep_session_id=session_id
        )
        await self._invalidate_tokens(identity.id, purpose=AuthTokenPurpose.PASSWORD_RESET)

        await record_audit(
            self._db,
            AuditAction.PASSWORD_CHANGED,
            entity_type="auth_identity",
            entity_id=identity.id,
            context={"other_sessions_revoked": revoked},
            actor_type=ActorType.USER,
            actor_id=user_id,
        )
        await self._db.flush()
        return revoked

    # ------------------------------------------------------ shared helpers --

    def _require_auth_method(self, enabled: bool, label: str) -> None:
        if not enabled:
            raise AppError(
                f"{label} is not available.",
                code=ErrorCode.FEATURE_DISABLED,
                message_bn="এই সাইন-ইন পদ্ধতি এখন বন্ধ আছে।",
            )

    def _require_email(self, raw: str) -> str:
        normalized = normalize_email(raw)
        if not _EMAIL_RE.match(normalized) or len(normalized) > MAX_EMAIL_LENGTH:
            raise ValidationError(
                "Enter a valid email address.",
                message_bn="সঠিক ইমেইল ঠিকানা দিন।",
                details={"field": "email"},
            )
        return normalized

    async def _require_active_user(self, user_id: uuid.UUID) -> User:
        user = await self._db.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError(
                "This account is not active", code=ErrorCode.FORBIDDEN, http_status=403
            )
        return user

    async def _find_identity(self, provider: AuthProvider, subject: str) -> AuthIdentity | None:
        return (
            await self._db.execute(
                sa.select(AuthIdentity).where(
                    AuthIdentity.provider == provider,
                    AuthIdentity.provider_subject == subject,
                )
            )
        ).scalar_one_or_none()

    async def _find_identity_by_email(
        self, email: str, provider: AuthProvider
    ) -> AuthIdentity | None:
        return (
            await self._db.execute(
                sa.select(AuthIdentity)
                .where(
                    AuthIdentity.normalized_email == email,
                    AuthIdentity.provider != provider,
                )
                .order_by(AuthIdentity.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _already_has_provider(self, user_id: uuid.UUID, provider: AuthProvider) -> bool:
        """Whether this user already signs in with this provider.

        One identity per provider per user, so "which Google account owns this
        shop?" always has one answer.
        """
        found = (
            await self._db.execute(
                sa.select(AuthIdentity.id).where(
                    AuthIdentity.user_id == user_id,
                    AuthIdentity.provider == provider,
                )
            )
        ).first()
        return found is not None

    async def _find_linkable_identity(
        self, email: str, provider: AuthProvider
    ) -> AuthIdentity | None:
        """The one existing identity a new provider identity may join.

        More than one match means two separate users already hold the same
        proven address, which should not happen and is not something to guess
        about — nothing is linked, and a new account is created instead.
        """
        rows = list(
            (
                await self._db.execute(
                    sa.select(AuthIdentity).where(
                        AuthIdentity.normalized_email == email,
                        AuthIdentity.email_verified.is_(True),
                        AuthIdentity.provider != provider,
                    )
                )
            )
            .scalars()
            .all()
        )
        distinct_users = {row.user_id for row in rows}
        if len(distinct_users) != 1:
            return None
        return rows[0]

    async def _enforce_login_limits(self, *, email: str, ip_hash: str | None, scope: str) -> None:
        """Throttle by address and by network.

        The email key is hashed rather than used raw: the rate-limit backend is
        Redis, and a key list there should not also be a list of who has an
        ecomsbd account.
        """
        settings = self._settings
        email_result = await self._limits.hit(
            f"auth:{scope}:email",
            self._hasher.token_hash(email),
            limit=settings.login_max_attempts_per_email_hour,
            window_seconds=3600,
        )
        if email_result.exceeded:
            raise RateLimitedError(
                "Too many attempts for this account. Try again later.",
                retry_after_seconds=email_result.retry_after_seconds,
            )
        if ip_hash is not None:
            ip_result = await self._limits.hit(
                f"auth:{scope}:ip",
                ip_hash,
                limit=settings.login_max_attempts_per_ip_hour,
                window_seconds=3600,
            )
            if ip_result.exceeded:
                raise RateLimitedError(
                    "Too many attempts from this network. Try again later.",
                    retry_after_seconds=ip_result.retry_after_seconds,
                )

    async def _issue_auth_token(
        self,
        identity: AuthIdentity,
        *,
        purpose: AuthTokenPurpose,
        ttl_seconds: int,
        ip_hash: str | None,
    ) -> str:
        """Mint a single-use link token, superseding any earlier one.

        Superseding matters: two live reset links double the window in which a
        stolen inbox is an account, for no benefit to the person who asked.
        """
        await self._invalidate_tokens(identity.id, purpose=purpose)
        raw = generate_opaque_token()
        self._db.add(
            AuthToken(
                identity_id=identity.id,
                user_id=identity.user_id,
                purpose=purpose,
                token_hash=self._hasher.token_hash(raw),
                expires_at=utc_now() + timedelta(seconds=ttl_seconds),
                request_ip_hash=ip_hash,
            )
        )
        await self._db.flush()
        return raw

    async def _invalidate_tokens(
        self,
        identity_id: uuid.UUID,
        *,
        purpose: AuthTokenPurpose,
        keep: uuid.UUID | None = None,
    ) -> None:
        conditions = [
            AuthToken.identity_id == identity_id,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
            AuthToken.invalidated_at.is_(None),
        ]
        if keep is not None:
            conditions.append(AuthToken.id != keep)
        await self._db.execute(
            sa.update(AuthToken).where(*conditions).values(invalidated_at=utc_now())
        )

    async def _consume_auth_token(
        self, raw: str, *, purpose: AuthTokenPurpose, ip_hash: str | None
    ) -> AuthToken:
        """Look up, check and burn a link token.

        A replayed token is audited before it is refused, and the audit row is
        committed even though the request fails — the whole value of recording
        a replay is lost if the rollback that accompanies the refusal takes the
        record with it.
        """
        row = (
            await self._db.execute(
                sa.select(AuthToken).where(
                    AuthToken.token_hash == self._hasher.token_hash(raw or ""),
                    AuthToken.purpose == purpose,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            raise AuthenticationError("This link is not valid.", code=ErrorCode.INVALID_TOKEN)
        if row.is_consumed:
            await record_audit(
                self._db,
                AuditAction.AUTH_TOKEN_REPLAY_BLOCKED,
                entity_type="auth_token",
                entity_id=row.id,
                context={"purpose": str(purpose)},
                actor_type=ActorType.ANONYMOUS,
                client_ip_hash=ip_hash,
            )
            await self._persist_then_raise(
                AuthenticationError(
                    "This link has already been used.", code=ErrorCode.INVALID_TOKEN
                )
            )
        if not row.is_usable():
            raise AuthenticationError(
                "This link has expired. Ask for a new one.", code=ErrorCode.TOKEN_EXPIRED
            )

        # Compare-and-set in the database: two requests may both read an unused
        # token, but only one may consume it, including on SQLite.
        now = utc_now()
        consumed = (
            await self._db.execute(
                sa.update(AuthToken)
                .where(
                    AuthToken.id == row.id,
                    AuthToken.consumed_at.is_(None),
                    AuthToken.invalidated_at.is_(None),
                    AuthToken.expires_at > now,
                )
                .values(consumed_at=now)
                .returning(AuthToken.id)
            )
        ).scalar_one_or_none()
        if consumed is None:
            raise AuthenticationError("This link is no longer valid.", code=ErrorCode.INVALID_TOKEN)
        return row

    async def _send_email_verification(
        self, identity: AuthIdentity, *, ip_hash: str | None
    ) -> tuple[str | None, bool]:
        if identity.normalized_email is None:
            return None, False
        raw = await self._issue_auth_token(
            identity,
            purpose=AuthTokenPurpose.EMAIL_VERIFICATION,
            ttl_seconds=self._settings.email_verification_ttl_seconds,
            ip_hash=ip_hash,
        )
        delivery = await self._email.send(
            EmailMessage(
                to_address=identity.normalized_email,
                subject="Verify your ecomsbd email",
                text_body=(
                    "Confirm this address to finish setting up your ecomsbd account.\n\n"
                    f"{self._link('/auth/email/verify', raw)}\n\n"
                    "If you did not create an ecomsbd account, ignore this message."
                ),
                reply_to=self._settings.support_email,
                idempotency_key=f"verify:{identity.id}:{self._hasher.token_hash(raw)}",
            )
        )
        await record_audit(
            self._db,
            AuditAction.EMAIL_VERIFICATION_SENT
            if delivery.delivered
            else AuditAction.EMAIL_DELIVERY_FAILED,
            entity_type="auth_identity",
            entity_id=identity.id,
            context={"transport": self._email.name, "outcome": str(delivery.outcome)},
            actor_type=ActorType.USER,
            actor_id=identity.user_id,
            client_ip_hash=ip_hash,
        )
        return raw, delivery.delivered

    async def _email_reset_link(self, identity: AuthIdentity, raw: str) -> None:
        if identity.normalized_email is None:
            return
        delivery = await self._email.send(
            EmailMessage(
                to_address=identity.normalized_email,
                subject="Reset your ecomsbd password",
                text_body=(
                    "Use this link to choose a new ecomsbd password. It works once "
                    "and expires shortly.\n\n"
                    f"{self._link('/auth/password/reset', raw)}\n\n"
                    "If you did not ask for this, nothing has changed and you can "
                    "ignore this message."
                ),
                reply_to=self._settings.support_email,
                idempotency_key=f"reset:{identity.id}:{self._hasher.token_hash(raw)}",
            )
        )
        if not delivery.delivered:
            await record_audit(
                self._db,
                AuditAction.EMAIL_DELIVERY_FAILED,
                entity_type="auth_identity",
                entity_id=identity.id,
                context={"transport": self._email.name, "outcome": str(delivery.outcome)},
                actor_type=ActorType.ANONYMOUS,
            )

    def _link(self, path: str, token: str) -> str:
        # The API serves both landing pages. Fragments keep tokens out of HTTP
        # requests, proxy access logs and referrers; JS posts them in the body.
        base = self._settings.public_base_url.rstrip("/")
        return f"{base}{path}#token={token}"

    async def _revoke_all_sessions(
        self,
        user_id: uuid.UUID,
        *,
        reason: RevocationReason,
        keep_session_id: uuid.UUID | None = None,
    ) -> int:
        rows = list(
            (
                await self._db.execute(
                    sa.select(AuthSession).where(
                        AuthSession.user_id == user_id,
                        AuthSession.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        revoked = 0
        for row in rows:
            if keep_session_id is not None and row.id == keep_session_id:
                continue
            await self._revoke_session(row, reason=reason)
            revoked += 1
        return revoked

    async def _complete_sign_in(
        self,
        *,
        user: User,
        identity: AuthIdentity,
        device: DeviceInfo,
        ip_hash: str | None,
        is_new_user: bool,
        audit_action: AuditAction,
        context: dict[str, object] | None = None,
    ) -> SignInResult:
        """The one path to a session, whichever identity proved the sign-in.

        Onboarding is decided here too, and only here: a user with one shop is
        bound to it, a user with several must choose, and a user with none is
        told to create one. Every provider gets the same answer because every
        provider ends up in this method.
        """
        memberships = await self._load_memberships(user.id)
        active_tenant = memberships[0] if len(memberships) == 1 else None

        device_row = await self._upsert_device(user_id=user.id, device=device)
        session_row = await self._create_session(
            user=user,
            tenant_id=active_tenant.id if active_tenant else None,
            device=device_row,
            device_info=device,
            ip_hash=ip_hash,
        )
        access_token, refresh_token = await self._issue_tokens(
            session_row, role=active_tenant.role if active_tenant else None
        )

        now = utc_now()
        user.last_login_at = now
        identity.last_login_at = now

        await record_audit(
            self._db,
            audit_action,
            entity_type="user",
            entity_id=user.id,
            context={
                "provider": identity.provider,
                "is_new_user": is_new_user,
                "tenant_count": len(memberships),
                **(context or {}),
            },
            actor_type=ActorType.USER,
            actor_id=user.id,
            tenant_id=session_row.tenant_id,
            client_ip_hash=ip_hash,
        )
        await enqueue(
            self._db,
            OutboxTopic.USER_SIGNED_IN,
            {"user_id": str(user.id), "is_new_user": is_new_user},
            tenant_id=session_row.tenant_id,
        )
        await self._db.flush()

        return SignInResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_seconds=self._tokens.access_token_ttl_seconds,
            session_id=session_row.id,
            user_id=user.id,
            tenant_id=session_row.tenant_id,
            role=active_tenant.role if active_tenant else None,
            is_new_user=is_new_user,
            tenants=memberships,
        )
