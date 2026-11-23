"""SMS gateway capability manifests (V4 WP7.1).

The courier and voice manifests' rule: **a capability is ``true`` only with a
dated source.** Each entry in ``manifests/<provider>.yaml`` carries its own
``verified_at`` and ``evidence`` (the provider's documentation URL and the
``docs/providers/<provider>/CONTRACT.md`` section). An entry missing either
reads as ``unknown``, which is never used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger
from app.couriers.capabilities import CapabilityState

__all__ = [
    "SMS_MANIFEST_DIR",
    "SmsCapabilityClaim",
    "SmsManifest",
    "load_sms_manifests",
    "parse_sms_manifest",
]

log = get_logger(__name__)

#: Inside the ``app`` package so the production image carries it.
SMS_MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


@dataclass(frozen=True, slots=True)
class SmsCapabilityClaim:
    state: CapabilityState
    verified_at: date | None = None
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class SmsManifest:
    provider: str
    display_name: str
    base_url: str
    documentation_source: str
    credential_fields: tuple[str, ...]
    capabilities: dict[str, SmsCapabilityClaim] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        claim = self.capabilities.get(capability)
        return claim is not None and claim.state is CapabilityState.SUPPORTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "documentation_source": self.documentation_source,
            "credential_fields": list(self.credential_fields),
            "capabilities": {
                name: {
                    "state": str(claim.state),
                    "verified_at": claim.verified_at.isoformat() if claim.verified_at else None,
                    "evidence": claim.evidence,
                }
                for name, claim in sorted(self.capabilities.items())
            },
        }


def _claim(provider: str, name: str, entry: Any) -> SmsCapabilityClaim:
    if not isinstance(entry, dict):
        state = CapabilityState.parse(entry)
        if state is not CapabilityState.UNKNOWN:
            log.warning(
                "sms capability without evidence", extra={"provider": provider, "capability": name}
            )
        return SmsCapabilityClaim(CapabilityState.UNKNOWN)
    state = CapabilityState.parse(entry.get("state", "unknown"))
    raw_date = entry.get("verified_at")
    verified_at = raw_date if isinstance(raw_date, date) else None
    if isinstance(raw_date, str):
        try:
            verified_at = date.fromisoformat(raw_date.strip())
        except ValueError:
            verified_at = None
    evidence = entry.get("evidence")
    evidence_text = evidence.strip() if isinstance(evidence, str) and evidence.strip() else None
    if verified_at is None or evidence_text is None:
        if state is not CapabilityState.UNKNOWN:
            log.warning(
                "sms capability without verified_at + evidence",
                extra={"provider": provider, "capability": name},
            )
        return SmsCapabilityClaim(CapabilityState.UNKNOWN)
    return SmsCapabilityClaim(state, verified_at=verified_at, evidence=evidence_text)


def parse_sms_manifest(raw: dict[str, Any]) -> SmsManifest:
    provider = str(raw.get("provider") or "")
    if not _NAME.fullmatch(provider):
        raise ValueError("an SMS manifest needs a provider name")
    base_url = str(raw.get("base_url") or "")
    if not base_url.startswith("https://"):
        raise ValueError("an SMS manifest needs an https base_url")
    fields = tuple(str(name) for name in raw.get("credential_fields") or ())
    if not fields or not all(_NAME.fullmatch(name) for name in fields):
        raise ValueError("an SMS manifest needs its credential_fields")
    capabilities = raw.get("capabilities") or {}
    return SmsManifest(
        provider=provider,
        display_name=str(raw.get("display_name") or provider),
        base_url=base_url.rstrip("/"),
        documentation_source=str(raw.get("documentation_source") or ""),
        credential_fields=fields,
        capabilities={
            str(name): _claim(provider, str(name), entry) for name, entry in capabilities.items()
        },
    )


def load_sms_manifests(*, directory: Path | None = None) -> dict[str, SmsManifest]:
    found: dict[str, SmsManifest] = {}
    for path in sorted((directory or SMS_MANIFEST_DIR).glob("*.yaml")):
        manifest = parse_sms_manifest(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        found[manifest.provider] = manifest
    return found
