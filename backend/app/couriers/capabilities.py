"""Provider capabilities and the versioned integration manifest.

Master spec sections 74 and 75.

The manifest exists to make one specific mistake impossible: implementing an
endpoint because a community SDK has it. A capability is one of three states —
``true``, ``false``, ``unknown`` — and **``unknown`` never becomes ``true``
without a dated verification against official or merchant documentation**.

The UI reacts to capabilities, not provider names (section 75), so an account
whose provider cannot return payouts shows "upload a statement" rather than a
broken button.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger

__all__ = [
    "MANIFEST_DIR",
    "Capability",
    "CapabilityState",
    "ProviderManifest",
    "load_all_manifests",
    "load_manifest",
]

log = get_logger(__name__)

#: Manifests live in the repository next to the redacted provider notes so a
#: capability change and its evidence are reviewed together.
MANIFEST_DIR = Path(__file__).resolve().parents[3] / "docs" / "provider_notes"


class Capability(StrEnum):
    """Capabilities a courier account may advertise (master spec section 5)."""

    CREDENTIAL_VALIDATION = "credential_validation"
    CREATE_SINGLE = "create_single"
    CREATE_BULK = "create_bulk"
    STATUS_LOOKUP = "status_lookup"
    WEBHOOK = "webhook"
    BALANCE = "balance"
    PAYOUTS = "payouts"
    RETURNS = "returns"
    PRICE_QUOTE = "price_quote"
    CUSTOMER_STATS = "customer_stats"
    AUTO_ADDRESS = "auto_address"
    CANCEL = "cancel"
    LIST_STORES = "list_stores"


class CapabilityState(StrEnum):
    """Three-valued capability state. ``UNKNOWN`` is a first-class answer."""

    SUPPORTED = "true"
    UNSUPPORTED = "false"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> CapabilityState:
        if isinstance(value, bool):
            return cls.SUPPORTED if value else cls.UNSUPPORTED
        text = str(value).strip().lower()
        if text in ("true", "yes", "supported"):
            return cls.SUPPORTED
        if text in ("false", "no", "unsupported"):
            return cls.UNSUPPORTED
        return cls.UNKNOWN

    @property
    def is_usable(self) -> bool:
        """Only an explicitly verified capability may be called.

        ``UNKNOWN`` is treated as unusable. Optimism here would mean calling an
        endpoint that may not exist, against a live merchant account, for money.
        """
        return self is CapabilityState.SUPPORTED


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    """A provider's verified capability set at a point in time."""

    provider: str
    display_name: str
    verified_at: date | None
    documentation_source: str | None
    capabilities: dict[Capability, CapabilityState]
    notes: list[str] = field(default_factory=list)
    #: Free-text description of what a seller can still do without an API.
    manual_fallback: str | None = None

    def state(self, capability: Capability) -> CapabilityState:
        return self.capabilities.get(capability, CapabilityState.UNKNOWN)

    def supports(self, capability: Capability) -> bool:
        return self.state(capability).is_usable

    @property
    def has_any_verified_capability(self) -> bool:
        return any(state.is_usable for state in self.capabilities.values())

    @property
    def is_fully_unverified(self) -> bool:
        """True when nothing has been confirmed against real documentation yet."""
        return not self.has_any_verified_capability

    def as_dict(self) -> dict[str, Any]:
        """Client-facing shape. Drives capability-aware UI (section 75)."""
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "capabilities": {str(k): str(v) for k, v in self.capabilities.items()},
            "manual_fallback": self.manual_fallback,
            "fully_unverified": self.is_fully_unverified,
        }


def _parse_manifest(raw: dict[str, Any], *, source: Path) -> ProviderManifest:
    provider = str(raw.get("provider") or source.stem)
    verified_raw = raw.get("verified_at")
    verified_at: date | None
    if isinstance(verified_raw, date):
        verified_at = verified_raw
    elif isinstance(verified_raw, str) and verified_raw.strip():
        try:
            verified_at = date.fromisoformat(verified_raw.strip())
        except ValueError:
            verified_at = None
    else:
        verified_at = None

    declared = raw.get("capabilities") or {}
    capabilities: dict[Capability, CapabilityState] = {}
    for key, value in declared.items():
        try:
            capability = Capability(str(key))
        except ValueError:
            log.warning(
                "unknown capability in provider manifest",
                extra={"provider": provider, "capability": str(key)},
            )
            continue
        capabilities[capability] = CapabilityState.parse(value)

    # Anything not mentioned is unknown, never assumed supported.
    for capability in Capability:
        capabilities.setdefault(capability, CapabilityState.UNKNOWN)

    return ProviderManifest(
        provider=provider,
        display_name=str(raw.get("display_name") or provider.title()),
        verified_at=verified_at,
        documentation_source=raw.get("documentation_source"),
        capabilities=capabilities,
        notes=[str(note) for note in (raw.get("notes") or [])],
        manual_fallback=raw.get("manual_fallback"),
    )


def load_manifest(provider: str, *, directory: Path | None = None) -> ProviderManifest | None:
    """Load one provider manifest, or ``None`` if it does not exist."""
    base = directory or MANIFEST_DIR
    path = base / f"{provider}.yaml"
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return _parse_manifest(raw, source=path)


def load_all_manifests(*, directory: Path | None = None) -> dict[str, ProviderManifest]:
    """Load every provider manifest in the directory, keyed by provider id."""
    base = directory or MANIFEST_DIR
    if not base.is_dir():
        return {}
    manifests: dict[str, ProviderManifest] = {}
    for path in sorted(base.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        manifest = _parse_manifest(raw, source=path)
        manifests[manifest.provider] = manifest
    return manifests
