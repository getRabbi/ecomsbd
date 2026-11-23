"""Reviewed external risk provider adapters.

An adapter is offered only with a licensed, documented provider contract behind
it; no scraper, shared blacklist or guessed API is ever registered. Environment
switches or client input cannot add one.

Two kinds:

* **Built-in** adapters (V4 WP0.6: BD Courier) ship with the code, each behind
  its own feature flag in :data:`~app.common.feature_flags.RISK_PROVIDER_FLAGS`
  (default off). A shop sees one only while the flag is on for that shop.
* **Registered** adapters (:func:`register`) are always offered; tests use them.

Screens and lookups ask :func:`for_tenant` / :func:`get_for_tenant`; the plain
:func:`available` / :func:`get` see registered adapters only.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.risk_providers.contract import RiskProviderAdapter

BLOCKER = "EXTERNAL_RISK_PROVIDER_REQUIRED"

_ADAPTERS: dict[str, RiskProviderAdapter] = {}


def _built_in() -> dict[str, RiskProviderAdapter]:
    from app.risk_providers.bdcourier import ADAPTER as BDCOURIER

    return {BDCOURIER.spec.provider_id: BDCOURIER}


def register(adapter: RiskProviderAdapter) -> None:
    _ADAPTERS[adapter.spec.provider_id] = adapter


def unregister(provider_id: str) -> None:
    _ADAPTERS.pop(provider_id, None)


def get(provider_id: str) -> RiskProviderAdapter | None:
    return _ADAPTERS.get(provider_id)


def available() -> list[RiskProviderAdapter]:
    return sorted(_ADAPTERS.values(), key=lambda a: a.spec.provider_id)


def known() -> list[RiskProviderAdapter]:
    """Every adapter, flags ignored: for housekeeping (retention), never for lookups."""
    adapters = {**_built_in(), **_ADAPTERS}
    return sorted(adapters.values(), key=lambda a: a.spec.provider_id)


async def _flag_on(db: AsyncSession, tenant_id: uuid.UUID, provider_id: str) -> bool:
    from app.common.feature_flags import RISK_PROVIDER_FLAGS, FeatureFlagService

    flag = RISK_PROVIDER_FLAGS.get(provider_id)
    if flag is None:
        return False
    return await FeatureFlagService(db).is_enabled(flag, tenant_id=tenant_id)


async def get_for_tenant(
    db: AsyncSession, tenant_id: uuid.UUID, provider_id: str
) -> RiskProviderAdapter | None:
    """The adapter this shop may use: registered, or built-in with its flag on."""
    adapter = _ADAPTERS.get(provider_id)
    if adapter is not None:
        return adapter
    built_in = _built_in().get(provider_id)
    if built_in is not None and await _flag_on(db, tenant_id, provider_id):
        return built_in
    return None


async def for_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> list[RiskProviderAdapter]:
    """Every adapter this shop may use, ordered by provider id."""
    adapters = dict(_ADAPTERS)
    for provider_id, adapter in _built_in().items():
        if provider_id not in adapters and await _flag_on(db, tenant_id, provider_id):
            adapters[provider_id] = adapter
    return sorted(adapters.values(), key=lambda a: a.spec.provider_id)
