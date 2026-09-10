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

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import NoReturn

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import (
    AuthSession,
    Device,
    DevicePlatform,
    OtpChallenge,
    OtpPurpose,
    RefreshToken,
    RevocationReason,
)
from app.auth.otp_providers import OtpSender
from app.common.audit import AuditAction, record_audit
from app.common.cache import RateLimiter
from app.common.outbox import OutboxTopic, enqueue
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.context import ActorType
from app.core.errors import AppError, AuthenticationError, ErrorCode, RateLimitedError
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
from app.tenants.models import Tenant, TenantStatus, TenantUser
from app.users.models import User, UserStatus

__all__ = ["AuthService", "ChallengeResult", "DeviceInfo", "SignInResult"]

log = get_logger(__name__)

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
    ) -> None:
        self._db = session
        self._settings = settings
        self._hasher = hasher
        self._vault = vault
        self._tokens = tokens
        self._otp = otp_sender
        self._limits = rate_limiter

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
