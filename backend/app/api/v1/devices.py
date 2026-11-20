"""Linked devices (V4 WP1.1).

* ``POST /v1/devices/link`` — a signed-in seller's phone links itself and
  receives its ingest key (shown once). Idempotent per install id.
* ``POST /v1/devices/pair`` — the web shows a 6-digit code (10 minutes, one
  use) for a phone that is not signed in.
* ``POST /v1/devices/pair/claim`` — unauthenticated, rate-limited per IP; the
  phone exchanges the code for its key.
* ``POST /v1/intake/heartbeat`` — the first intake route: the only kind of
  route an ingest key is valid on.
* ``GET /v1/devices/extensions`` — the shop's linked browser extensions, for
  the web Settings card (V4 Phase 5).
* ``POST /v1/intake/unlink`` — ingest key: the extension's Unpair revokes its
  own key and disconnects its link.

All behind ``intake_device_enabled``; the browser extension also behind
``extension_enabled``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import DbSession, TenantPrincipal, get_hasher, require_permission
from app.common.cache import RateLimiter
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.clock import utc_now
from app.core.errors import AppError, ErrorCode, RateLimitedError
from app.db.session import system_session
from app.device_links import service
from app.device_links.auth import IngestPrincipal, require_ingest_key
from app.device_links.models import INTAKE_CAPTURE, DevicePairingCode
from app.device_links.service import DEVICE_PROVIDER
from app.integrations.models import IntegrationConnection
from app.tenants.roles import Permission

router = APIRouter(tags=["devices"])

_MANAGE = [Depends(require_permission(Permission.SETTINGS_MANAGE))]
_CLAIMS_PER_IP_PER_10_MIN = 10
IngestCapture = Annotated[IngestPrincipal, Depends(require_ingest_key(INTAKE_CAPTURE))]


class DeviceInfo(BaseModel):
    install_id: str = Field(min_length=8, max_length=128)
    platform: Literal["android", "extension"]
    app_version: str | None = Field(default=None, max_length=40)
    covered_packages: list[str] = Field(default_factory=list, max_length=20)
    name: str | None = Field(default=None, max_length=120)


class ClaimRequest(DeviceInfo):
    code: str = Field(pattern=r"^\d{6}$")


class KeyResponse(BaseModel):
    connection_id: str
    #: Shown once. Store it in the device's secure storage.
    ingest_key: str
    key_last4: str
    scope: str = INTAKE_CAPTURE


class PairingResponse(BaseModel):
    code: str
    expires_at: datetime


class HeartbeatRequest(BaseModel):
    app_version: str | None = Field(default=None, max_length=40)
    covered_packages: list[str] | None = Field(default=None, max_length=20)
    listener_state: Literal["ON", "OFF", "NO_PERMISSION", "UNKNOWN"] = "UNKNOWN"


class HeartbeatResponse(BaseModel):
    connection_id: str
    listener_state: str
    last_heartbeat_at: str
    #: V4 Phase 4: the seller turned Autopilot on with consent (and has not
    #: paused it), so order-like messages may be uploaded without a tap (L4).
    autopilot_on: bool = False


async def _require_flag(db: DbSession, tenant_id: object, platform: str = "android") -> None:
    flags = FeatureFlagService(db)
    if not await flags.is_enabled(
        FlagKey.INTAKE_DEVICE_ENABLED,
        tenant_id=tenant_id,  # type: ignore[arg-type]
    ):
        raise AppError("Linked devices are not enabled", code=ErrorCode.FEATURE_DISABLED)
    if platform == "extension" and not await flags.is_enabled(
        FlagKey.EXTENSION_ENABLED,
        tenant_id=tenant_id,  # type: ignore[arg-type]
    ):
        raise AppError("The browser extension is not enabled", code=ErrorCode.FEATURE_DISABLED)


def _key(issued: service.IssuedKey) -> KeyResponse:
    return KeyResponse(
        connection_id=str(issued.connection_id), ingest_key=issued.key, key_last4=issued.key_last4
    )


@router.post("/devices/link", response_model=KeyResponse, dependencies=_MANAGE)
async def link_device(
    payload: DeviceInfo, principal: TenantPrincipal, db: DbSession
) -> KeyResponse:
    tenant_id = principal.require_tenant()
    await _require_flag(db, tenant_id, payload.platform)
    async with system_session("device link: install uniqueness") as unscoped:
        issued = await service.link_device(
            db,
            tenant_id=tenant_id,
            user_id=principal.user_id,
            install_id=payload.install_id,
            platform=payload.platform,
            app_version=payload.app_version,
            covered_packages=payload.covered_packages,
            name=payload.name,
            other_shop_lookup=unscoped,
        )
    return _key(issued)


@router.post("/devices/pair", response_model=PairingResponse, dependencies=_MANAGE)
async def start_pairing(principal: TenantPrincipal, db: DbSession) -> PairingResponse:
    tenant_id = principal.require_tenant()
    await _require_flag(db, tenant_id)
    async with system_session("device pairing: code uniqueness") as unscoped:
        code, expires_at = await service.create_pairing_code(
            db, tenant_id=tenant_id, user_id=principal.user_id, unscoped=unscoped
        )
    return PairingResponse(code=code, expires_at=expires_at)


@router.post("/devices/pair/claim", response_model=KeyResponse)
async def claim_pairing(payload: ClaimRequest, request: Request) -> KeyResponse:
    ip = get_hasher().ip_hash(request.client.host if request.client else "unknown") or "unknown"
    limited = await RateLimiter().hit(
        "device-pair-claim", ip, limit=_CLAIMS_PER_IP_PER_10_MIN, window_seconds=600
    )
    if not limited.allowed:
        raise RateLimitedError(retry_after_seconds=limited.retry_after_seconds)
    async with system_session("device pairing: claim") as unscoped:
        pending = (
            await unscoped.execute(
                sa.select(DevicePairingCode.tenant_id).where(
                    DevicePairingCode.code_hash == service.hash_secret(payload.code),
                    DevicePairingCode.claimed_at.is_(None),
                    DevicePairingCode.expires_at > utc_now(),
                )
            )
        ).scalar_one_or_none()
        if pending is not None:
            await _require_flag(unscoped, pending, payload.platform)
        issued = await service.claim_pairing_code(
            unscoped,
            code=payload.code,
            install_id=payload.install_id,
            platform=payload.platform,
            app_version=payload.app_version,
            covered_packages=payload.covered_packages,
        )
    return _key(issued)


@router.post("/intake/heartbeat", response_model=HeartbeatResponse)
async def device_heartbeat(
    payload: HeartbeatRequest, device: IngestCapture, db: DbSession
) -> HeartbeatResponse:
    conn = await db.get(IntegrationConnection, device.connection_id)
    assert conn is not None  # noqa: S101 - resolved by require_ingest_key
    config = await service.heartbeat(
        db,
        conn,
        app_version=payload.app_version,
        covered_packages=payload.covered_packages,
        listener_state=payload.listener_state,
    )
    from app.autopilot.service import device_upload_allowed

    return HeartbeatResponse(
        connection_id=str(conn.id),
        listener_state=config["listener_state"],
        last_heartbeat_at=config["last_heartbeat_at"],
        autopilot_on=await device_upload_allowed(db, conn.tenant_id),
    )


__all__ = ["router"]


# ------------------------------------------------------------- extension ---


class LinkedExtension(BaseModel):
    connection_id: str
    name: str | None
    state: str
    app_version: str | None
    last_heartbeat_at: str | None
    linked_at: datetime


class ExtensionsResponse(BaseModel):
    extensions: list[LinkedExtension]


@router.get("/devices/extensions", response_model=ExtensionsResponse, dependencies=_MANAGE)
async def linked_extensions(principal: TenantPrincipal, db: DbSession) -> ExtensionsResponse:
    """The web card's list; health, revoke and the kill switch stay in the hub (L2)."""
    tenant_id = principal.require_tenant()
    await _require_flag(db, tenant_id, "extension")
    rows = (
        await db.scalars(
            sa.select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == DEVICE_PROVIDER,
                IntegrationConnection.state == "CONNECTED",
            )
            .order_by(IntegrationConnection.created_at.desc())
        )
    ).all()
    return ExtensionsResponse(
        extensions=[
            LinkedExtension(
                connection_id=str(row.id),
                name=row.account_name,
                state=row.state,
                app_version=(row.config or {}).get("app_version"),
                last_heartbeat_at=(row.config or {}).get("last_heartbeat_at"),
                linked_at=row.created_at,
            )
            for row in rows
            if (row.config or {}).get("platform") == "extension"
        ]
    )


class UnlinkResponse(BaseModel):
    connection_id: str
    state: str


@router.post("/intake/unlink", response_model=UnlinkResponse)
async def unlink_self(device: IngestCapture, db: DbSession) -> UnlinkResponse:
    """Unpair: the key's own link is disconnected and every key of it revoked."""
    from app.integrations import service as integrations

    conn = await db.get(IntegrationConnection, device.connection_id)
    assert conn is not None  # noqa: S101 - resolved by require_ingest_key
    await integrations.disconnect(db, conn, remote=False)
    await db.flush()
    return UnlinkResponse(connection_id=str(conn.id), state=conn.state)
