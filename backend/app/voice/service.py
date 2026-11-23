"""The platform voice provider account: credentials, switch and health (V4 WP3.1).

Credentials are validated against the provider's manifest (its
``credential_fields``), encrypted with the credential vault and never returned.
Health and the circuit breaker are the shared :mod:`app.common.provider_health`
rows (kind ``VOICE``, platform scope): a provider account belongs to no shop.
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
from app.common.provider_health import ProviderHealthService, ProviderKind
from app.core.clock import utc_now
from app.core.errors import AppError, ErrorCode, ValidationError
from app.core.ids import new_id
from app.core.security import CredentialVault
from app.voice.manifest import VoiceCapability, VoiceManifest
from app.voice.models import VoiceProviderConnection
from app.voice.transport import VoiceCallOutcome, VoiceCallResult

__all__ = [
    "CALL_CAPABILITY",
    "VoiceConnectionService",
    "VoiceConnectionUpdate",
    "connection_view",
]

#: Health is tracked for placing calls; callbacks are recorded by WP3.2.
CALL_CAPABILITY = "place_call"

_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_MAX_SECRET = 4096


def _context(row_id: Any) -> str:
    return f"voice_provider_connection:{row_id}"


def _mask(value: str) -> str:
    return "****" + value[-4:] if len(value) >= 8 else "****"


@dataclass(frozen=True, slots=True)
class VoiceConnectionUpdate:
    """One operator change. ``None`` keeps what is stored."""

    credentials: Mapping[str, str] | None = None
    callback_secret: str | None = None
    caller_id: str | None = None
    enabled: bool = False
    reason: str | None = None


def connection_view(row: VoiceProviderConnection | None) -> dict[str, Any] | None:
    """What the console sees. No secret, no envelope, no key version."""
    if row is None:
        return None
    return {
        "enabled": row.enabled,
        "has_credentials": row.has_credentials,
        "has_callback_secret": row.has_callback_secret,
        "masked_identifier": row.masked_identifier,
        "caller_id": row.caller_id,
        "credentials_rotated_at": row.credentials_rotated_at,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


class VoiceConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        vault: CredentialVault,
        manifests: Mapping[str, VoiceManifest],
    ) -> None:
        self._db = session
        self._vault = vault
        self._manifests = manifests

    async def get(self, provider: str) -> VoiceProviderConnection | None:
        return (
            await self._db.execute(
                sa.select(VoiceProviderConnection).where(
                    VoiceProviderConnection.provider == provider
                )
            )
        ).scalar_one_or_none()

    async def list(self) -> list[VoiceProviderConnection]:
        return list(
            (
                await self._db.execute(
                    sa.select(VoiceProviderConnection).order_by(VoiceProviderConnection.provider)
                )
            )
            .scalars()
            .all()
        )

    def manifest(self, provider: str) -> VoiceManifest:
        manifest = self._manifests.get(provider)
        if manifest is None:
            raise AppError(
                "No verified contract for this voice provider",
                code=ErrorCode.VOICE_PROVIDER_UNKNOWN,
                details={"provider": provider},
            )
        return manifest

    async def update(
        self, provider: str, change: VoiceConnectionUpdate, *, actor: str
    ) -> VoiceProviderConnection:
        """Connect, rotate or switch the provider account, audited."""
        manifest = self.manifest(provider)
        credentials = self._checked_credentials(manifest, change.credentials)
        secret = change.callback_secret
        if secret is not None and not (16 <= len(secret) <= _MAX_SECRET):
            raise ValidationError(
                "callback_secret must be 16-4096 characters",
                details={"field": "callback_secret"},
            )
        caller_id = change.caller_id
        if caller_id is not None:
            if not manifest.supports(VoiceCapability.CALLER_ID):
                raise ValidationError(
                    "This provider's caller id choice is not verified",
                    details={"field": "caller_id"},
                )
            if not _E164.fullmatch(caller_id):
                raise ValidationError(
                    "caller_id must be an E.164 number", details={"field": "caller_id"}
                )

        row = await self.get(provider)
        created = row is None
        if row is None and credentials is None:
            raise ValidationError(
                "Credentials are required to connect a provider",
                details={"fields": list(manifest.credential_fields)},
            )
        # Every refusal happens before the row changes, so a refused request
        # leaves nothing half-written in the session.
        if change.enabled:
            missing = list(manifest.missing_for_calls)
            if credentials is None and not (row is not None and row.has_credentials):
                missing.append("CREDENTIALS_MISSING")
            if secret is None and not (row is not None and row.has_callback_secret):
                # The callback verifier refuses everything without it.
                missing.append("CALLBACK_SECRET_MISSING")
            if missing:
                raise AppError(
                    "This voice provider cannot be turned on yet",
                    code=ErrorCode.VOICE_PROVIDER_NOT_READY,
                    details={"missing": missing},
                )

        if row is None:
            row = VoiceProviderConnection(
                id=new_id(), provider=provider, created_by=actor, updated_by=actor, config={}
            )
            self._db.add(row)

        if credentials is not None:
            row.credentials_encrypted = self._vault.encrypt(
                json.dumps(credentials, sort_keys=True), context=_context(row.id)
            )
            row.masked_identifier = _mask(credentials[manifest.credential_fields[0]])
            row.key_version = self._vault.key_version
            row.credentials_rotated_at = utc_now()
        if secret is not None:
            row.callback_secret_encrypted = self._vault.encrypt(secret, context=_context(row.id))
            row.key_version = self._vault.key_version
        if caller_id is not None:
            row.caller_id = caller_id

        was_enabled = bool(row.enabled) and not created
        row.enabled = change.enabled
        row.updated_by = actor
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.ADMIN_VOICE_PROVIDER_UPDATED,
            entity_type="voice_provider_connections",
            entity_id=row.id,
            reason=change.reason,
            context={
                "provider": provider,
                "created": created,
                "enabled": row.enabled,
                "was_enabled": was_enabled,
                # What changed, never a value. One list because the audit
                # redactor blanks any key naming a credential or secret.
                "changed": [
                    name
                    for name, changed in (
                        ("credentials", credentials is not None),
                        ("callback_secret", secret is not None),
                        ("caller_id", caller_id is not None),
                    )
                    if changed
                ],
                "admin": actor,
            },
        )
        return row

    def _checked_credentials(
        self, manifest: VoiceManifest, supplied: Mapping[str, str] | None
    ) -> dict[str, str] | None:
        if supplied is None:
            return None
        expected = set(manifest.credential_fields)
        given = set(supplied)
        if not expected:
            raise AppError(
                "The provider's credential fields are not declared",
                code=ErrorCode.VOICE_PROVIDER_NOT_READY,
                details={"missing": ["CREDENTIAL_FIELDS_UNDECLARED"]},
            )
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
        return {name: supplied[name] for name in manifest.credential_fields}

    # ---------------------------------------------------------- secrets ----

    def credentials(self, row: VoiceProviderConnection) -> dict[str, str]:
        """Decrypted, for the adapter only. Never logged or returned."""
        if not row.credentials_encrypted:
            return {}
        data = json.loads(self._vault.decrypt(row.credentials_encrypted, context=_context(row.id)))
        return {str(key): str(value) for key, value in data.items()}

    def callback_secret(self, row: VoiceProviderConnection) -> str | None:
        if not row.callback_secret_encrypted:
            return None
        return self._vault.decrypt(row.callback_secret_encrypted, context=_context(row.id))

    # ----------------------------------------------------------- health ----

    async def breaker_allows(self, provider: str) -> bool:
        return await ProviderHealthService(self._db).allows(provider, capability=CALL_CAPABILITY)

    async def record_result(
        self, provider: str, result: VoiceCallResult, *, latency_ms: int | None = None
    ) -> None:
        """Feed one place-call answer into the shared health row.

        A per-call refusal (a bad number) says nothing about the provider and is
        not counted; a refused credential is, straight to NEEDS_RECONNECT.
        """
        health = ProviderHealthService(self._db)
        outcome = result.outcome
        if outcome is VoiceCallOutcome.PLACED:
            await health.record_success(
                ProviderKind.VOICE, provider, capability=CALL_CAPABILITY, latency_ms=latency_ms
            )
        elif outcome in (VoiceCallOutcome.FAILED, VoiceCallOutcome.UNKNOWN) or (
            outcome is VoiceCallOutcome.REJECTED and result.auth_failure
        ):
            await health.record_failure(
                ProviderKind.VOICE,
                provider,
                capability=CALL_CAPABILITY,
                error_code=result.detail or str(outcome),
                is_auth_failure=result.auth_failure,
                latency_ms=latency_ms,
            )
