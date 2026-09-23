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

#: Manifests ship inside the ``app`` package. The production image is built
#: from the ``backend`` directory alone, and anything outside ``app`` is not in
#: it: when they lived under the repository's ``docs/``, production had no
#: manifests at all, and every courier read as unsupported.
MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"


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
    #: A provider payments API listing settlements. Distinct from ``PAYOUTS``,
    #: which is a downloadable statement: one is pulled, the other uploaded,
    #: and a provider may offer either, both or neither.
    PAYMENTS = "payments"
    #: Whether a payment can be expanded into the parcels it covers. Without
    #: it a payment is a lump sum, which reconciliation may only suggest
    #: against — never auto-match (master spec section 82).
    PAYMENT_CONSIGNMENTS = "payment_consignments"
    #: Serviceable-area or location reference data (police stations, zones).
    LOCATION_LOOKUP = "location_lookup"


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

    #: Which revision of the provider's documentation was read.
    documentation_version: str | None = None
    base_url: str | None = None
    auth_model: str | None = None

    #: Per-capability evidence: endpoint, method, request/response model, notes,
    #: and whether live credentials are needed to confirm runtime behaviour.
    #: Kept alongside the capability flags so a reviewer sees the claim and its
    #: evidence in one place rather than in two files that can drift.
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Named facts about what the documentation does *not* say. These are
    #: answers, not a backlog: "we asked and the document is silent".
    unknowns: dict[str, str] = field(default_factory=dict)

    #: Operator-facing blocker identifiers, verbatim, as they appear in
    #: ``docs/RELEASE_READINESS.md`` and the admin console.
    blockers: list[str] = field(default_factory=list)

    def state(self, capability: Capability) -> CapabilityState:
        return self.capabilities.get(capability, CapabilityState.UNKNOWN)

    def supports(self, capability: Capability) -> bool:
        return self.state(capability).is_usable

    def evidence_for(self, capability: Capability) -> dict[str, Any]:
        """The endpoint, method and models behind one capability claim."""
        return dict(self.endpoints.get(str(capability), {}))

    def requires_live_credentials(self, capability: Capability) -> bool:
        """Whether runtime behaviour of this capability is still unproven.

        A capability can be documented completely and still be unverified in
        practice — a create is not exercised without creating a real parcel.
        This is what separates "code complete" from "live verification done".
        """
        return bool(self.evidence_for(capability).get("live_credentials_required"))

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
            "documentation_version": self.documentation_version,
            # Deliberately *not* the endpoint table: the client reacts to
            # capability, and shipping provider paths to every device would
            # invite a client to call one directly.
            "unknowns": dict(self.unknowns),
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

    endpoints_raw = raw.get("endpoints") or {}
    endpoints: dict[str, dict[str, Any]] = {
        str(key): dict(value) for key, value in endpoints_raw.items() if isinstance(value, dict)
    }

    unknowns_raw = raw.get("unknowns") or {}
    unknowns = {str(key): str(value) for key, value in unknowns_raw.items()}

    return ProviderManifest(
        provider=provider,
        display_name=str(raw.get("display_name") or provider.title()),
        verified_at=verified_at,
        documentation_source=raw.get("documentation_source"),
        capabilities=capabilities,
        notes=[str(note) for note in (raw.get("notes") or [])],
        manual_fallback=raw.get("manual_fallback"),
        documentation_version=_optional_str(raw.get("documentation_version")),
        base_url=_optional_str(raw.get("base_url")),
        auth_model=_optional_str(raw.get("auth_model")),
        endpoints=endpoints,
        unknowns=unknowns,
        blockers=[str(blocker) for blocker in (raw.get("blockers") or [])],
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
        # Not a normal state. An empty answer here makes every courier
        # unsupported everywhere, so it is logged loudly rather than passing
        # for "this shop has no couriers".
        log.error("courier manifest directory is missing", extra={"directory": str(base)})
        return {}
    manifests: dict[str, ProviderManifest] = {}
    for path in sorted(base.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        manifest = _parse_manifest(raw, source=path)
        manifests[manifest.provider] = manifest
    return manifests
