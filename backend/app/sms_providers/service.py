"""The platform SMS gateway account: credentials, Sender ID, switch (V4 WP7.1).

Credentials are checked against the provider's manifest (its
``credential_fields``), sealed with the credential vault and never returned.
A gateway sends only when **both** its flag (``alpha_sms_enabled``, default
off) and its account row are on, with credentials stored. Otherwise every
caller keeps the deployment's settings transport (disabled in production).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.errors import AppError, ErrorCode, ValidationError
from app.core.ids import new_id
from app.core.security import CredentialVault
from app.notifications.transport import SmsTransport
from app.sms_providers.alpha_sms import ALPHA_SMS_PROVIDER, AlphaSmsTransport
from app.sms_providers.manifest import SmsManifest, load_sms_manifests
from app.sms_providers.models import SmsProviderConnection

__all__ = [
    "PROVIDER_FLAGS",
    "SmsAccountUpdate",
    "SmsProviderService",
    "active_sms_transport",
    "connection_view",
    "sms_manifests",
]

_MAX_SECRET = 4096
#: Not documented by the provider beyond "approved Sender ID": letters,
#: digits and a few separators, kept short.
_SENDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,19}$")
#: Which flag gates each provider (default off, ``app.common.feature_flags``).
PROVIDER_FLAGS: dict[str, str] = {ALPHA_SMS_PROVIDER: "alpha_sms_enabled"}

_manifests: dict[str, SmsManifest] | None = None


def sms_manifests() -> dict[str, SmsManifest]:
    global _manifests
    if _manifests is None:
        _manifests = load_sms_manifests()
    return _manifests


def _context(row_id: Any) -> str:
    return f"sms_provider_connection:{row_id}"


def _mask(value: str) -> str:
    return "****" + value[-4:] if len(value) >= 8 else "****"


@dataclass(frozen=True, slots=True)
class SmsAccountUpdate:
    """One operator change. ``None`` keeps what is stored."""

    credentials: Mapping[str, str] | None = None
    sender_id: str | None = None
    enabled: bool = False
    reason: str | None = None


def connection_view(row: SmsProviderConnection | None) -> dict[str, Any] | None:
    """What the console sees. No secret, no envelope, no key version."""
    if row is None:
        return None
    return {
        "enabled": row.enabled,
        "has_credentials": row.has_credentials,
        "masked_identifier": row.masked_identifier,
        "sender_id": row.sender_id,
        "credentials_rotated_at": row.credentials_rotated_at,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


class SmsProviderService:
    def __init__(
        self,
        session: AsyncSession,
        vault: CredentialVault,
        manifests: Mapping[str, SmsManifest] | None = None,
    ) -> None:
        self._db = session
        self._vault = vault
        self._manifests = manifests if manifests is not None else sms_manifests()

    async def get(self, provider: str) -> SmsProviderConnection | None:
        row: SmsProviderConnection | None = await self._db.scalar(
            sa.select(SmsProviderConnection).where(SmsProviderConnection.provider == provider)
        )
        return row

    async def list(self) -> list[SmsProviderConnection]:
        rows = await self._db.scalars(
            sa.select(SmsProviderConnection).order_by(SmsProviderConnection.provider)
        )
        return list(rows.all())

    def manifest(self, provider: str) -> SmsManifest:
        manifest = self._manifests.get(provider)
        if manifest is None:
            raise AppError(
                "No verified contract for this SMS provider",
                code=ErrorCode.SMS_PROVIDER_UNKNOWN,
                details={"provider": provider},
            )
        return manifest

    async def update(
        self, provider: str, change: SmsAccountUpdate, *, actor: str
    ) -> SmsProviderConnection:
        """Connect, rotate or switch the gateway account, audited."""
        manifest = self.manifest(provider)
        credentials = self._checked_credentials(manifest, change.credentials)
        sender_id = change.sender_id.strip() if change.sender_id is not None else None
        if sender_id:
            if not manifest.supports("sender_id"):
                raise ValidationError(
                    "This provider's Sender ID is not verified", details={"field": "sender_id"}
                )
            if not _SENDER_ID.fullmatch(sender_id):
                raise ValidationError(
                    "sender_id must be 1-20 letters, digits, space, dot, dash or underscore",
                    details={"field": "sender_id"},
                )

        row = await self.get(provider)
        created = row is None
        if row is None and credentials is None:
            raise ValidationError(
                "Credentials are required to connect a provider",
                details={"fields": list(manifest.credential_fields)},
            )
        # Every refusal happens before the row changes.
        if change.enabled:
            missing = [] if manifest.supports("send") else ["SEND_NOT_VERIFIED"]
            if credentials is None and not (row is not None and row.has_credentials):
                missing.append("CREDENTIALS_MISSING")
            if missing:
                raise AppError(
                    "This SMS provider cannot be turned on yet",
                    code=ErrorCode.SMS_PROVIDER_NOT_READY,
                    details={"missing": missing},
                )

        if row is None:
            row = SmsProviderConnection(
                id=new_id(), provider=provider, created_by=actor, updated_by=actor
            )
            self._db.add(row)
        if credentials is not None:
            row.credentials_encrypted = self._vault.encrypt(
                json.dumps(credentials, sort_keys=True), context=_context(row.id)
            )
            row.masked_identifier = _mask(credentials[manifest.credential_fields[0]])
            row.key_version = self._vault.key_version
            row.credentials_rotated_at = utc_now()
        if sender_id is not None:
            # An empty string clears it: send without a Sender ID.
            row.sender_id = sender_id or None

        was_enabled = bool(row.enabled) and not created
        row.enabled = change.enabled
        row.updated_by = actor
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.ADMIN_SMS_PROVIDER_UPDATED,
            entity_type="sms_provider_connections",
            entity_id=row.id,
            reason=change.reason,
            context={
                "provider": provider,
                "created": created,
                "enabled": row.enabled,
                "was_enabled": was_enabled,
                # What changed, never a value (the redactor blanks credential keys).
                "changed": [
                    name
                    for name, changed in (
                        ("credentials", credentials is not None),
                        ("sender_id", sender_id is not None),
                    )
                    if changed
                ],
                "admin": actor,
            },
        )
        return row

    def _checked_credentials(
        self, manifest: SmsManifest, supplied: Mapping[str, str] | None
    ) -> dict[str, str] | None:
        if supplied is None:
            return None
        expected, given = set(manifest.credential_fields), set(supplied)
        if given != expected:
            raise ValidationError(
                "Credentials must be exactly the provider's fields",
                details={
                    "missing": sorted(expected - given),
                    "unexpected": sorted(given - expected),
                },
            )
        for name in manifest.credential_fields:
            value = supplied[name]
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_SECRET:
                raise ValidationError(
                    "Each credential is 1-4096 characters", details={"field": name}
                )
        return {name: supplied[name].strip() for name in manifest.credential_fields}

    def credentials(self, row: SmsProviderConnection) -> dict[str, str]:
        """Decrypted, for the transport only. Never logged or returned."""
        if not row.credentials_encrypted:
            return {}
        data = json.loads(self._vault.decrypt(row.credentials_encrypted, context=_context(row.id)))
        return {str(key): str(value) for key, value in data.items()}


async def active_sms_transport(
    db: AsyncSession, *, vault: CredentialVault | None = None
) -> SmsTransport | None:
    """The gateway that sends right now, or ``None`` (callers keep their default).

    Both gates: the provider's flag and its account row, with credentials.
    """
    from app.common.feature_flags import FeatureFlagService

    manifest = sms_manifests().get(ALPHA_SMS_PROVIDER)
    if manifest is None or not manifest.supports("send"):
        return None
    if not await FeatureFlagService(db).is_enabled(PROVIDER_FLAGS[ALPHA_SMS_PROVIDER]):
        return None
    if vault is None:
        from app.api.deps import get_vault

        vault = get_vault()
    service = SmsProviderService(db, vault)
    row = await service.get(ALPHA_SMS_PROVIDER)
    if row is None or not row.enabled or not row.has_credentials:
        return None
    api_key = service.credentials(row).get("api_key", "")
    if not api_key:
        return None
    return AlphaSmsTransport(api_key, sender_id=row.sender_id, base_url=manifest.base_url)
