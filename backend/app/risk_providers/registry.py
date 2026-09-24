"""Reviewed external risk provider adapters.

Empty on purpose. An adapter is added here only with a licensed, documented
provider contract behind it; no scraper, shared blacklist or guessed API is
ever registered. Environment switches or client input cannot add one.
"""

from __future__ import annotations

from app.risk_providers.contract import RiskProviderAdapter

BLOCKER = "EXTERNAL_RISK_PROVIDER_REQUIRED"

_ADAPTERS: dict[str, RiskProviderAdapter] = {}


def register(adapter: RiskProviderAdapter) -> None:
    _ADAPTERS[adapter.spec.provider_id] = adapter


def unregister(provider_id: str) -> None:
    _ADAPTERS.pop(provider_id, None)


def get(provider_id: str) -> RiskProviderAdapter | None:
    return _ADAPTERS.get(provider_id)


def available() -> list[RiskProviderAdapter]:
    return sorted(_ADAPTERS.values(), key=lambda a: a.spec.provider_id)
