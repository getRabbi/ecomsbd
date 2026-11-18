"""The contract every external risk provider adapter implements.

Three rules shape it:

* **Minimum PII out.** An adapter receives a :class:`LookupRequest`: the
  customer's phone number in E.164 and the market. Never a name, address,
  order or shop identity.
* **Facts in, never scores.** An adapter returns :class:`ProviderFact` values
  whose codes come from :data:`FACT_CODES`. There is no field for an opaque
  score, so one cannot leak into a screen or a workflow as if it were truth.
  Codes outside the list are dropped by :func:`normalize_facts`.
* **Typed failures.** An adapter raises :class:`ProviderError` with a
  :class:`FailureKind`; the service maps it to a seller-safe code, decides
  whether to retry and records the provider's health.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

#: Facts a provider may report. Counts of outcomes the provider observed for
#: the number, over the period the provider states.
FACT_CODES: frozenset[str] = frozenset(
    {
        "DELIVERED_PARCELS",
        "RETURNED_PARCELS",
        "CANCELLED_ORDERS",
        "TOTAL_PARCELS",
    }
)

#: Upper bound on a reported count. A larger value is a provider error, not a fact.
MAX_FACT_VALUE = 1_000_000


class Capability(StrEnum):
    """What an adapter can do, shown to the seller before they connect it."""

    PHONE_DELIVERY_HISTORY = "PHONE_DELIVERY_HISTORY"
    CONNECTION_TEST = "CONNECTION_TEST"


class FailureKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_FAILED = "AUTH_FAILED"
    REJECTED = "REJECTED"
    BAD_RESPONSE = "BAD_RESPONSE"

    @property
    def retryable(self) -> bool:
        return self in {FailureKind.TIMEOUT, FailureKind.UNAVAILABLE}


class ProviderError(Exception):
    """A provider call failed. ``detail`` is for logs and must hold no secret or PII."""

    def __init__(
        self, kind: FailureKind, *, detail: str = "", retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(f"{kind}: {detail}"[:200])
        self.kind = kind
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class LookupRequest:
    phone_e164: str
    market: str = "BD"
    #: The lawful purpose the provider contract names; sent as-is.
    purpose: str = "COD_DELIVERY_RISK"


@dataclass(frozen=True)
class ProviderFact:
    code: str
    value: int
    #: The window the provider says the count covers, in days, if it says.
    period_days: int | None = None


@dataclass(frozen=True)
class CourierCount:
    """Parcels a provider saw for the number at one courier (V4 WP0.6)."""

    courier: str
    delivered: int
    cancelled: int
    total: int


@dataclass(frozen=True)
class CourierHistory:
    """Per-courier delivery history, provider-neutral (V4 WP0.6).

    ``aggregate`` is the provider's own total when it reports one (it may
    cover couriers it does not itemise); otherwise the rows are summed.
    """

    provider_counts: tuple[CourierCount, ...] = ()
    aggregate: CourierCount | None = None
    as_of: datetime | None = None


@dataclass(frozen=True)
class ProviderResult:
    found: bool
    facts: tuple[ProviderFact, ...] = ()
    #: When the provider says its data was current. ``None`` if it does not say.
    observed_at: datetime | None = None
    #: Only when the provider supplies them.
    sample_size: int | None = None
    confidence: str | None = None
    reference: str | None = None
    #: Per-courier counts, when the provider itemises them (V4 WP0.6).
    courier_history: CourierHistory | None = None


@dataclass(frozen=True)
class CredentialField:
    name: str
    label_en: str
    label_bn: str
    secret: bool = True


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    display_name: str
    #: Where the documented contract lives. An adapter without one is not registered.
    official_contract: str
    markets: frozenset[str]
    capabilities: frozenset[Capability]
    credential_fields: tuple[CredentialField, ...]
    default_cache_ttl_hours: int = 72
    min_cache_ttl_hours: int = 1
    max_cache_ttl_hours: int = 24 * 30
    timeout_seconds: float = 8.0
    config_fields: tuple[str, ...] = field(default_factory=tuple)


class RiskProviderAdapter(Protocol):
    spec: ProviderSpec

    async def test_connection(self, credentials: dict[str, str], config: dict) -> None:
        """Return on success; raise :class:`ProviderError` otherwise."""

    async def lookup(
        self, request: LookupRequest, credentials: dict[str, str], config: dict
    ) -> ProviderResult:
        """Facts about ``request``; raise :class:`ProviderError` on failure."""


def normalize_facts(facts: Iterable[ProviderFact]) -> tuple[list[dict], int]:
    """Allow-listed, bounded facts, and how many were dropped.

    Anything that is not a known count is dropped rather than stored: an
    unexplained score must not become something the product shows as truth.
    """
    kept: list[dict] = []
    dropped = 0
    seen: set[str] = set()
    for fact in facts:
        if (
            fact.code not in FACT_CODES
            or fact.code in seen
            or type(fact.value) is not int
            or not 0 <= fact.value <= MAX_FACT_VALUE
            or (fact.period_days is not None and not 1 <= fact.period_days <= 3650)
        ):
            dropped += 1
            continue
        seen.add(fact.code)
        kept.append({"code": fact.code, "value": fact.value, "period_days": fact.period_days})
    return kept, dropped


# --------------------------------------------------------------------------- #
# Courier history (V4 WP0.6)
# --------------------------------------------------------------------------- #

#: Below this many parcels a ratio is not shown: "kom data" (too little data).
SAMPLE_FLOOR = 3
_MAX_COURIERS = 12
_COURIER_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")


def _courier_key(raw: str) -> str | None:
    key = re.sub(r"[\s\-]+", "_", str(raw).strip().casefold())
    return key if _COURIER_KEY.match(key) else None


def _counts_ok(row: CourierCount) -> bool:
    values = (row.delivered, row.cancelled, row.total)
    return (
        all(type(v) is int and 0 <= v <= MAX_FACT_VALUE for v in values)
        and row.delivered + row.cancelled <= row.total
    )


def history_state(aggregate: dict | None) -> tuple[str, int | None]:
    """``("OK", success_bps)`` or ``("INSUFFICIENT_DATA", None)`` under the floor."""
    if not aggregate or int(aggregate.get("total") or 0) < SAMPLE_FLOOR:
        return "INSUFFICIENT_DATA", None
    total = int(aggregate["total"])
    return "OK", int(aggregate["delivered"]) * 10_000 // total


def normalize_courier_history(history: CourierHistory | None) -> dict | None:
    """Validated, bounded courier history in storage shape, or ``None``.

    A row with an unknown-shaped courier key, a negative or huge count, or
    delivered + cancelled above total is dropped, not repaired: a count the
    provider got wrong must not become a ratio the seller trusts.
    """
    if history is None:
        return None
    rows: list[dict] = []
    seen: set[str] = set()
    for row in history.provider_counts:
        key = _courier_key(row.courier)
        if key is None or key in seen or not _counts_ok(row) or len(rows) >= _MAX_COURIERS:
            continue
        seen.add(key)
        rows.append(
            {
                "courier": key,
                "delivered": row.delivered,
                "cancelled": row.cancelled,
                "total": row.total,
            }
        )
    if history.aggregate is not None and _counts_ok(history.aggregate):
        aggregate = {
            "delivered": history.aggregate.delivered,
            "cancelled": history.aggregate.cancelled,
            "total": history.aggregate.total,
        }
    elif rows:
        aggregate = {
            name: sum(r[name] for r in rows) for name in ("delivered", "cancelled", "total")
        }
    else:
        return None
    state, success_bps = history_state(aggregate)
    return {
        "provider_counts": rows,
        "aggregate": aggregate,
        "as_of": history.as_of.isoformat() if history.as_of else None,
        "state": state,
        "success_bps": success_bps,
    }
