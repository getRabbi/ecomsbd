"""Voice provider capability manifests (V4 WP3.1).

The courier manifest's rule, applied to voice: **a capability is ``true`` only
with a dated source**. Each entry in ``app/voice/manifests/<provider>.yaml``
carries its own ``verified_at`` and ``evidence`` (a section of the operator's
``docs/providers/voice/<provider>/CONTRACT.md``, or a recorded live call). An
entry missing either reads as ``unknown``, which is never usable.

Numeric facts (``max_call_seconds``, the price) follow the same rule and are
whole numbers only: a price is integer paisa, never a float.

No provider manifest ships until a contract does. The mock transport's
manifest lives in code (:func:`mock_manifest`) and is offered only while
``VOICE_TRANSPORT=mock``, which the settings refuse outside local/test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.core.logging import get_logger
from app.couriers.capabilities import CapabilityState

__all__ = [
    "VOICE_MANIFEST_DIR",
    "CapabilityClaim",
    "Evidence",
    "FactClaim",
    "VoiceCapability",
    "VoiceFact",
    "VoiceManifest",
    "available_voice_manifests",
    "load_voice_manifest",
    "load_voice_manifests",
    "mock_manifest",
    "parse_voice_manifest",
]

log = get_logger(__name__)

#: Inside the ``app`` package so the production image carries it (see
#: :data:`app.couriers.capabilities.MANIFEST_DIR`).
VOICE_MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"

_CREDENTIAL_FIELD = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class VoiceCapability(StrEnum):
    #: The provider reports the key the customer pressed.
    DTMF_CAPTURE = "dtmf_capture"
    #: Recordings and spoken numbers can be chained per call (the amount).
    DYNAMIC_AUDIO = "dynamic_audio"
    #: Callbacks carry a verifiable signature. ``detail`` names the scheme.
    CALLBACK_SIGNATURE = "callback_signature"
    #: The number the customer sees can be chosen.
    CALLER_ID = "caller_id"
    #: WP3.3: ``true`` = a recording is uploaded to the provider before a call
    #: plays it (``VoiceTransport.upload_audio``); ``false`` = the provider
    #: fetches our stored file by link. Unknown = we cannot get the intro to it.
    AUDIO_UPLOAD = "audio_upload"


class VoiceFact(StrEnum):
    MAX_CALL_SECONDS = "max_call_seconds"
    PRICE_PER_CALL_PAISA = "price_per_call_paisa"
    PRICE_PER_MINUTE_PAISA = "price_per_minute_paisa"


_PRICES = (VoiceFact.PRICE_PER_CALL_PAISA, VoiceFact.PRICE_PER_MINUTE_PAISA)


@dataclass(frozen=True, slots=True)
class Evidence:
    verified_at: date
    #: Where the claim comes from, e.g. ``CONTRACT.md §4 Callbacks``.
    source: str


@dataclass(frozen=True, slots=True)
class CapabilityClaim:
    state: CapabilityState
    evidence: Evidence | None = None
    #: Capability-specific detail, e.g. the signature scheme.
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FactClaim:
    value: int
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class VoiceManifest:
    provider: str
    display_name: str
    documentation_source: str | None
    documentation_version: str | None
    capabilities: dict[VoiceCapability, CapabilityClaim]
    #: Only verified facts are present; an absent fact is unknown.
    facts: dict[VoiceFact, FactClaim]
    #: Names of the secret fields the adapter needs, from the contract.
    credential_fields: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def state(self, capability: VoiceCapability) -> CapabilityState:
        claim = self.capabilities.get(capability)
        return claim.state if claim else CapabilityState.UNKNOWN

    def supports(self, capability: VoiceCapability) -> bool:
        return self.state(capability).is_usable

    def fact(self, fact: VoiceFact) -> int | None:
        claim = self.facts.get(fact)
        return claim.value if claim else None

    @property
    def missing_for_calls(self) -> list[str]:
        """Why confirmation calls cannot run on this provider; empty = they can."""
        missing: list[str] = []
        if not self.supports(VoiceCapability.DTMF_CAPTURE):
            missing.append("DTMF_CAPTURE_UNVERIFIED")
        if not self.supports(VoiceCapability.CALLBACK_SIGNATURE):
            # A callback we cannot verify could confirm an order nobody confirmed.
            missing.append("CALLBACK_SIGNATURE_UNVERIFIED")
        if self.state(VoiceCapability.AUDIO_UPLOAD) is CapabilityState.UNKNOWN:
            # Either answer works; not knowing means the intro cannot be played.
            missing.append("AUDIO_DELIVERY_UNVERIFIED")
        if self.fact(VoiceFact.MAX_CALL_SECONDS) is None:
            missing.append("MAX_CALL_SECONDS_UNVERIFIED")
        if all(self.fact(price) is None for price in _PRICES):
            missing.append("PRICE_UNVERIFIED")
        if not self.credential_fields:
            missing.append("CREDENTIAL_FIELDS_UNDECLARED")
        missing.extend(self.blockers)
        return missing

    @property
    def ready_for_calls(self) -> bool:
        return not self.missing_for_calls

    def as_dict(self) -> dict[str, Any]:
        """Operator-facing. Evidence included: the console shows claim and source together."""
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "documentation_source": self.documentation_source,
            "documentation_version": self.documentation_version,
            "capabilities": {
                str(capability): {
                    "state": str(self.state(capability)),
                    "detail": claim.detail if claim else None,
                    "verified_at": (
                        claim.evidence.verified_at.isoformat() if claim and claim.evidence else None
                    ),
                    "evidence": claim.evidence.source if claim and claim.evidence else None,
                }
                for capability in VoiceCapability
                for claim in [self.capabilities.get(capability)]
            },
            "facts": {
                str(fact): {
                    "value": claim.value if claim else None,
                    "verified_at": claim.evidence.verified_at.isoformat() if claim else None,
                    "evidence": claim.evidence.source if claim else None,
                }
                for fact in VoiceFact
                for claim in [self.facts.get(fact)]
            },
            "credential_fields": list(self.credential_fields),
            "blockers": list(self.blockers),
            "ready_for_calls": self.ready_for_calls,
            "missing_for_calls": self.missing_for_calls,
        }


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _evidence(entry: dict[str, Any]) -> Evidence | None:
    verified_at = _date(entry.get("verified_at"))
    source = entry.get("evidence")
    if verified_at is None or not isinstance(source, str) or not source.strip():
        return None
    return Evidence(verified_at=verified_at, source=source.strip())


def _capability(provider: str, name: str, entry: Any) -> CapabilityClaim:
    if not isinstance(entry, dict):
        # A bare ``true`` has no source: unknown, by the manifest's one rule.
        log.warning(
            "voice capability without evidence",
            extra={"provider": provider, "capability": name},
        )
        return CapabilityClaim(CapabilityState.UNKNOWN)
    state = CapabilityState.parse(entry.get("state", "unknown"))
    evidence = _evidence(entry)
    detail = entry.get("detail")
    detail_text = detail.strip() if isinstance(detail, str) and detail.strip() else None
    if state is not CapabilityState.UNKNOWN and evidence is None:
        log.warning(
            "voice capability without verified_at + evidence",
            extra={"provider": provider, "capability": name},
        )
        return CapabilityClaim(CapabilityState.UNKNOWN, detail=detail_text)
    if (
        name == VoiceCapability.CALLBACK_SIGNATURE
        and state is CapabilityState.SUPPORTED
        and detail_text is None
    ):
        # "Signed" without the scheme cannot be implemented, so it is not a claim.
        log.warning("callback signature without a scheme", extra={"provider": provider})
        return CapabilityClaim(CapabilityState.UNKNOWN, evidence=evidence)
    return CapabilityClaim(state, evidence=evidence, detail=detail_text)


def _fact(provider: str, name: str, entry: Any) -> FactClaim | None:
    if not isinstance(entry, dict):
        log.warning("voice fact without evidence", extra={"provider": provider, "fact": name})
        return None
    value = entry.get("value")
    evidence = _evidence(entry)
    # Whole numbers only: a price is integer paisa, never a float or a string.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or evidence is None:
        log.warning(
            "voice fact unverified or not a whole number",
            extra={"provider": provider, "fact": name},
        )
        return None
    if name == VoiceFact.MAX_CALL_SECONDS and value == 0:
        return None
    return FactClaim(value=value, evidence=evidence)


def parse_voice_manifest(raw: dict[str, Any], *, source: str = "<memory>") -> VoiceManifest:
    """Build a manifest; anything unproven becomes unknown rather than an error."""
    provider = str(raw.get("provider") or "").strip()
    if not _PROVIDER.fullmatch(provider):
        raise ValueError(f"voice manifest {source}: provider must be a lower-case slug")

    capabilities: dict[VoiceCapability, CapabilityClaim] = {}
    for key, entry in (raw.get("capabilities") or {}).items():
        try:
            capability = VoiceCapability(str(key))
        except ValueError:
            log.warning(
                "unknown capability in voice manifest",
                extra={"provider": provider, "capability": str(key)},
            )
            continue
        capabilities[capability] = _capability(provider, str(capability), entry)
    for capability in VoiceCapability:
        capabilities.setdefault(capability, CapabilityClaim(CapabilityState.UNKNOWN))

    facts: dict[VoiceFact, FactClaim] = {}
    for key, entry in (raw.get("facts") or {}).items():
        try:
            fact = VoiceFact(str(key))
        except ValueError:
            log.warning(
                "unknown fact in voice manifest", extra={"provider": provider, "fact": str(key)}
            )
            continue
        claim = _fact(provider, str(fact), entry)
        if claim is not None:
            facts[fact] = claim

    fields_raw = raw.get("credential_fields") or []
    credential_fields = tuple(str(name) for name in fields_raw)
    if any(not _CREDENTIAL_FIELD.fullmatch(name) for name in credential_fields) or len(
        set(credential_fields)
    ) != len(credential_fields):
        raise ValueError(f"voice manifest {source}: credential_fields must be unique slugs")

    return VoiceManifest(
        provider=provider,
        display_name=str(raw.get("display_name") or provider.title()),
        documentation_source=_text(raw.get("documentation_source")),
        documentation_version=_text(raw.get("documentation_version")),
        capabilities=capabilities,
        facts=facts,
        credential_fields=credential_fields,
        blockers=tuple(str(item) for item in (raw.get("blockers") or [])),
        notes=tuple(str(item) for item in (raw.get("notes") or [])),
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_voice_manifest(provider: str, *, directory: Path | None = None) -> VoiceManifest | None:
    path = (directory or VOICE_MANIFEST_DIR) / f"{provider}.yaml"
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return parse_voice_manifest(raw, source=path.name)


def load_voice_manifests(*, directory: Path | None = None) -> dict[str, VoiceManifest]:
    """Every shipped manifest, keyed by provider. Empty until a contract exists.

    A file that cannot be read is skipped and logged: one bad manifest must not
    hide the others from the console.
    """
    base = directory or VOICE_MANIFEST_DIR
    if not base.is_dir():
        log.info("no voice manifest directory", extra={"directory": str(base)})
        return {}
    manifests: dict[str, VoiceManifest] = {}
    for path in sorted(base.glob("*.yaml")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            manifest = parse_voice_manifest(raw, source=path.name)
        except (OSError, yaml.YAMLError, ValueError, AttributeError):
            log.error("voice manifest unreadable", extra={"file": path.name})
            continue
        manifests[manifest.provider] = manifest
    return manifests


_MOCK_EVIDENCE = {
    "verified_at": "2026-11-19",
    "evidence": "app/voice/transport.py MockVoiceTransport (test double, no real provider)",
}


def mock_manifest() -> VoiceManifest:
    """The mock transport's manifest. Never in the shipped directory."""
    return parse_voice_manifest(
        {
            "provider": "mock",
            "display_name": "Mock voice (local/test only)",
            "documentation_source": "Internal test double",
            "capabilities": {
                "dtmf_capture": {"state": True, **_MOCK_EVIDENCE},
                "dynamic_audio": {"state": True, **_MOCK_EVIDENCE},
                "callback_signature": {
                    "state": True,
                    "detail": "hmac_sha256_hex body, X-Mock-Voice-Signature",
                    **_MOCK_EVIDENCE,
                },
                "caller_id": {"state": False, **_MOCK_EVIDENCE},
                "audio_upload": {"state": True, **_MOCK_EVIDENCE},
            },
            "facts": {
                "max_call_seconds": {"value": 60, **_MOCK_EVIDENCE},
                "price_per_call_paisa": {"value": 0, **_MOCK_EVIDENCE},
            },
            "credential_fields": ["api_key"],
        },
        source="mock",
    )


def available_voice_manifests(settings: Settings) -> dict[str, VoiceManifest]:
    """What the console may connect: shipped manifests, plus the mock in local/test."""
    manifests = load_voice_manifests()
    if settings.voice_transport == "mock":
        manifests.setdefault("mock", mock_manifest())
    return manifests
