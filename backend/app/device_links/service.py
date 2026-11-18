"""Linking phones and extensions to a shop (V4 WP1.1).

* A linked device is an ``integration_connections`` row, provider ``DEVICE``.
  Its ``account_key`` is the SHA-256 of the app's install id, and that column
  is unique per provider, so one install belongs to exactly one shop.
* It authenticates with an ingest key: ``edk_`` + 40 url-safe characters,
  stored as SHA-256 only, shown once. A connection has one live key; linking
  again issues a new one and revokes the old.
* A phone that is not signed in pairs with a 6-digit code the seller shows on
  the web: valid 10 minutes, claimable once.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.device_links.models import INTAKE_CAPTURE, DeviceIngestKey, DevicePairingCode
from app.integrations.models import IntegrationConnection

__all__ = [
    "DEVICE_PROVIDER",
    "KEY_PREFIX",
    "PAIRING_TTL",
    "PLATFORMS",
    "IssuedKey",
    "claim_pairing_code",
    "create_pairing_code",
    "hash_secret",
    "heartbeat",
    "link_device",
    "new_ingest_key",
    "revoke_connection_keys",
    "revoke_member_devices",
    "revoke_tenant_keys",
]

DEVICE_PROVIDER = "DEVICE"
KEY_PREFIX = "edk_"
PLATFORMS = frozenset({"android", "extension"})
PAIRING_TTL = timedelta(minutes=10)
LISTENER_STATES = frozenset({"ON", "OFF", "NO_PERMISSION", "UNKNOWN"})
_MAX_PACKAGES = 20


@dataclass(frozen=True, slots=True)
class IssuedKey:
    connection_id: uuid.UUID
    #: Shown to the device once and never stored.
    key: str
    key_last4: str


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_ingest_key() -> str:
    # 30 random bytes -> exactly 40 url-safe characters.
    return KEY_PREFIX + secrets.token_urlsafe(30)


def _install_key(install_id: str) -> str:
    cleaned = install_id.strip()
    if not 8 <= len(cleaned) <= 128:
        raise ValidationError(
            "install_id must be 8-128 characters", details={"field": "install_id"}
        )
    return "install:" + hash_secret(cleaned)


def _clean_config(
    *,
    platform: str,
    app_version: str | None,
    covered_packages: list[str] | None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if platform not in PLATFORMS:
        raise ValidationError("Unknown platform", details={"field": "platform"})
    packages = [
        p.strip()[:120] for p in (covered_packages or []) if isinstance(p, str) and p.strip()
    ][:_MAX_PACKAGES]
    config = dict(existing or {})
    config.update(
        {
            "platform": platform,
            "app_version": (app_version or "")[:40] or None,
            "covered_packages": packages,
        }
    )
    config.setdefault("last_heartbeat_at", None)
    config.setdefault("listener_state", "UNKNOWN")
    return config


async def _issue_key(
    db: AsyncSession, conn: IntegrationConnection, *, created_by: uuid.UUID
) -> IssuedKey:
    await revoke_connection_keys(db, conn.id)
    key = new_ingest_key()
    db.add(
        DeviceIngestKey(
            tenant_id=conn.tenant_id,
            connection_id=conn.id,
            key_hash=hash_secret(key),
            key_last4=key[-4:],
            scope=INTAKE_CAPTURE,
            created_by=created_by,
        )
    )
    await db.flush()
    return IssuedKey(connection_id=conn.id, key=key, key_last4=key[-4:])


async def _connection_for_install(
    db: AsyncSession, account_key: str
) -> IntegrationConnection | None:
    return (
        await db.execute(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.provider == DEVICE_PROVIDER,
                IntegrationConnection.account_key == account_key,
            )
        )
    ).scalar_one_or_none()


async def link_device(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    install_id: str,
    platform: str,
    app_version: str | None = None,
    covered_packages: list[str] | None = None,
    name: str | None = None,
    other_shop_lookup: AsyncSession | None = None,
) -> IssuedKey:
    """Link (or re-link) an install to this shop and issue a fresh key.

    ``other_shop_lookup`` is an unscoped session used only to see whether the
    install is already linked to a different shop (the unique key is global).
    """
    account_key = _install_key(install_id)
    config = _clean_config(
        platform=platform, app_version=app_version, covered_packages=covered_packages
    )
    if other_shop_lookup is not None:
        elsewhere = await _connection_for_install(other_shop_lookup, account_key)
        if elsewhere is not None and elsewhere.tenant_id != tenant_id:
            raise ConflictError(
                "This device is linked to another shop. Unlink it there first.",
                details={"reason": "LINKED_ELSEWHERE"},
            )
    conn = await _connection_for_install(db, account_key)
    if conn is None:
        conn = IntegrationConnection(
            tenant_id=tenant_id,
            provider=DEVICE_PROVIDER,
            name=(name or f"{platform.capitalize()} device")[:120],
            state="CONNECTED",
            account_key=account_key,
            config=config,
            created_by=user_id,
        )
        db.add(conn)
        await db.flush()
    else:
        conn.state = "CONNECTED"
        conn.config = _clean_config(
            platform=platform,
            app_version=app_version,
            covered_packages=covered_packages,
            existing=conn.config,
        )
        conn.created_by = user_id
    issued = await _issue_key(db, conn, created_by=user_id)
    await record_audit(
        db,
        AuditAction.DEVICE_LINKED,
        entity_type="integration_connection",
        entity_id=conn.id,
        context={"platform": platform, "key_last4": issued.key_last4},
        tenant_id=tenant_id,
    )
    return issued


# --------------------------------------------------------------- pairing ---


async def create_pairing_code(
    db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, unscoped: AsyncSession
) -> tuple[str, Any]:
    """A fresh 6-digit code, unique among live codes of every shop."""
    now = utc_now()
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        taken = await unscoped.scalar(
            sa.select(sa.func.count())
            .select_from(DevicePairingCode)
            .where(
                DevicePairingCode.code_hash == hash_secret(code),
                DevicePairingCode.claimed_at.is_(None),
                DevicePairingCode.expires_at > now,
            )
        )
        if not taken:
            break
    else:  # pragma: no cover - a million codes, a handful live
        raise ConflictError("Could not create a pairing code, try again")
    expires_at = now + PAIRING_TTL
    db.add(
        DevicePairingCode(
            tenant_id=tenant_id,
            code_hash=hash_secret(code),
            created_by=user_id,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return code, expires_at


async def claim_pairing_code(
    unscoped: AsyncSession,
    *,
    code: str,
    install_id: str,
    platform: str,
    app_version: str | None = None,
    covered_packages: list[str] | None = None,
) -> IssuedKey:
    """Exchange a live code for a link and a key. One use; atomic."""
    if not (len(code) == 6 and code.isdigit()):
        raise NotFoundError("That code is not valid")
    now = utc_now()
    row = (
        await unscoped.execute(
            sa.select(DevicePairingCode).where(
                DevicePairingCode.code_hash == hash_secret(code),
                DevicePairingCode.claimed_at.is_(None),
                DevicePairingCode.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("That code is not valid or has expired")
    claimed = await unscoped.execute(
        sa.update(DevicePairingCode)
        .where(DevicePairingCode.id == row.id, DevicePairingCode.claimed_at.is_(None))
        .values(claimed_at=now)
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        raise NotFoundError("That code is not valid or has expired")
    issued = await link_device(
        unscoped,
        tenant_id=row.tenant_id,
        user_id=row.created_by,
        install_id=install_id,
        platform=platform,
        app_version=app_version,
        covered_packages=covered_packages,
        other_shop_lookup=unscoped,
    )
    await unscoped.execute(
        sa.update(DevicePairingCode)
        .where(DevicePairingCode.id == row.id)
        .values(connection_id=issued.connection_id)
    )
    return issued


# ------------------------------------------------------------ revocation ---


async def revoke_connection_keys(db: AsyncSession, connection_id: uuid.UUID) -> int:
    result = await db.execute(
        sa.update(DeviceIngestKey)
        .where(DeviceIngestKey.connection_id == connection_id, DeviceIngestKey.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def revoke_tenant_keys(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await db.execute(
        sa.update(DeviceIngestKey)
        .where(DeviceIngestKey.tenant_id == tenant_id, DeviceIngestKey.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def revoke_member_devices(
    db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """A removed member's linked devices stop capturing for the shop."""
    from app.integrations import service as integrations

    connections = (
        await db.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == tenant_id,
                IntegrationConnection.provider == DEVICE_PROVIDER,
                IntegrationConnection.created_by == user_id,
                IntegrationConnection.state != "DISCONNECTED",
            )
        )
    ).all()
    for conn in connections:
        await integrations.disconnect(db, conn, remote=False)
    return len(connections)


# ------------------------------------------------------------- heartbeat ---


async def heartbeat(
    db: AsyncSession,
    conn: IntegrationConnection,
    *,
    app_version: str | None,
    covered_packages: list[str] | None,
    listener_state: str,
) -> dict[str, Any]:
    if listener_state not in LISTENER_STATES:
        raise ValidationError("Unknown listener state", details={"field": "listener_state"})
    now = utc_now()
    config = _clean_config(
        platform=str(conn.config.get("platform") or "android"),
        app_version=app_version or conn.config.get("app_version"),
        covered_packages=covered_packages
        if covered_packages is not None
        else conn.config.get("covered_packages"),
        existing=conn.config,
    )
    config["last_heartbeat_at"] = now.isoformat()
    config["listener_state"] = listener_state
    conn.config = config
    conn.last_success_at = now
    await db.flush()
    return config
