"""Redis abstraction with an in-process fallback.

Redis backs rate limiting, provider token caches and distributed locks (master
spec sections 35, 47). Local development and the test suite should not require a
running Redis, so this module exposes one small interface with two backends:

*   :class:`RedisBackend` — the real thing, used whenever ``REDIS_URL`` is set;
*   :class:`InMemoryBackend` — process-local, used otherwise.

The in-memory backend is correct for a single process and *not* correct across
workers. That is fine for laptops and tests and wrong for production, which is
why :class:`~app.core.config.Settings` refuses to start a deployed environment
without ``REDIS_URL``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import generate_opaque_token

__all__ = [
    "CacheBackend",
    "InMemoryBackend",
    "RateLimitResult",
    "RateLimiter",
    "RedisBackend",
    "close_cache",
    "get_cache",
]

log = get_logger(__name__)


class CacheBackend(Protocol):
    """Minimal key/value surface the application relies on."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def incr_with_ttl(self, key: str, *, ttl_seconds: int) -> int:
        """Increment a counter, setting the TTL on first use. Returns the new value."""
        ...

    async def ttl(self, key: str) -> int:
        """Seconds until expiry; ``-1`` if the key has no TTL, ``-2`` if absent."""
        ...

    async def acquire_lock(self, key: str, *, ttl_seconds: int) -> str | None: ...

    async def release_lock(self, key: str, token: str) -> None: ...

    async def close(self) -> None: ...


@dataclass
class _Entry:
    value: str
    expires_at: float | None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.monotonic()


class InMemoryBackend:
    """Process-local backend. Single-process correctness only."""

    def __init__(self) -> None:
        self._data: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    def _read(self, key: str) -> _Entry | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expired:
            self._data.pop(key, None)
            return None
        return entry

    async def get(self, key: str) -> str | None:
        async with self._lock:
            entry = self._read(key)
            return entry.value if entry else None

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        async with self._lock:
            expires = time.monotonic() + ttl_seconds if ttl_seconds else None
            self._data[key] = _Entry(value, expires)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def incr_with_ttl(self, key: str, *, ttl_seconds: int) -> int:
        async with self._lock:
            entry = self._read(key)
            if entry is None:
                self._data[key] = _Entry("1", time.monotonic() + ttl_seconds)
                return 1
            new_value = int(entry.value) + 1
            entry.value = str(new_value)
            return new_value

    async def ttl(self, key: str) -> int:
        async with self._lock:
            entry = self._read(key)
            if entry is None:
                return -2
            if entry.expires_at is None:
                return -1
            return max(0, int(entry.expires_at - time.monotonic()))

    async def acquire_lock(self, key: str, *, ttl_seconds: int) -> str | None:
        async with self._lock:
            if self._read(key) is not None:
                return None
            token = generate_opaque_token(16)
            self._data[key] = _Entry(token, time.monotonic() + ttl_seconds)
            return token

    async def release_lock(self, key: str, token: str) -> None:
        async with self._lock:
            entry = self._read(key)
            if entry is not None and entry.value == token:
                self._data.pop(key, None)

    async def close(self) -> None:
        self._data.clear()


class RedisBackend:
    """Redis-backed implementation."""

    #: Compare-and-delete so a lock is only released by its owner. Releasing
    #: another holder's lock would let two workers book the same parcel.
    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis  # imported lazily: optional at runtime

        self._client = Redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def incr_with_ttl(self, key: str, *, ttl_seconds: int) -> int:
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)
        result = await pipe.execute()
        return int(result[0])

    async def ttl(self, key: str) -> int:
        return int(await self._client.ttl(key))

    async def acquire_lock(self, key: str, *, ttl_seconds: int) -> str | None:
        token = generate_opaque_token(16)
        acquired = await self._client.set(key, token, ex=ttl_seconds, nx=True)
        return token if acquired else None

    async def release_lock(self, key: str, token: str) -> None:
        await self._client.eval(self._RELEASE_SCRIPT, 1, key, token)

    async def close(self) -> None:
        await self._client.aclose()


_backend: CacheBackend | None = None


def get_cache(settings: Settings | None = None) -> CacheBackend:
    """The process-wide cache backend."""
    global _backend
    if _backend is None:
        settings = settings or get_settings()
        if settings.redis_url:
            _backend = RedisBackend(settings.redis_url)
            log.info("cache backend: redis")
        else:
            _backend = InMemoryBackend()
            log.warning(
                "cache backend: in-process (REDIS_URL is not set). "
                "Rate limits and locks are per-process only."
            )
    return _backend


async def close_cache() -> None:
    global _backend
    if _backend is not None:
        await _backend.close()
    _backend = None


def set_cache_backend(backend: CacheBackend | None) -> None:
    """Override the backend. Tests only."""
    global _backend
    _backend = backend


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int

    @property
    def exceeded(self) -> bool:
        return not self.allowed


class RateLimiter:
    """Fixed-window counters.

    Master spec section 47 requires limits per IP, user, tenant, courier account,
    risk phone lookup, OTP, export and AI parse. A fixed window is coarse at the
    boundary but cheap and, for abuse control on OTP and exports, sufficient.
    """

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self._backend = backend or get_cache()

    @staticmethod
    def _key(scope: str, identity: str, window_seconds: int) -> str:
        window = int(time.time()) // window_seconds
        return f"rl:{scope}:{identity}:{window}"

    async def hit(
        self, scope: str, identity: str, *, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Record an attempt and report whether it is permitted."""
        key = self._key(scope, identity, window_seconds)
        count = await self._backend.incr_with_ttl(key, ttl_seconds=window_seconds)
        remaining = max(0, limit - count)
        retry_after = await self._backend.ttl(key)
        return RateLimitResult(
            allowed=count <= limit,
            limit=limit,
            remaining=remaining,
            retry_after_seconds=max(1, retry_after if retry_after > 0 else window_seconds),
        )

    async def peek(
        self, scope: str, identity: str, *, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Check without consuming an attempt."""
        key = self._key(scope, identity, window_seconds)
        raw = await self._backend.get(key)
        count = int(raw) if raw else 0
        retry_after = await self._backend.ttl(key)
        return RateLimitResult(
            allowed=count < limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after_seconds=max(1, retry_after if retry_after > 0 else window_seconds),
        )

    @asynccontextmanager
    async def lock(self, name: str, *, ttl_seconds: int = 30) -> AsyncIterator[bool]:
        """Best-effort distributed lock. Yields whether it was acquired."""
        token = await self._backend.acquire_lock(f"lock:{name}", ttl_seconds=ttl_seconds)
        try:
            yield token is not None
        finally:
            if token is not None:
                await self._backend.release_lock(f"lock:{name}", token)
