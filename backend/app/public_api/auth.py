from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import get_hasher
from app.common.cache import RateLimiter
from app.core.clock import utc_now
from app.core.context import ActorType, clear_context, current_context, set_context
from app.core.errors import AuthenticationError, ForbiddenError, RateLimitedError
from app.db.session import system_session
from app.public_api.models import ApiKey
from app.tenants.models import Tenant

SCOPES = frozenset(
    {
        "orders:read",
        "orders:write",
        "customers:read",
        "customers:write",
        "products:read",
        "products:write",
        "inventory:read",
        "inventory:write",
        "sources:write",
    }
)


@dataclass(frozen=True)
class ApiPrincipal:
    key_id: uuid.UUID
    tenant_id: uuid.UUID
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ForbiddenError(
                "API key does not grant the required scope", details={"scope": scope}
            )


async def authenticate(request: Request) -> AsyncIterator[ApiPrincipal]:
    limiter = RateLimiter()
    ip = get_hasher().ip_hash(request.client.host if request.client else "unknown") or "unknown"
    result = await limiter.hit("public-auth", ip, limit=1000, window_seconds=60)
    if not result.allowed:
        raise RateLimitedError(retry_after_seconds=result.retry_after_seconds)
    value = request.headers.get("authorization", "")
    try:
        scheme, token = value.split(" ", 1)
        identity, secret = token.removeprefix("ec_live_").split(".", 1)
        key_id = uuid.UUID(hex=identity)
        if scheme.lower() != "bearer" or not token.startswith("ec_live_") or len(secret) != 43:
            raise ValueError
    except ValueError as exc:
        raise AuthenticationError("A public API key is required") from exc
    async with system_session("public API credential verification") as db:
        key = await db.get(ApiKey, key_id)
        if (
            key is None
            or key.revoked_at
            or not get_hasher().verify_token("public:" + token, key.secret_hash)
        ):
            raise AuthenticationError("Invalid or revoked API key")
        tenant = await db.get(Tenant, key.tenant_id)
        if tenant is None or tenant.status != "ACTIVE" or tenant.deleted_at is not None:
            raise AuthenticationError("Shop is unavailable")
        principal = ApiPrincipal(key.id, key.tenant_id, frozenset(key.scopes))
        rate_limit = key.rate_limit
        # Integration health shows "last API call"; one write a minute is enough.
        now = utc_now()
        if key.last_used_at is None or now - key.last_used_at >= timedelta(minutes=1):
            key.last_used_at = now
    for scope, identity, limit in (
        ("public-key", str(principal.key_id), rate_limit),
        ("public-shop", str(principal.tenant_id), 1000),
    ):
        result = await limiter.hit(scope, identity, limit=limit, window_seconds=60)
        if not result.allowed:
            raise RateLimitedError(retry_after_seconds=result.retry_after_seconds)
    context = set_context(
        replace(
            current_context(),
            tenant_id=principal.tenant_id,
            actor_type=ActorType.PROVIDER,
            user_id=None,
            session_id=None,
            job_name=f"public-api:{principal.key_id}",
        )
    )
    try:
        yield principal
    finally:
        clear_context(context)


PublicPrincipal = Annotated[ApiPrincipal, Depends(authenticate)]
