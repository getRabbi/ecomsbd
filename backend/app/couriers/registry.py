"""Building a courier adapter for a provider.

One place decides which adapter a provider name maps to, and how its transport
is configured. Everywhere else takes an adapter and does not know which
provider it is — that is what keeps order, money and profit code free of
provider branches (master spec sections 34, 62.3).

The registry also owns the *fake* wiring used by the test suite and the offline
smoke runs, so a test never has to reach inside a service to swap a transport.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.couriers.adapter import CourierAdapter
from app.couriers.http import HttpxProviderTransport, ProviderTransport
from app.couriers.http import TransportTimeouts as ProviderTimeouts
from app.couriers.pathao.adapter import PROVIDER as PATHAO
from app.couriers.pathao.adapter import PathaoAdapter
from app.couriers.pathao.client import PathaoClient, PathaoConfig
from app.couriers.redx.adapter import PROVIDER as REDX
from app.couriers.redx.adapter import RedxAdapter
from app.couriers.redx.client import RedxClient, RedxConfig
from app.couriers.steadfast.adapter import PROVIDER as STEADFAST
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import SteadfastClient, SteadfastConfig
from app.couriers.steadfast.transport import (
    HttpxSteadfastTransport,
    SteadfastTransport,
    TransportTimeouts,
)

__all__ = [
    "AdapterFactory",
    "CourierAdapterRegistry",
    "build_registry",
    "get_courier_registry",
    "reset_courier_registry",
    "set_courier_registry",
]

log = get_logger(__name__)


class AdapterFactory(Protocol):
    def __call__(self) -> CourierAdapter: ...


class CourierAdapterRegistry:
    """Provider name -> adapter, built once per process.

    Adapters are stateless apart from their connection pool, so one instance
    per process is correct and is what makes pooling work at all: a new
    ``AsyncClient`` per booking would open a new TCP connection per parcel.
    """

    def __init__(self, factories: dict[str, AdapterFactory]) -> None:
        self._factories = factories
        self._instances: dict[str, CourierAdapter] = {}

    def supports(self, provider: str) -> bool:
        return provider in self._factories

    def get(self, provider: str) -> CourierAdapter | None:
        """The adapter for a provider, or ``None`` if there is no integration.

        ``None`` rather than an exception: a shop on manual courier mode is the
        normal case, not an error, and every caller already has to handle a
        provider it cannot reach.
        """
        if provider not in self._factories:
            return None
        if provider not in self._instances:
            self._instances[provider] = self._factories[provider]()
        return self._instances[provider]

    def register(self, provider: str, factory: AdapterFactory) -> None:
        self._factories[provider] = factory
        self._instances.pop(provider, None)

    async def aclose(self) -> None:
        for adapter in self._instances.values():
            close = getattr(adapter, "client", None)
            if close is not None and hasattr(close, "aclose"):
                await close.aclose()
        self._instances.clear()


def _steadfast_factory(settings: Settings) -> AdapterFactory:
    def build() -> CourierAdapter:
        transport: SteadfastTransport = HttpxSteadfastTransport(
            timeouts=TransportTimeouts(
                connect=settings.courier_connect_timeout_seconds,
                read=settings.courier_read_timeout_seconds,
                write=settings.courier_write_timeout_seconds,
                pool=settings.courier_pool_timeout_seconds,
            ),
            max_connections=settings.courier_max_connections,
        )
        config = SteadfastConfig(
            base_url=settings.steadfast_base_url,
            bulk_chunk_size=settings.steadfast_bulk_chunk_size,
            max_read_retries=settings.courier_read_retries,
        )
        return SteadfastAdapter(SteadfastClient(transport, config=config))

    return build


def _pathao_factory(settings: Settings) -> AdapterFactory:
    def build() -> CourierAdapter:
        transport: ProviderTransport = HttpxProviderTransport(
            provider=PATHAO,
            timeouts=ProviderTimeouts(
                connect=settings.courier_connect_timeout_seconds,
                read=settings.courier_read_timeout_seconds,
                write=settings.courier_write_timeout_seconds,
                pool=settings.courier_pool_timeout_seconds,
            ),
            max_connections=settings.courier_max_connections,
        )
        config = PathaoConfig(
            live_base_url=settings.pathao_base_url,
            sandbox_base_url=settings.pathao_sandbox_base_url,
            bulk_chunk_size=settings.pathao_bulk_chunk_size,
            max_read_retries=settings.courier_read_retries,
        )
        return PathaoAdapter(PathaoClient(transport, config=config))

    return build


def _redx_factory(settings: Settings) -> AdapterFactory:
    def build() -> CourierAdapter:
        transport: ProviderTransport = HttpxProviderTransport(
            provider=REDX,
            timeouts=ProviderTimeouts(
                connect=settings.courier_connect_timeout_seconds,
                read=settings.courier_read_timeout_seconds,
                write=settings.courier_write_timeout_seconds,
                pool=settings.courier_pool_timeout_seconds,
            ),
            max_connections=settings.courier_max_connections,
        )
        config = RedxConfig(
            live_base_url=settings.redx_base_url,
            sandbox_base_url=settings.redx_sandbox_base_url,
            max_read_retries=settings.courier_read_retries,
        )
        return RedxAdapter(RedxClient(transport, config=config))

    return build


def build_registry(settings: Settings | None = None) -> CourierAdapterRegistry:
    resolved = settings or get_settings()
    return CourierAdapterRegistry(
        {
            STEADFAST: _steadfast_factory(resolved),
            PATHAO: _pathao_factory(resolved),
            REDX: _redx_factory(resolved),
        }
    )


_registry: CourierAdapterRegistry | None = None


def get_courier_registry(settings: Settings | None = None) -> CourierAdapterRegistry:
    global _registry
    if _registry is None:
        _registry = build_registry(settings)
    return _registry


def set_courier_registry(registry: CourierAdapterRegistry | None) -> None:
    """Install a registry. Tests use this to wire a fake transport."""
    global _registry
    _registry = registry


def reset_courier_registry() -> None:
    set_courier_registry(None)
