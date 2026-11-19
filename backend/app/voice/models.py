"""The platform's voice provider accounts (V4 WP3.1).

Platform-level, not tenant-owned: ecomsbd places confirmation calls on its own
provider account and meters them per shop as credits (WP3.4). A seller never
sees or supplies these credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = [
    "INTRO_ON_PREDICATE",
    "CallbackState",
    "IntroSource",
    "IntroState",
    "VoiceCallEvent",
    "VoiceCallbackDelivery",
    "VoiceEventResult",
    "VoiceIntroAudio",
    "VoiceProviderConnection",
]


class VoiceProviderConnection(Base, PrimaryKeyMixin, TimestampMixin):
    """One provider account. One row per provider."""

    __tablename__ = "voice_provider_connections"
    __table_args__ = (
        sa.UniqueConstraint("provider", name="uq_voice_provider_connections_provider"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Off until an operator turns it on; turning on needs a ready manifest,
    #: credentials and a callback secret.
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    #: One vault envelope over a JSON object of the manifest's
    #: ``credential_fields``. Context ``voice_provider_connection:<id>``.
    credentials_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Verifies the provider's callbacks. Unset → every callback is refused.
    callback_secret_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    key_version: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    #: ``****abcd`` of the first credential field; the only credential-derived
    #: value that ever leaves the server.
    masked_identifier: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    #: The platform's own outgoing number (E.164), only where the manifest
    #: verifies ``caller_id``. Not a customer number.
    caller_id: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    #: Non-secret provider settings (region, application id).
    config: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)

    created_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    updated_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    credentials_rotated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials_encrypted)

    @property
    def has_callback_secret(self) -> bool:
        return bool(self.callback_secret_encrypted)


class CallbackState(StrEnum):
    """``voice_callback_deliveries.state``."""

    RECEIVED = "RECEIVED"
    #: Signature missing or wrong, or no verifier for this provider.
    REJECTED = "REJECTED"
    #: Verified, but the body could not be read as events.
    MALFORMED = "MALFORMED"
    PROCESSED = "PROCESSED"


class VoiceCallbackDelivery(Base, PrimaryKeyMixin):
    """One inbound voice callback, stored before anything is decided (WP3.2).

    Platform-level like the provider account: a callback arrives before we know
    whose call it is. A provider retrying the same body bumps
    ``delivery_count`` instead of adding a row.
    """

    __tablename__ = "voice_callback_deliveries"
    __table_args__ = (
        sa.UniqueConstraint("provider", "body_sha256", name="uq_voice_callback_deliveries_body"),
        sa.Index("ix_voice_callback_deliveries_state", "provider", "state", "received_at"),
        sa.CheckConstraint("delivery_count > 0", name="delivery_count_positive"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=CallbackState.RECEIVED
    )
    #: The body exactly as received (decoded as UTF-8 with replacement).
    raw_body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Header names only, sorted; never values (a signature is a secret).
    header_names: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: A machine code, never provider text.
    reason: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    event_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    delivery_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class VoiceEventResult(StrEnum):
    """What one callback event did."""

    #: It moved the confirmation (an answer, a no-answer).
    APPLIED = "APPLIED"
    #: Progress only (ringing, answered); recorded on the attempt.
    RECORDED = "RECORDED"
    #: The call had already ended or been handed to the seller: recorded, ignored.
    LATE = "LATE"
    #: A key the script does not offer.
    IGNORED = "IGNORED"
    #: No call of ours matches yet (the callback beat our own record of the
    #: provider's reference); applied when the reference is stored.
    UNMATCHED = "UNMATCHED"


class VoiceCallEvent(Base, PrimaryKeyMixin, TimestampMixin):
    """One provider call event, deduplicated on ``(provider, provider_ref, event_key)``.

    ``event_key`` is the kind, or ``DTMF:<digit>`` for a key press, so a
    second, different key on one call is its own event (the first may be a
    key the script does not offer). Platform-level (an unmatched event has no
    shop yet); ``tenant_id`` and ``attempt_id`` are filled in once the call is
    found.
    """

    __tablename__ = "voice_call_events"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider", "provider_ref", "event_key", name="uq_voice_call_events_event"
        ),
        sa.Index("ix_voice_call_events_attempt", "attempt_id"),
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    provider_ref: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    event_key: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    digit: Mapped[str | None] = mapped_column(sa.String(1), nullable=True)
    client_ref: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    result: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)


class IntroState(StrEnum):
    """``voice_intro_audio.state``."""

    #: Stored; the seller may listen to it and turn calls on with it.
    READY = "READY"
    #: The recording calls play. At most one per shop.
    ON = "ON"
    #: Replaced, or made for a shop name the shop no longer has.
    RETIRED = "RETIRED"


class IntroSource(StrEnum):
    #: Made by the TTS port (``TTS_PROVIDER_REQUIRED`` until a contract exists).
    TTS = "TTS"
    #: Recorded by the operator and uploaded in the admin console.
    UPLOAD = "UPLOAD"


#: One recording in use per shop. Partial unique index on PostgreSQL and SQLite.
INTRO_ON_PREDICATE = "state = 'ON'"


class VoiceIntroAudio(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    """One version of a shop's confirmation-call intro (V4 WP3.3).

    The recording speaks the shop's name, so a version belongs to the name it
    was made for and a rename retires it. The bytes live in R2
    (``voice/<tenant>/intro-v<n>.<ext>``); a seller of the shop must play them
    back before the version can be turned on.
    """

    __tablename__ = "voice_intro_audio"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "version", name="uq_voice_intro_audio_version"),
        sa.Index(
            "uq_voice_intro_audio_on",
            "tenant_id",
            unique=True,
            postgresql_where=sa.text(INTRO_ON_PREDICATE),
            sqlite_where=sa.text(INTRO_ON_PREDICATE),
        ),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.CheckConstraint("byte_size > 0", name="byte_size_positive"),
    )

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    state: Mapped[str] = mapped_column(sa.String(12), nullable=False, default=IntroState.READY)
    source: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    #: The shop name the recording speaks (the script's only variable part).
    shop_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    #: The Bangla script it was made from (the TTS input; what the operator reads).
    script: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    byte_size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: WAV only; an MP3's length is not measured.
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    #: Who made it: an admin label, or ``tts:<provider>``.
    created_by: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: The first time a seller of the shop played it back (needed to turn it on).
    previewed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    previewed_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: ``REPLACED`` or ``SHOP_RENAMED``.
    retired_reason: Mapped[str | None] = mapped_column(sa.String(24), nullable=True)
    #: The provider's own id for the uploaded recording, per provider, where its
    #: manifest says recordings are uploaded before a call.
    provider_refs: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
