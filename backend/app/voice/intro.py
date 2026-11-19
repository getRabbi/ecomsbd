"""Each shop's confirmation-call intro (V4 WP3.3).

The IVR opens with a short Bangla script that names the shop and offers the
keys (1 confirm, 2 cancel, 3 talk to the shop). It is recorded once per shop
name: by the TTS port when a provider exists (``TTS_PROVIDER_REQUIRED`` until
then), otherwise by the operator, who uploads a WAV or MP3 in the admin console.

Rules this module holds:

*   **A version belongs to the name it speaks.** Renaming the shop retires
    every version made for the old name, so a customer never hears the wrong
    shop; calls stop (the gate needs an ``ON`` intro) until a new one is on.
*   **The seller listens before calls play it.** Streaming the audio to a
    seller of the shop records the preview; turning a version on refuses one
    nobody has played (``VOICE_INTRO_NOT_PREVIEWED``).
*   **One version is on per shop**, enforced by a partial unique index.
*   **Bytes live in R2** (``voice/<tenant>/intro-v<n>.<ext>``). Where the voice
    provider's manifest verifies ``audio_upload``, the call worker hands the
    on version to the provider once and keeps the provider's id per provider.
"""

from __future__ import annotations

import hashlib
import io
import uuid
import wave
from dataclasses import dataclass
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.object_storage import ObjectStorage
from app.common.outbox import OutboxTopic, enqueue
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ConflictError, ErrorCode, NotFoundError
from app.core.logging import get_logger
from app.couriers.capabilities import CapabilityState
from app.tenants.models import Tenant
from app.voice.manifest import VoiceCapability, VoiceManifest
from app.voice.models import IntroSource, IntroState, VoiceIntroAudio
from app.voice.transport import (
    SegmentKind,
    VoiceAudio,
    VoiceAudioOutcome,
    VoiceSegment,
    VoiceTransport,
)
from app.voice.tts import TtsAvailability, TtsClient, TtsError, build_tts_client

__all__ = [
    "AMOUNT_PREFIX_TEXT",
    "AMOUNT_SUFFIX_TEXT",
    "INTRO_LANGUAGE",
    "MAX_AUDIO_BYTES",
    "MAX_INTRO_SECONDS",
    "AudioInfo",
    "IntroAudioService",
    "amount_prompts",
    "audio_storage",
    "call_audio",
    "ensure_intro_uploaded",
    "inspect_audio",
    "intro_script",
    "tts_client",
]

log = get_logger("app.voice.intro")

INTRO_LANGUAGE = "bn-BD"
MAX_AUDIO_BYTES = 2 * 1024 * 1024
#: The script is about 12 seconds; anything much longer costs call minutes.
MAX_INTRO_SECONDS = 60
#: The two platform recordings around a spoken amount (operator records them).
AMOUNT_PREFIX_TEXT = "আপনার অর্ডারের মূল্য"
AMOUNT_SUFFIX_TEXT = "টাকা।"


def intro_script(shop_name: str) -> str:
    """The spec's Bangla script for ``shop_name`` (white space folded)."""
    name = " ".join(shop_name.split())
    return (
        f"আসসালামু আলাইকুম। আপনি {name} থেকে একটি অর্ডার করেছেন। "
        "অর্ডার কনফার্ম করতে ১ চাপুন। বাতিল করতে ২ চাপুন। "
        "দোকানের সাথে কথা বলতে ৩ চাপুন।"
    )


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AudioInfo:
    content_type: str
    extension: str
    duration_ms: int | None


def _invalid(reason: str) -> AppError:
    return AppError(
        "This recording cannot be used",
        code=ErrorCode.VOICE_AUDIO_INVALID,
        details={"reason": reason},
    )


def inspect_audio(content: bytes) -> AudioInfo:
    """WAV or MP3, told apart by their bytes (never by a name or a header the
    client sent), at most 2 MiB; a WAV must be readable PCM of at most
    :data:`MAX_INTRO_SECONDS`. Raises ``VOICE_AUDIO_INVALID``."""
    if not content:
        raise _invalid("EMPTY")
    if len(content) > MAX_AUDIO_BYTES:
        raise _invalid("TOO_LARGE")
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(content), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
        except (wave.Error, EOFError, ValueError):
            raise _invalid("UNREADABLE_WAV") from None
        if rate <= 0 or frames <= 0:
            raise _invalid("EMPTY")
        duration_ms = frames * 1000 // rate
        if duration_ms > MAX_INTRO_SECONDS * 1000:
            raise _invalid("TOO_LONG")
        return AudioInfo("audio/wav", "wav", duration_ms)
    if content[:3] == b"ID3" or (
        len(content) > 1 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
    ):
        return AudioInfo("audio/mpeg", "mp3", None)
    raise _invalid("NOT_WAV_OR_MP3")


class AudioStore(Protocol):
    async def put(self, key: str, content: bytes, content_type: str = ...) -> None: ...

    async def get(self, key: str) -> bytes: ...


def audio_storage() -> AudioStore | None:
    """R2, or ``None`` when this deployment has none (tests replace this)."""
    settings = get_settings()
    return ObjectStorage(settings) if settings.r2_configured else None


def tts_client() -> TtsClient:
    """The deployment's TTS port (tests replace this)."""
    return build_tts_client(get_settings())


def _storage() -> AudioStore:
    store = audio_storage()
    if store is None:
        raise AppError(
            "Recording storage is not configured", code=ErrorCode.AUDIO_STORAGE_NOT_CONFIGURED
        )
    return store


# --------------------------------------------------------------------------- #
# What a call plays
# --------------------------------------------------------------------------- #


def amount_prompts(manifest: VoiceManifest | None, settings: Settings) -> tuple[str, str] | None:
    """The amount recordings, only where the provider verifies ``dynamic_audio``
    and the operator configured both."""
    if manifest is None or not manifest.supports(VoiceCapability.DYNAMIC_AUDIO):
        return None
    prefix = settings.voice_amount_prefix_audio_ref
    suffix = settings.voice_amount_suffix_audio_ref
    if prefix is None or suffix is None:
        return None
    return prefix, suffix


def call_audio(
    audio_ref: str, *, cod_paisa: int, prompts: tuple[str, str] | None
) -> dict[str, Any]:
    """``VoiceCall`` audio fields: the intro alone, or the intro followed by
    "আপনার অর্ডারের মূল্য <amount> টাকা।". The amount is spoken only as whole
    taka above zero; anything else is left to the SMS link, never rounded."""
    if prompts is None or cod_paisa <= 0 or cod_paisa % 100:
        return {"audio_ref": audio_ref}
    prefix, suffix = prompts
    return {
        "segments": (
            VoiceSegment(SegmentKind.AUDIO, audio_ref=audio_ref),
            VoiceSegment(SegmentKind.AUDIO, audio_ref=prefix),
            VoiceSegment(SegmentKind.NUMBER, number=cod_paisa // 100),
            VoiceSegment(SegmentKind.AUDIO, audio_ref=suffix),
        )
    }


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


def _name_key(shop_name: str) -> str:
    return hashlib.sha256(" ".join(shop_name.split()).encode()).hexdigest()[:16]


class IntroAudioService:
    """One shop's intro versions. Works in a tenant session or, for the admin
    console, a system session (``tenant_id`` is always explicit)."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def _tenant(self, tenant_id: uuid.UUID) -> Tenant:
        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is None or tenant.deleted_at is not None:
            raise NotFoundError("Shop not found")
        return tenant

    async def versions(self, tenant_id: uuid.UUID) -> list[VoiceIntroAudio]:
        return list(
            (
                await self._db.scalars(
                    sa.select(VoiceIntroAudio)
                    .where(VoiceIntroAudio.tenant_id == tenant_id)
                    .order_by(VoiceIntroAudio.version.desc())
                )
            ).all()
        )

    async def _get(self, tenant_id: uuid.UUID, version: int) -> VoiceIntroAudio:
        row = await self._db.scalar(
            sa.select(VoiceIntroAudio).where(
                VoiceIntroAudio.tenant_id == tenant_id, VoiceIntroAudio.version == version
            )
        )
        if row is None:
            raise NotFoundError("Recording not found")
        return row

    async def live(self, tenant_id: uuid.UUID) -> VoiceIntroAudio | None:
        return await self._db.scalar(
            sa.select(VoiceIntroAudio).where(
                VoiceIntroAudio.tenant_id == tenant_id,
                VoiceIntroAudio.state == str(IntroState.ON),
            )
        )

    # ------------------------------------------------------------ making ---

    async def add(
        self,
        tenant_id: uuid.UUID,
        content: bytes,
        *,
        source: IntroSource,
        created_by: str,
        shop_name: str | None = None,
    ) -> VoiceIntroAudio:
        """Store a new ``READY`` version for the shop's current name. An older
        ``READY`` one is retired (the newest is what the seller hears); the
        ``ON`` one keeps playing until the new one is turned on."""
        tenant = await self._tenant(tenant_id)
        name = shop_name if shop_name is not None else tenant.name
        if name != tenant.name:
            raise AppError("The shop has been renamed", code=ErrorCode.VOICE_INTRO_OUTDATED)
        info = inspect_audio(content)
        store = _storage()
        now = utc_now()
        latest = await self._db.scalar(
            sa.select(sa.func.max(VoiceIntroAudio.version)).where(
                VoiceIntroAudio.tenant_id == tenant_id
            )
        )
        version = int(latest or 0) + 1
        row = VoiceIntroAudio(
            tenant_id=tenant_id,
            version=version,
            state=str(IntroState.READY),
            source=str(source),
            shop_name=name,
            script=intro_script(name),
            storage_key=ObjectStorage.voice_intro_key(tenant_id, version, info.extension),
            content_type=info.content_type,
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            duration_ms=info.duration_ms,
            created_by=created_by[:120],
            provider_refs={},
        )
        try:
            # The version is claimed before the file is written, so two uploads
            # racing for one version never overwrite each other's file.
            async with self._db.begin_nested():
                self._db.add(row)
                await self._db.flush()
        except IntegrityError as exc:
            raise ConflictError("Another recording was saved at the same time; try again") from exc
        for older in await self.versions(tenant_id):
            if older.id != row.id and older.state == IntroState.READY:
                older.state = str(IntroState.RETIRED)
                older.retired_at = now
                older.retired_reason = "REPLACED"
        await store.put(row.storage_key, content, info.content_type)
        await self._db.flush()
        return row

    async def request(self, tenant_id: uuid.UUID, *, tts: TtsClient | None = None) -> bool:
        """Ask the TTS port for the current name's intro (outbox, once per name).

        ``False`` when one for this name already exists. Raises
        ``TTS_PROVIDER_REQUIRED`` while there is no TTS provider.
        """
        tenant = await self._tenant(tenant_id)
        client = tts or tts_client()
        if client.availability() is not TtsAvailability.AVAILABLE:
            raise AppError("No TTS provider", code=ErrorCode.TTS_PROVIDER_REQUIRED)
        if await self._has_version_for(tenant):
            return False
        await enqueue(
            self._db,
            OutboxTopic.VOICE_INTRO_REQUESTED,
            {"shop_name_key": _name_key(tenant.name)},
            tenant_id=tenant_id,
            dedupe_key=f"voice.intro_requested:{tenant_id}:{_name_key(tenant.name)}",
        )
        return True

    async def _has_version_for(self, tenant: Tenant) -> bool:
        found = await self._db.scalar(
            sa.select(VoiceIntroAudio.id).where(
                VoiceIntroAudio.tenant_id == tenant.id,
                VoiceIntroAudio.shop_name == tenant.name,
                VoiceIntroAudio.state != str(IntroState.RETIRED),
            )
        )
        return found is not None

    async def generate(
        self, tenant_id: uuid.UUID, shop_name_key: str, *, tts: TtsClient | None = None
    ) -> VoiceIntroAudio | None:
        """The ``voice.intro_requested`` handler's work. Nothing for a name the
        shop no longer has or one already recorded; a retryable TTS failure
        raises so the outbox tries again."""
        tenant = await self._tenant(tenant_id)
        if _name_key(tenant.name) != shop_name_key or await self._has_version_for(tenant):
            return None
        client = tts or tts_client()
        try:
            audio = await client.synthesize(intro_script(tenant.name), language=INTRO_LANGUAGE)
        except TtsError as exc:
            if exc.retryable:
                raise
            log.warning(
                "intro not generated",
                extra={"tenant_id": str(tenant_id), "provider": client.name, "code": exc.code},
            )
            return None
        row = await self.add(
            tenant_id, audio.content, source=IntroSource.TTS, created_by=f"tts:{client.name}"
        )
        await record_audit(
            self._db,
            AuditAction.VOICE_INTRO_CREATED,
            entity_type="voice_intro_audio",
            entity_id=row.id,
            tenant_id=tenant_id,
            context={"version": row.version, "source": row.source, "provider": client.name},
        )
        return row

    async def shop_renamed(self, tenant: Tenant) -> int:
        """Retire every version made for another name; request a new one when a
        TTS provider exists and the shop uses voice. Returns how many retired."""
        from app.common.feature_flags import FeatureFlagService, FlagKey

        now = utc_now()
        stale = [
            row
            for row in await self.versions(tenant.id)
            if row.state != IntroState.RETIRED and row.shop_name != tenant.name
        ]
        for row in stale:
            row.state = str(IntroState.RETIRED)
            row.retired_at = now
            row.retired_reason = "SHOP_RENAMED"
        if stale:
            await self._db.flush()
            enabled = await FeatureFlagService(self._db).is_enabled(
                FlagKey.VOICE_CONFIRMATION_ENABLED, tenant_id=tenant.id
            )
            if enabled and tts_client().availability() is TtsAvailability.AVAILABLE:
                await self.request(tenant.id)
        return len(stale)

    # ------------------------------------------------------------ seller ---

    async def play(
        self, tenant_id: uuid.UUID, version: int, *, user_id: uuid.UUID | None
    ) -> tuple[VoiceIntroAudio, bytes]:
        """The recording's bytes, for a seller of the shop. The first play is
        the preview that allows turning it on."""
        row = await self._get(tenant_id, version)
        if row.state == IntroState.RETIRED:
            raise NotFoundError("Recording not found")
        content = await _storage().get(row.storage_key)
        if row.previewed_at is None:
            row.previewed_at = utc_now()
            row.previewed_by = user_id
            await self._db.flush()
        return row, content

    async def turn_on(
        self, tenant_id: uuid.UUID, version: int, *, user_id: uuid.UUID | None
    ) -> VoiceIntroAudio:
        """Calls play this version from now on (the previous one is retired)."""
        row = await self._get(tenant_id, version)
        if row.state == IntroState.ON:
            return row
        if row.state == IntroState.RETIRED:
            raise NotFoundError("Recording not found")
        tenant = await self._tenant(tenant_id)
        if row.shop_name != tenant.name:
            raise AppError("The shop has been renamed", code=ErrorCode.VOICE_INTRO_OUTDATED)
        if row.previewed_at is None:
            raise AppError("Listen first", code=ErrorCode.VOICE_INTRO_NOT_PREVIEWED)
        now = utc_now()
        current = await self.live(tenant_id)
        if current is not None:
            current.state = str(IntroState.RETIRED)
            current.retired_at = now
            current.retired_reason = "REPLACED"
            await self._db.flush()  # the index allows one ON: retire first
        row.state = str(IntroState.ON)
        row.approved_at = now
        row.approved_by = user_id
        try:
            async with self._db.begin_nested():
                await self._db.flush()
        except IntegrityError as exc:
            raise ConflictError("Another recording was turned on at the same time") from exc
        await record_audit(
            self._db,
            AuditAction.VOICE_INTRO_TURNED_ON,
            entity_type="voice_intro_audio",
            entity_id=row.id,
            tenant_id=tenant_id,
            context={"version": row.version, "replaced": current.version if current else None},
        )
        return row

    async def turn_off(self, tenant_id: uuid.UUID) -> VoiceIntroAudio | None:
        """Calls stop; the version stays ``READY`` to turn on again."""
        row = await self.live(tenant_id)
        if row is None:
            return None
        row.state = str(IntroState.READY)
        await self._db.flush()
        await record_audit(
            self._db,
            AuditAction.VOICE_INTRO_TURNED_OFF,
            entity_type="voice_intro_audio",
            entity_id=row.id,
            tenant_id=tenant_id,
            context={"version": row.version},
        )
        return row

    # ------------------------------------------------------------- calls ---

    async def call_ref(
        self, tenant_id: uuid.UUID, *, provider: str, manifest: VoiceManifest | None
    ) -> str | None:
        """What a call plays for this shop on ``provider``; ``None`` = no call.

        The on version for the current name, as the provider's own id where
        recordings are uploaded first (``audio_upload``), else our storage key
        (the provider fetches it). Unknown delivery → ``None``.
        """
        row = await self.live(tenant_id)
        if row is None or manifest is None:
            return None
        tenant = await self._db.get(Tenant, tenant_id)
        if tenant is None or row.shop_name != tenant.name:
            return None
        delivery = manifest.state(VoiceCapability.AUDIO_UPLOAD)
        if delivery is CapabilityState.SUPPORTED:
            ref = (row.provider_refs or {}).get(provider)
            return str(ref) if ref else None
        if delivery is CapabilityState.UNSUPPORTED:
            return row.storage_key
        return None


async def ensure_intro_uploaded(
    tenant_id: uuid.UUID, transport: VoiceTransport, manifest: VoiceManifest | None
) -> bool:
    """Hand the shop's on intro to the provider once, where it needs uploads.

    Runs in the call worker, never inside a call's transaction: one session
    reads, the network calls run with no transaction open, a second session
    stores the provider's id (only if that version is still on). ``True`` when
    the provider now has it.
    """
    from app.db.session import session_scope

    if (
        manifest is None
        or manifest.state(VoiceCapability.AUDIO_UPLOAD) is not CapabilityState.SUPPORTED
    ):
        return False
    async with session_scope() as db:
        row = await IntroAudioService(db).live(tenant_id)
        if row is None:
            return False
        if (row.provider_refs or {}).get(transport.name):
            return True
        intro_id, key, content_type = row.id, row.storage_key, row.content_type
    store = audio_storage()
    if store is None:
        return False
    content = await store.get(key)
    result = await transport.upload_audio(VoiceAudio(key, content, content_type))
    if result.outcome is not VoiceAudioOutcome.UPLOADED or result.audio_ref is None:
        log.warning(
            "intro not uploaded to the voice provider",
            extra={
                "tenant_id": str(tenant_id),
                "provider": transport.name,
                "outcome": str(result.outcome),
                "detail": result.detail,
            },
        )
        return False
    async with session_scope() as db:
        row = await db.scalar(
            sa.select(VoiceIntroAudio).where(VoiceIntroAudio.id == intro_id).with_for_update()
        )
        if row is None or row.state != IntroState.ON:
            return False
        row.provider_refs = {**(row.provider_refs or {}), transport.name: result.audio_ref}
    return True
