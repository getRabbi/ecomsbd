"""Supabase seller credentials; internal identities and shop routing stay in FastAPI."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identities import AuthIdentity, AuthProvider, normalize_email
from app.auth.models import AuthSession
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, ErrorCode
from app.users.models import User


def invalid_token() -> AuthenticationError:
    return AuthenticationError("Invalid Supabase session", code=ErrorCode.INVALID_TOKEN)


def provider_unavailable() -> AuthenticationError:
    """The key set could not be read: the request fails, the token is not judged."""
    return AuthenticationError(
        "Authentication provider unavailable",
        code=ErrorCode.SERVICE_UNAVAILABLE,
        http_status=503,
    )


@dataclass(frozen=True)
class SupabaseClaims:
    subject: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


class SupabaseVerifier:
    """Bounded JWKS cache, single-flight refresh, rotation, and no stale-on-error use."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.issuer = (settings.supabase_url or "").rstrip("/") + "/auth/v1"
        self._keys: list[dict[str, Any]] = []
        self._fetched = -float("inf")
        self._attempted = -float("inf")
        self._lock = asyncio.Lock()

    async def _fetch_keys(self, *, force: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            now = time.monotonic()
            if not force and now - self._fetched < 600:
                return self._keys
            if now - self._attempted < 30:
                if now - self._fetched < 600:
                    return self._keys
                # A fetch failed moments ago and no fresh key set is held: the
                # provider is unreachable, which says nothing about this token.
                # INVALID_TOKEN here made clients discard sessions that were fine.
                raise provider_unavailable()
            self._attempted = now
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(self.issuer + "/.well-known/jwks.json")
                    response.raise_for_status()
                    keys = response.json()["keys"]
                    if not isinstance(keys, list) or len(keys) > 20:
                        raise ValueError("Invalid key set")
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise provider_unavailable() from exc
            self._keys, self._fetched = keys, now
            return keys

    async def verify(self, token: str) -> SupabaseClaims:
        try:
            if len(token) > 16384:
                raise ValueError("Oversize token")
            header = jwt.get_unverified_header(token)
            algorithm, kid = header.get("alg"), header.get("kid")
            if algorithm not in ("ES256", "RS256") or not isinstance(kid, str) or not kid:
                raise ValueError("Unsupported signing key")
            entry = None
            for force in (False, True):
                keys = await self._fetch_keys(force=force)
                entry = next((k for k in keys if k.get("kid") == kid), None)
                if entry is not None:
                    break
            if entry is None or entry.get("alg") != algorithm or entry.get("use", "sig") != "sig":
                raise ValueError("Unknown signing key")
            claims = jwt.decode(
                token,
                jwt.PyJWK(entry).key,
                algorithms=[algorithm],
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["exp", "iat", "sub", "iss", "aud", "session_id", "role"]},
            )
            if claims["role"] != "authenticated" or claims.get("is_anonymous", False):
                raise ValueError("Not a seller session")
            # SMS/phone sessions are never a supported seller credential.
            if any(m.get("method") in ("sms", "phone") for m in claims.get("amr", [])):
                raise ValueError("Phone login disabled")
            if claims.get("app_metadata", {}).get("provider") == "phone":
                raise ValueError("Phone login disabled")
            return SupabaseClaims(
                uuid.UUID(claims["sub"]),
                uuid.UUID(claims["session_id"]),
                datetime.fromtimestamp(claims["exp"], UTC),
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Access token expired", code=ErrorCode.TOKEN_EXPIRED) from exc
        except (
            jwt.PyJWTError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
        ) as exc:
            raise invalid_token() from exc

    async def verified_email(self, token: str, claims: SupabaseClaims) -> str:
        """Read server-owned confirmation state; user_metadata is user-editable."""
        key = self.settings.supabase_anon_key
        if not key:
            raise invalid_token()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    self.issuer + "/user",
                    headers={"apikey": key.get_secret_value(), "Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                user = response.json()
            if (
                user.get("id") != str(claims.subject)
                or not user.get("email_confirmed_at")
                or not user.get("email")
                or user.get("is_anonymous")
            ):
                raise invalid_token()
            return normalize_email(user["email"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise invalid_token() from exc


async def resolve_user(
    db: AsyncSession,
    verifier: SupabaseVerifier,
    token: str,
    claims: SupabaseClaims,
) -> User:
    """Unique constraints arbitrate concurrent first logins, including all new user writes."""
    query = sa.select(AuthIdentity).where(
        AuthIdentity.provider == AuthProvider.SUPABASE,
        AuthIdentity.provider_subject == str(claims.subject),
    )
    identity = (await db.execute(query)).scalar_one_or_none()
    if identity is None:
        email = await verifier.verified_email(token, claims)
        try:
            async with db.begin_nested():
                candidates = (
                    (
                        await db.execute(
                            sa.select(AuthIdentity)
                            .where(
                                AuthIdentity.normalized_email == email,
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                owners = {row.user_id for row in candidates}
                if candidates:
                    # Both sides verified, exactly one owner, and no prior Supabase binding.
                    if len(owners) != 1 or not all(row.email_verified for row in candidates):
                        raise ConflictError("Account ownership needs support verification")
                    user = (
                        await db.execute(
                            sa.select(User)
                            .where(
                                User.id == next(iter(owners)),
                            )
                            .with_for_update()
                        )
                    ).scalar_one()
                    prior = (
                        await db.execute(
                            sa.select(AuthIdentity).where(
                                AuthIdentity.user_id == user.id,
                                AuthIdentity.provider == AuthProvider.SUPABASE,
                            )
                        )
                    ).scalar_one_or_none()
                    if prior is not None:
                        if prior.provider_subject != str(claims.subject):
                            raise ConflictError("Account already has a different identity")
                        identity = prior
                else:
                    user = User()
                    db.add(user)
                    await db.flush()
                if identity is None:
                    identity = AuthIdentity(
                        user_id=user.id,
                        provider=AuthProvider.SUPABASE,
                        provider_subject=str(claims.subject),
                        normalized_email=email,
                        email_verified=True,
                    )
                    db.add(identity)
                    await db.flush()
        except IntegrityError:
            identity = (await db.execute(query)).scalar_one_or_none()
            if identity is None:
                raise ConflictError("Account ownership needs support verification") from None
    resolved_user = await db.get(User, identity.user_id)
    if resolved_user is None or not resolved_user.is_active:
        raise AuthenticationError("Account is not active", code=ErrorCode.SESSION_REVOKED)
    return resolved_user


async def resolve_session(db: AsyncSession, claims: SupabaseClaims, user: User) -> AuthSession:
    """Only routing/revocation metadata; this row never issues or stores a credential."""
    row = await db.get(AuthSession, claims.session_id)
    if row is None:
        try:
            async with db.begin_nested():
                row = AuthSession(
                    id=claims.session_id, user_id=user.id, expires_at=claims.expires_at
                )
                db.add(row)
                await db.flush()
        except IntegrityError:
            row = await db.get(AuthSession, claims.session_id)
    if row is None or row.user_id != user.id or row.is_revoked:
        raise AuthenticationError("Session revoked", code=ErrorCode.SESSION_REVOKED)
    row.expires_at = max(row.expires_at, claims.expires_at)
    row.last_seen_at = utc_now()
    return row


async def delete_auth_identity(subject: str, settings: Settings) -> None:
    """Called by the retryable business deletion job, only for a user's final shop."""
    if not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for account deletion")
    secret = settings.supabase_service_role_key.get_secret_value()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.delete(
            (settings.supabase_url or "").rstrip("/")
            + f"/auth/v1/admin/users/{uuid.UUID(subject)}",
            headers={"apikey": secret, "Authorization": f"Bearer {secret}"},
        )
    if response.status_code not in (200, 204, 404):
        # Do not retain provider response bodies or secrets in job errors.
        raise RuntimeError("Supabase account deletion failed; retry required")
