"""The provider-neutral voice port (V4 WP3.1, rule L8).

Mirrors :mod:`app.notifications.transport`'s ``SmsTransport``: one Protocol, a
disabled implementation that says plainly nothing happened, a mock for tests,
and a builder that picks one from settings. A real provider adapter is written
only from an operator-supplied contract (``docs/providers/voice/<provider>/``);
until then the builder can only return the disabled or the mock transport.

Three facts the types encode rather than document:

*   **We retry, the provider does not.** ``VoiceCall.max_retries`` is fixed at
    0: a provider-side retry would ring a customer again outside our quiet-hour,
    per-day and credit checks.
*   **An ambiguous result is its own outcome.** ``UNKNOWN`` means the provider
    may have dialled (timeout, 5xx, crash mid-request). WP3.2 never re-calls on
    it; ``FAILED`` is reserved for "certainly not placed".
*   **No phone in a repr or a log.** ``VoiceCall`` prints its redacted form.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.redaction import mask_phone

__all__ = [
    "AUDIO_CONTENT_TYPES",
    "DEFAULT_DTMF_OPTIONS",
    "DisabledVoiceTransport",
    "DtmfAction",
    "DtmfOption",
    "MockVoiceTransport",
    "SegmentKind",
    "VoiceAudio",
    "VoiceAudioOutcome",
    "VoiceAudioResult",
    "VoiceAvailability",
    "VoiceAvailabilityState",
    "VoiceCall",
    "VoiceCallOutcome",
    "VoiceCallResult",
    "VoiceEvent",
    "VoiceEventKind",
    "VoiceSegment",
    "VoiceTransport",
    "build_voice_transport",
]

log = get_logger(__name__)

#: Shown on every "nothing was placed" answer until a contract exists.
CONTRACT_REQUIRED = "VOICE_PROVIDER_CONTRACT_REQUIRED"

_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
#: ``confirmation:<uuid>:<attempt>`` fits; nothing a provider could choke on.
_CLIENT_REF = re.compile(r"^[A-Za-z0-9:_.\-]{1,80}$")
_AUDIO_REF = re.compile(r"^[A-Za-z0-9:_./\-]{1,200}$")
_DTMF_DIGITS = frozenset("0123456789*#")


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


class VoiceAvailabilityState(StrEnum):
    AVAILABLE = "AVAILABLE"
    #: No provider is configured in this deployment.
    NOT_CONFIGURED = "NOT_CONFIGURED"
    #: Configured, but cannot take calls right now.
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class VoiceAvailability:
    state: VoiceAvailabilityState
    provider: str
    #: A machine code (``VOICE_PROVIDER_CONTRACT_REQUIRED``), never provider text.
    detail: str | None = None

    @property
    def can_place_calls(self) -> bool:
        return self.state is VoiceAvailabilityState.AVAILABLE


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #


class DtmfAction(StrEnum):
    """What a key press means. Digits are the script's (WP3.3), not fixed here."""

    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    CALLBACK = "CALLBACK"


@dataclass(frozen=True, slots=True)
class DtmfOption:
    digit: str
    action: DtmfAction

    def __post_init__(self) -> None:
        if len(self.digit) != 1 or self.digit not in _DTMF_DIGITS:
            raise ValueError("a DTMF option is one of 0-9, * or #")


#: WP3.3's script: 1 confirm, 2 cancel, 3 talk to the shop.
DEFAULT_DTMF_OPTIONS: tuple[DtmfOption, ...] = (
    DtmfOption("1", DtmfAction.CONFIRM),
    DtmfOption("2", DtmfAction.CANCEL),
    DtmfOption("3", DtmfAction.CALLBACK),
)


class SegmentKind(StrEnum):
    #: A pre-uploaded recording.
    AUDIO = "AUDIO"
    #: A whole number the provider speaks (the COD amount in taka). Only for a
    #: provider whose manifest verifies ``dynamic_audio``.
    NUMBER = "NUMBER"


@dataclass(frozen=True, slots=True)
class VoiceSegment:
    kind: SegmentKind
    audio_ref: str | None = None
    number: int | None = None

    def __post_init__(self) -> None:
        if self.kind is SegmentKind.AUDIO:
            if self.number is not None or not _valid_audio_ref(self.audio_ref):
                raise ValueError("an AUDIO segment carries one audio_ref and no number")
        else:
            number = self.number
            if (
                self.audio_ref is not None
                or not isinstance(number, int)
                or isinstance(number, bool)
                or number < 0
            ):
                raise ValueError("a NUMBER segment carries one non-negative integer")


@dataclass(frozen=True, slots=True, repr=False)
class VoiceCall:
    """One outbound confirmation call.

    Exactly one of ``audio_ref`` (one pre-uploaded recording) or ``segments``
    (recordings and spoken numbers, only with ``dynamic_audio``) is set.
    ``client_ref`` is our idempotency key, ``confirmation:{id}:{attempt}``.
    """

    to_e164: str
    client_ref: str
    dtmf_options: tuple[DtmfOption, ...] = DEFAULT_DTMF_OPTIONS
    audio_ref: str | None = None
    segments: tuple[VoiceSegment, ...] = ()
    max_retries: int = 0

    def __post_init__(self) -> None:
        # Messages never carry the number: they reach logs and Sentry.
        if not isinstance(self.to_e164, str) or not _E164.fullmatch(self.to_e164):
            raise ValueError("to_e164 must be an E.164 number (+ and 8-15 digits)")
        if not _CLIENT_REF.fullmatch(self.client_ref or ""):
            raise ValueError("client_ref must be 1-80 letters, digits or : _ . -")
        if self.max_retries != 0:
            raise ValueError("max_retries is always 0: ecomsbd retries, the provider does not")
        if (self.audio_ref is None) == (not self.segments):
            raise ValueError("set exactly one of audio_ref or segments")
        if self.audio_ref is not None and not _valid_audio_ref(self.audio_ref):
            raise ValueError("audio_ref must be 1-200 letters, digits or : _ . / -")
        if not self.dtmf_options:
            raise ValueError("a confirmation call needs DTMF options")
        digits = [option.digit for option in self.dtmf_options]
        if len(set(digits)) != len(digits):
            raise ValueError("each DTMF digit may mean one thing only")

    def redacted(self) -> dict[str, Any]:
        """What may be logged: the number masked, no audio contents."""
        return {
            "to": mask_phone(self.to_e164),
            "client_ref": self.client_ref,
            "audio": "segments" if self.segments else "recording",
            "segments": len(self.segments),
            "dtmf": [option.digit for option in self.dtmf_options],
        }

    def __repr__(self) -> str:
        return f"VoiceCall({self.redacted()!r})"


class VoiceCallOutcome(StrEnum):
    #: The provider accepted the call; what happened arrives by callback.
    PLACED = "PLACED"
    #: Refused for good (number, audio): no call happened, retrying is pointless.
    REJECTED = "REJECTED"
    #: Certainly not placed (connection refused, breaker open): safe to try later.
    FAILED = "FAILED"
    #: The provider may have dialled (timeout, 5xx). Never re-called automatically.
    UNKNOWN = "UNKNOWN"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    #: This ``client_ref`` was already placed; nothing new happened.
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class VoiceCallResult:
    outcome: VoiceCallOutcome
    provider: str
    provider_ref: str | None = None
    #: A machine code only; never the provider's response body.
    detail: str | None = None
    #: The provider refused our credentials: health goes to NEEDS_RECONNECT.
    auth_failure: bool = False

    @property
    def may_have_called(self) -> bool:
        """Whether the customer's phone may ring because of this request."""
        return self.outcome in (
            VoiceCallOutcome.PLACED,
            VoiceCallOutcome.UNKNOWN,
            VoiceCallOutcome.DUPLICATE,
        )


# --------------------------------------------------------------------------- #
# Recordings (WP3.3)
# --------------------------------------------------------------------------- #

#: What a recording handed to a provider may be.
AUDIO_CONTENT_TYPES = frozenset({"audio/wav", "audio/mpeg"})


@dataclass(frozen=True, slots=True, repr=False)
class VoiceAudio:
    """A recording for a provider whose manifest says recordings are uploaded
    before a call plays them (``audio_upload``)."""

    #: Our storage key (``voice/<tenant>/intro-v<n>.wav``): a stable name for it.
    key: str
    content: bytes
    content_type: str

    def __post_init__(self) -> None:
        if not _valid_audio_ref(self.key):
            raise ValueError("an audio key is 1-200 letters, digits or : _ . / -")
        if not self.content:
            raise ValueError("a recording has content")
        if self.content_type not in AUDIO_CONTENT_TYPES:
            raise ValueError("a recording is audio/wav or audio/mpeg")

    def __repr__(self) -> str:
        return f"VoiceAudio({self.key!r}, {self.content_type}, {len(self.content)} bytes)"


class VoiceAudioOutcome(StrEnum):
    UPLOADED = "UPLOADED"
    #: The provider refused the file (format, length): sending it again is pointless.
    REJECTED = "REJECTED"
    #: Not stored this time; trying later may work.
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class VoiceAudioResult:
    outcome: VoiceAudioOutcome
    provider: str
    #: The provider's id for the recording: what ``VoiceCall.audio_ref`` carries.
    audio_ref: str | None = None
    #: A machine code only; never the provider's response body.
    detail: str | None = None
    auth_failure: bool = False

    def __post_init__(self) -> None:
        if (self.outcome is VoiceAudioOutcome.UPLOADED) != _valid_audio_ref(self.audio_ref):
            raise ValueError("an uploaded recording, and only one, carries a valid audio_ref")


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #


class VoiceEventKind(StrEnum):
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    DTMF = "DTMF"
    COMPLETED = "COMPLETED"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    FAILED = "FAILED"


_TERMINAL_EVENTS = frozenset(
    {
        VoiceEventKind.COMPLETED,
        VoiceEventKind.NO_ANSWER,
        VoiceEventKind.BUSY,
        VoiceEventKind.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    """One provider callback event, already in our vocabulary.

    WP3.2 dedupes on ``(provider, provider_ref, kind)``.
    """

    kind: VoiceEventKind
    provider_ref: str
    client_ref: str | None = None
    #: Only on ``DTMF``: the key pressed.
    digit: str | None = None
    occurred_at: datetime | None = None
    duration_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.provider_ref:
            raise ValueError("a voice event names the provider's call")
        if (self.kind is VoiceEventKind.DTMF) != (self.digit is not None):
            raise ValueError("a digit comes with a DTMF event and only with one")
        if self.digit is not None and (len(self.digit) != 1 or self.digit not in _DTMF_DIGITS):
            raise ValueError("a DTMF digit is one of 0-9, * or #")
        if self.duration_seconds is not None and (
            not _is_int(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("duration_seconds is a non-negative integer")

    @property
    def is_terminal(self) -> bool:
        return self.kind in _TERMINAL_EVENTS


# --------------------------------------------------------------------------- #
# The port
# --------------------------------------------------------------------------- #


class VoiceTransport(Protocol):
    """Provider-neutral outbound calls with keypad answers."""

    name: str

    def availability(self) -> VoiceAvailability: ...

    async def place_call(self, call: VoiceCall) -> VoiceCallResult: ...

    async def upload_audio(self, audio: VoiceAudio) -> VoiceAudioResult:
        """Hand a recording to the provider before any call plays it. Called
        only where the manifest verifies ``audio_upload``; the result's
        ``audio_ref`` is what a ``VoiceCall`` then carries."""
        ...

    async def verify_callback(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Whether a callback is genuinely the provider's. Refuses everything
        when the callback secret is not configured."""
        ...

    def parse_callback(self, body: bytes) -> list[VoiceEvent]:
        """Events in a *verified* body. Raises ``ValueError`` on a malformed one."""
        ...


class DisabledVoiceTransport:
    """The default: places nothing and says so (``NOT_CONFIGURED``)."""

    name = "disabled"

    def availability(self) -> VoiceAvailability:
        return VoiceAvailability(
            VoiceAvailabilityState.NOT_CONFIGURED, self.name, detail=CONTRACT_REQUIRED
        )

    async def place_call(self, call: VoiceCall) -> VoiceCallResult:
        log.info("voice call not placed: no transport configured", extra=call.redacted())
        return VoiceCallResult(VoiceCallOutcome.NOT_CONFIGURED, self.name, detail=CONTRACT_REQUIRED)

    async def upload_audio(self, audio: VoiceAudio) -> VoiceAudioResult:
        return VoiceAudioResult(
            VoiceAudioOutcome.NOT_CONFIGURED, self.name, detail=CONTRACT_REQUIRED
        )

    async def verify_callback(self, headers: Mapping[str, str], body: bytes) -> bool:
        return False

    def parse_callback(self, body: bytes) -> list[VoiceEvent]:
        return []


MOCK_SIGNATURE_HEADER = "x-mock-voice-signature"
MOCK_CALLBACK_SECRET = "mock-voice-callback-secret"  # noqa: S105 - the mock's own, local/test only


@dataclass(slots=True)
class MockVoiceTransport:
    """Test double. Refused outside local/test by :class:`Settings`.

    Its callback scheme (HMAC-SHA256 hex of the body in
    ``X-Mock-Voice-Signature``) is the mock's own and claims nothing about any
    real provider.
    """

    callback_secret: str | None = MOCK_CALLBACK_SECRET
    available: bool = True
    name: str = "mock"
    #: Call references are unique per instance, as a real provider's are.
    ref_prefix: str = field(default_factory=lambda: f"mock-{uuid.uuid4().hex[:8]}")
    #: Every request that reached the "provider", duplicates excluded.
    calls: list[VoiceCall] = field(default_factory=list)
    #: Every recording handed over, repeats of a known key excluded.
    uploads: list[VoiceAudio] = field(default_factory=list)
    _script: deque[VoiceCallResult] = field(default_factory=deque)
    _placed: dict[str, str] = field(default_factory=dict)
    _audio_script: deque[VoiceAudioOutcome] = field(default_factory=deque)
    _audio: dict[str, str] = field(default_factory=dict)

    def script_audio(self, *outcomes: VoiceAudioOutcome) -> None:
        """The next ``upload_audio`` answers, in order; then ``UPLOADED`` again."""
        self._audio_script.extend(outcomes)

    async def upload_audio(self, audio: VoiceAudio) -> VoiceAudioResult:
        known = self._audio.get(audio.key)
        if known is not None:
            return VoiceAudioResult(VoiceAudioOutcome.UPLOADED, self.name, audio_ref=known)
        scripted = self._audio_script.popleft() if self._audio_script else None
        if scripted is not None and scripted is not VoiceAudioOutcome.UPLOADED:
            return VoiceAudioResult(scripted, self.name, detail=f"MOCK_{scripted}")
        self.uploads.append(audio)
        ref = f"{self.ref_prefix}-audio-{len(self.uploads)}"
        self._audio[audio.key] = ref
        return VoiceAudioResult(VoiceAudioOutcome.UPLOADED, self.name, audio_ref=ref)

    def script(self, *outcomes: VoiceCallOutcome, auth_failure: bool = False) -> None:
        """The next ``place_call`` answers, in order; then ``PLACED`` again."""
        for outcome in outcomes:
            self._script.append(
                VoiceCallResult(
                    outcome, self.name, detail=f"MOCK_{outcome}", auth_failure=auth_failure
                )
            )

    def availability(self) -> VoiceAvailability:
        if not self.available:
            return VoiceAvailability(
                VoiceAvailabilityState.UNAVAILABLE, self.name, detail="MOCK_UNAVAILABLE"
            )
        return VoiceAvailability(VoiceAvailabilityState.AVAILABLE, self.name)

    async def place_call(self, call: VoiceCall) -> VoiceCallResult:
        if not self.available:
            return VoiceCallResult(VoiceCallOutcome.FAILED, self.name, detail="MOCK_UNAVAILABLE")
        known = self._placed.get(call.client_ref)
        if known is not None:
            return VoiceCallResult(VoiceCallOutcome.DUPLICATE, self.name, provider_ref=known)
        self.calls.append(call)
        scripted = self._script.popleft() if self._script else None
        if scripted is not None and scripted.outcome not in (
            VoiceCallOutcome.PLACED,
            VoiceCallOutcome.UNKNOWN,
        ):
            return scripted
        # An UNKNOWN call did reach the mock provider: a repeat is a duplicate,
        # as it would be at a provider that honours idempotency keys.
        provider_ref = f"{self.ref_prefix}-call-{len(self.calls)}"
        self._placed[call.client_ref] = provider_ref
        if scripted is not None and scripted.outcome is VoiceCallOutcome.UNKNOWN:
            return scripted
        return VoiceCallResult(VoiceCallOutcome.PLACED, self.name, provider_ref=provider_ref)

    def provider_ref_for(self, client_ref: str) -> str | None:
        return self._placed.get(client_ref)

    def sign(self, body: bytes) -> str:
        if self.callback_secret is None:
            raise ValueError("the mock has no callback secret")
        return hmac.new(self.callback_secret.encode(), body, hashlib.sha256).hexdigest()

    def callback(
        self, *events: VoiceEvent, secret: str | None = None
    ) -> tuple[dict[str, str], bytes]:
        """A signed callback carrying ``events``, as the mock provider sends it."""
        body = json.dumps(
            {
                "events": [
                    {
                        "kind": str(event.kind),
                        "call_id": event.provider_ref,
                        "client_ref": event.client_ref,
                        "digit": event.digit,
                        "at": event.occurred_at.isoformat() if event.occurred_at else None,
                        "duration": event.duration_seconds,
                    }
                    for event in events
                ]
            },
            separators=(",", ":"),
        ).encode()
        key = secret if secret is not None else self.callback_secret
        signature = hmac.new((key or "").encode(), body, hashlib.sha256).hexdigest()
        return {MOCK_SIGNATURE_HEADER: signature}, body

    async def verify_callback(self, headers: Mapping[str, str], body: bytes) -> bool:
        if not self.callback_secret:
            return False
        lowered = {key.lower(): value for key, value in headers.items()}
        given = lowered.get(MOCK_SIGNATURE_HEADER)
        if not given:
            return False
        return hmac.compare_digest(given, self.sign(body))

    def parse_callback(self, body: bytes) -> list[VoiceEvent]:
        try:
            raw = json.loads(body)
            items = raw["events"]
            if not isinstance(items, list):
                raise TypeError
            return [
                VoiceEvent(
                    kind=VoiceEventKind(item["kind"]),
                    provider_ref=str(item["call_id"]),
                    client_ref=item.get("client_ref"),
                    digit=item.get("digit"),
                    occurred_at=datetime.fromisoformat(item["at"]) if item.get("at") else None,
                    duration_seconds=item.get("duration"),
                )
                for item in items
            ]
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError("malformed mock voice callback") from exc


def build_voice_transport(settings: Settings) -> VoiceTransport:
    """``VOICE_TRANSPORT``: ``disabled`` (default) or ``mock`` (local/test only).

    A real provider joins this match only with its adapter, written from an
    operator-supplied contract.
    """
    match settings.voice_transport:
        case "mock":
            return MockVoiceTransport()
        case _:
            return DisabledVoiceTransport()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_audio_ref(value: str | None) -> bool:
    return isinstance(value, str) and bool(_AUDIO_REF.fullmatch(value))
