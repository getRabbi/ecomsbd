"""Fan-out between processes: Redis pub/sub, or an in-process stand-in.

The API and the worker both commit shop data, and a seller's sockets may be
held by any API process, so invalidations travel through Redis. Without
``REDIS_URL`` (local development and the test suite) an in-process broker
does the same job inside one process.

Channel names are built here and only here, from a server-side tenant UUID.
Nothing a client sends is ever part of a channel name, so a client cannot ask
to listen to another shop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from redis.asyncio.client import PubSub

__all__ = [
    "CONTROL_CHANNEL",
    "TENANT_CHANNEL_PREFIX",
    "Broker",
    "InMemoryBroker",
    "Listener",
    "RedisBroker",
    "close_broker",
    "get_broker",
    "set_broker",
    "tenant_channel",
]

log = get_logger(__name__)

_NAMESPACE = "ecomsbd:rt:v1:"
TENANT_CHANNEL_PREFIX = _NAMESPACE + "tenant:"
#: Session and membership changes. Read by the socket hub, never forwarded.
CONTROL_CHANNEL = _NAMESPACE + "control"


def tenant_channel(tenant_id: uuid.UUID) -> str:
    """The one channel a shop's invalidations go to."""
    # uuid.UUID(...) refuses anything that is not a UUID, so a value that is
    # not one cannot become part of a channel name by accident.
    return TENANT_CHANNEL_PREFIX + str(uuid.UUID(str(tenant_id)))


class Listener(Protocol):
    """A live subscription to every tenant channel and the control channel."""

    async def next(self) -> tuple[str, str]:
        """The next ``(channel, message)``. Raises when the connection is lost."""
        ...

    async def close(self) -> None: ...


class Broker(Protocol):
    async def publish(self, channel: str, message: str) -> None: ...

    async def listen(self) -> Listener:
        """Subscribe. The subscription is live when this returns."""
        ...

    async def close(self) -> None: ...


class _QueueListener:
    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def next(self) -> tuple[str, str]:
        return await self.queue.get()

    async def close(self) -> None:
        self._broker._listeners.discard(self)


class InMemoryBroker:
    """Process-local fan-out. Correct for one process only."""

    def __init__(self) -> None:
        self._listeners: set[_QueueListener] = set()

    async def publish(self, channel: str, message: str) -> None:
        for listener in list(self._listeners):
            listener.queue.put_nowait((channel, message))

    async def listen(self) -> Listener:
        listener = _QueueListener(self)
        self._listeners.add(listener)
        return listener

    async def close(self) -> None:
        self._listeners.clear()


class _RedisListener:
    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub

    async def next(self) -> tuple[str, str]:
        while True:
            # The timeout lets the client's health check ping a quiet
            # connection, so a dead one raises here instead of hanging.
            message = await self._pubsub.get_message(timeout=30.0)
            if message is not None and message["type"] in ("message", "pmessage"):
                return str(message["channel"]), str(message["data"])

    async def close(self) -> None:
        await self._pubsub.aclose()


class RedisBroker:
    """Redis pub/sub. One publishing pool; one subscription per listener."""

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis

        self._url = url
        self._client = Redis.from_url(
            url, decode_responses=True, socket_keepalive=True, health_check_interval=30
        )

    async def publish(self, channel: str, message: str) -> None:
        await self._client.publish(channel, message)

    async def listen(self) -> Listener:
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.psubscribe(TENANT_CHANNEL_PREFIX + "*")
            await pubsub.subscribe(CONTROL_CHANNEL)
        except BaseException:
            await pubsub.aclose()
            raise
        return _RedisListener(pubsub)

    async def close(self) -> None:
        await self._client.aclose()


_broker: Broker | None = None


def get_broker(settings: Settings | None = None) -> Broker:
    """The process-wide broker."""
    global _broker
    if _broker is None:
        settings = settings or get_settings()
        _broker = RedisBroker(settings.redis_url) if settings.redis_url else InMemoryBroker()
    return _broker


def set_broker(broker: Broker | None) -> None:
    """Override the broker. Tests only."""
    global _broker
    _broker = broker


async def close_broker() -> None:
    global _broker
    if _broker is not None:
        try:
            await _broker.close()
        except Exception as exc:
            log.warning("realtime broker close failed", extra={"error_type": type(exc).__name__})
    _broker = None
