"""``require_ingest_key(scope)``: device authentication for intake routes only.

The key is looked up by its SHA-256 in a system session (the request has no
tenant yet), then the request runs with that shop as its tenant — the same
binding the public API uses. Everything else in the API refuses an ``edk_``
key outright (``app.api.deps.get_optional_principal``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import Request

from app.common.cache import RateLimiter
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.clock import utc_now
from app.core.context import ActorType, clear_context, current_context, set_context
from app.core.errors import (
    AppError,
    AuthenticationError,
    ErrorCode,
    ForbiddenError,
    RateLimitedError,
)
from app.db.session import system_session
from app.device_links.models import DeviceIngestKey
from app.device_links.service import DEVICE_PROVIDER, KEY_PREFIX, hash_secret
from app.integrations.models import IntegrationConnection
from app.tenants.models import Tenant

__all__ = ["KEY_LENGTH", "IngestPrincipal", "require_ingest_key"]

KEY_LENGTH = len(KEY_PREFIX) + 40
#: Captures are bursts of notifications; this is far above a real phone.
_PER_KEY_PER_MINUTE = 600


@dataclass(frozen=True, slots=True)
class IngestPrincipal:
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    key_id: uuid.UUID
    scope: str


def _key_from(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX) or len(token) != KEY_LENGTH:
        raise AuthenticationError("A device key is required")
    return token


def require_ingest_key(scope: str) -> Callable[..., Any]:
    async def dependency(request: Request) -> AsyncIterator[IngestPrincipal]:
        token = _key_from(request)
        key_hash = hash_secret(token)
        limited = await RateLimiter().hit(
            "device-key", key_hash[:24], limit=_PER_KEY_PER_MINUTE, window_seconds=60
        )
        if not limited.allowed:
            raise RateLimitedError(retry_after_seconds=limited.retry_after_seconds)
        async with system_session("device ingest key verification") as db:
            key = (
                await db.execute(
                    sa.select(DeviceIngestKey).where(DeviceIngestKey.key_hash == key_hash)
                )
            ).scalar_one_or_none()
            if key is None or key.revoked_at is not None:
                raise AuthenticationError("This device key is not valid")
            if key.scope != scope:
                raise ForbiddenError("This device key cannot do that", details={"scope": scope})
            conn = await db.get(IntegrationConnection, key.connection_id)
            if conn is None or conn.provider != DEVICE_PROVIDER or conn.state != "CONNECTED":
                raise AuthenticationError("This device is no longer linked")
            tenant = await db.get(Tenant, key.tenant_id)
            if tenant is None or tenant.status != "ACTIVE" or tenant.deleted_at is not None:
                raise AuthenticationError("Shop is unavailable")
            if not await FeatureFlagService(db).is_enabled(
                FlagKey.INTAKE_DEVICE_ENABLED, tenant_id=key.tenant_id
            ):
                raise AppError("Linked devices are not enabled", code=ErrorCode.FEATURE_DISABLED)
            if (conn.config or {}).get("platform") == "extension" and not await FeatureFlagService(
                db
            ).is_enabled(FlagKey.EXTENSION_ENABLED, tenant_id=key.tenant_id):
                # V4 Phase 5: `extension_enabled` off stops every linked
                # extension at once (rules, heartbeat, capture, unlink).
                raise AppError(
                    "The browser extension is not enabled", code=ErrorCode.FEATURE_DISABLED
                )
            now = utc_now()
            if key.last_used_at is None or now - key.last_used_at >= timedelta(minutes=1):
                key.last_used_at = now
            principal = IngestPrincipal(
                tenant_id=key.tenant_id, connection_id=conn.id, key_id=key.id, scope=key.scope
            )
        context = set_context(
            replace(
                current_context(),
                tenant_id=principal.tenant_id,
                actor_type=ActorType.PROVIDER,
                user_id=None,
                session_id=None,
                job_name=f"device:{principal.connection_id}",
            )
        )
        try:
            yield principal
        finally:
            clear_context(context)

    return dependency
