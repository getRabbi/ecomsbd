"""The text-to-speech port for shop intro recordings (V4 WP3.3).

A shop's confirmation-call intro speaks its name, so it is made once per shop
name. A real TTS provider is added only from an operator-supplied contract; until
then the builder returns :class:`DisabledTtsClient` (``TTS_PROVIDER_REQUIRED``)
and the operator records the intro and uploads it in the admin console.

The port only turns text into audio bytes. Checking, storing and versioning the
result is :mod:`app.voice.intro`'s job, the same for a TTS file and an upload.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.core.config import Settings

__all__ = [
    "TTS_PROVIDER_REQUIRED",
    "DisabledTtsClient",
    "FakeTtsClient",
    "TtsAudio",
    "TtsAvailability",
    "TtsClient",
    "TtsError",
    "build_tts_client",
]

#: Shown while no TTS contract exists; the operator uploads a recording instead.
TTS_PROVIDER_REQUIRED = "TTS_PROVIDER_REQUIRED"


class TtsAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True, slots=True, repr=False)
class TtsAudio:
    content: bytes
    #: ``audio/wav`` or ``audio/mpeg``; :mod:`app.voice.intro` checks the bytes anyway.
    content_type: str

    def __repr__(self) -> str:
        return f"TtsAudio({self.content_type}, {len(self.content)} bytes)"


class TtsError(Exception):
    """The provider could not make the audio. ``code`` is a machine code, never
    provider text; ``retryable`` says whether trying later may help."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class TtsClient(Protocol):
    name: str

    def availability(self) -> TtsAvailability: ...

    async def synthesize(self, text: str, *, language: str) -> TtsAudio:
        """Speak ``text`` (``language`` is a BCP 47 tag, ``bn-BD``). Raises
        :class:`TtsError`."""
        ...


class DisabledTtsClient:
    name = "disabled"

    def availability(self) -> TtsAvailability:
        return TtsAvailability.NOT_CONFIGURED

    async def synthesize(self, text: str, *, language: str) -> TtsAudio:
        raise TtsError(TTS_PROVIDER_REQUIRED, retryable=False)


def silent_wav(*, milliseconds: int = 500, rate: int = 8000) -> bytes:
    """A mono 16-bit PCM WAV of silence: what the fake returns."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * (rate * milliseconds // 1000))
    return buffer.getvalue()


class FakeTtsClient:
    """Local/test double. Records what it was asked to say; can be told to fail."""

    name = "fake"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.available = True
        self.fail_with: TtsError | None = None
        self.audio: TtsAudio | None = None

    def availability(self) -> TtsAvailability:
        return TtsAvailability.AVAILABLE if self.available else TtsAvailability.NOT_CONFIGURED

    async def synthesize(self, text: str, *, language: str) -> TtsAudio:
        self.requests.append((text, language))
        if self.fail_with is not None:
            raise self.fail_with
        return self.audio or TtsAudio(silent_wav(), "audio/wav")


def build_tts_client(settings: Settings) -> TtsClient:
    """``TTS_PROVIDER``: ``disabled`` (default) or ``fake`` (local/test only)."""
    if settings.tts_provider == "fake":
        return FakeTtsClient()
    return DisabledTtsClient()
