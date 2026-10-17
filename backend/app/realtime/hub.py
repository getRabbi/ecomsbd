"""The sockets one API process holds, and the subscription that feeds them.

One broker subscription per process, not one per socket. Each tenant message
goes only to the sockets bound to that tenant; control messages (a session
revoked or moved to another shop, a membership or account changed) close the
affected sockets at once, so a socket never keeps a shop's feed after its
session lost the right to it.

Messages from the broker are re-checked and rebuilt before they reach a client:
only an ``invalidate`` with a known topic and the documented fields goes out,
so even a stray publish to the channel cannot carry anything else.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.realtime.broker import CONTROL_CHANNEL, TENANT_CHANNEL_PREFIX, Listener, get_broker
from app.realtime.topics import RealtimeTopic

__all__ = [
    "CLOSE_BAD_MESSAGE",
    "CLOSE_FORBIDDEN",
    "CLOSE_REBIND",
    "CLOSE_UNAUTHENTICATED",
    "Close",
    "RealtimeHub",
    "Subscriber",
    "close_hub",
    "get_hub",
    "sanitize_invalidation",
]

log = get_logger(__name__)

# Application close codes (4000-4999), shared with the web and phone clients.
CLOSE_BAD_MESSAGE = 4400
#: Not signed in, token expired, or session revoked: get a fresh token first.
CLOSE_UNAUTHENTICATED = 4401
#: Signed in, but no shop, or no longer a member of it.
CLOSE_FORBIDDEN = 4403
CLOSE_AUTH_TIMEOUT = 4408
#: The session now points at another shop: reconnect, and bind to that one.
CLOSE_REBIND = 4409
CLOSE_TOO_MANY_MESSAGES = 4429

#: Undelivered messages one socket may hold before it is told to resync.
_OUTBOX_LIMIT = 64
_RESYNC = json.dumps({"type": "resync"}, separators=(",", ":"))
_TOPICS = frozenset(str(topic) for topic in RealtimeTopic)
#: First wait before resubscribing to a lost broker; doubles up to 30 s.
_RETRY_BASE_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class Close:
    code: int
    reason: str


@dataclass(eq=False)
class Subscriber:
    """One authenticated socket, bound to exactly one shop."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    outbox: asyncio.Queue[str | Close] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_OUTBOX_LIMIT + 1)
    )
    closed: bool = False

    def deliver(self, message: str) -> None:
        if self.closed:
            return
        if self.outbox.qsize() >= _OUTBOX_LIMIT:
            # A client this far behind refetches everything anyway; dropping
            # the backlog keeps memory bounded for a stalled connection.
            self._clear()
            self.outbox.put_nowait(_RESYNC)
            return
        self.outbox.put_nowait(message)

    def close(self, code: int, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        self._clear()
        self.outbox.put_nowait(Close(code, reason))

    def _clear(self) -> None:
        while not self.outbox.empty():
            self.outbox.get_nowait()


def sanitize_invalidation(raw: str) -> str | None:
    """Rebuild a tenant-channel message from its allowed fields, or refuse it."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "invalidate":
        return None
    topic = data.get("topic")
    if topic not in _TOPICS:
        return None

    def _uuid_or_none(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return None

    version = data.get("version")
    occurred_at = data.get("occurred_at")
    clean = {
        "type": "invalidate",
        "topic": topic,
        "entity_id": _uuid_or_none(data.get("entity_id")),
        "version": version if isinstance(version, int) and not isinstance(version, bool) else None,
        "event_id": _uuid_or_none(data.get("event_id")),
        "occurred_at": occurred_at
        if isinstance(occurred_at, str) and len(occurred_at) <= 40
        else None,
    }
    return json.dumps(clean, separators=(",", ":"))


class RealtimeHub:
    """Every socket in this process, by shop, and the broker subscription."""

    def __init__(self) -> None:
        self._by_tenant: dict[uuid.UUID, set[Subscriber]] = {}
        self._all: set[Subscriber] = set()
        self._task: asyncio.Task[None] | None = None
        self._lost = False
        #: Set while the broker subscription is live.
        self.subscribed = asyncio.Event()

    # ------------------------------------------------------------ sockets ---

    def add(self, subscriber: Subscriber) -> None:
        self._all.add(subscriber)
        self._by_tenant.setdefault(subscriber.tenant_id, set()).add(subscriber)
        self._ensure_started()

    def remove(self, subscriber: Subscriber) -> None:
        self._all.discard(subscriber)
        self._unbind(subscriber)

    def rebind(self, subscriber: Subscriber, tenant_id: uuid.UUID) -> None:
        """Move a socket to another shop. The old shop's feed stops here."""
        self._unbind(subscriber)
        subscriber.tenant_id = tenant_id
        if subscriber in self._all and not subscriber.closed:
            self._by_tenant.setdefault(tenant_id, set()).add(subscriber)

    def _unbind(self, subscriber: Subscriber) -> None:
        members = self._by_tenant.get(subscriber.tenant_id)
        if members is not None:
            members.discard(subscriber)
            if not members:
                del self._by_tenant[subscriber.tenant_id]

    def _close(self, subscriber: Subscriber, code: int, reason: str) -> None:
        # Unbound before the close is even sent: nothing more for that shop
        # reaches this socket from this moment.
        self._unbind(subscriber)
        subscriber.close(code, reason)

    @property
    def connection_count(self) -> int:
        return len(self._all)

    # ----------------------------------------------------------- dispatch ---

    def dispatch(self, channel: str, message: str) -> None:
        if channel == CONTROL_CHANNEL:
            self._control(message)
            return
        if not channel.startswith(TENANT_CHANNEL_PREFIX):
            return
        try:
            tenant_id = uuid.UUID(channel[len(TENANT_CHANNEL_PREFIX) :])
        except ValueError:
            return
        members = self._by_tenant.get(tenant_id)
        if not members:
            return
        clean = sanitize_invalidation(message)
        if clean is None:
            log.warning("realtime: dropped a malformed tenant message")
            return
        for subscriber in list(members):
            subscriber.deliver(clean)

    def _control(self, message: str) -> None:
        try:
            data = json.loads(message)
            kind = data["kind"]
            if kind == "session":
                session_id = uuid.UUID(data["session_id"])
                revoked = bool(data.get("revoked"))
                moved_to = data.get("tenant_id")
                for subscriber in [s for s in self._all if s.session_id == session_id]:
                    if revoked:
                        self._close(subscriber, CLOSE_UNAUTHENTICATED, "session revoked")
                    elif moved_to != str(subscriber.tenant_id):
                        self._close(subscriber, CLOSE_REBIND, "shop changed")
            elif kind == "member":
                tenant_id = uuid.UUID(data["tenant_id"])
                user_id = uuid.UUID(data["user_id"])
                for subscriber in [
                    s for s in self._all if s.user_id == user_id and s.tenant_id == tenant_id
                ]:
                    # Reconnecting re-checks the membership and the role.
                    self._close(subscriber, CLOSE_REBIND, "membership changed")
            elif kind == "user":
                user_id = uuid.UUID(data["user_id"])
                for subscriber in [s for s in self._all if s.user_id == user_id]:
                    self._close(subscriber, CLOSE_UNAUTHENTICATED, "account changed")
        except (ValueError, KeyError, TypeError):
            log.warning("realtime: dropped a malformed control message")

    def _resync_all(self) -> None:
        for subscriber in list(self._all):
            subscriber.deliver(_RESYNC)

    # ------------------------------------------------------- subscription ---

    def _ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        delay = _RETRY_BASE_SECONDS
        while True:
            listener: Listener | None = None
            try:
                listener = await get_broker().listen()
                self.subscribed.set()
                if self._lost:
                    # Messages published while the subscription was down are
                    # gone. Every client refetches what it shows.
                    self._lost = False
                    self._resync_all()
                delay = _RETRY_BASE_SECONDS
                while True:
                    channel, message = await listener.next()
                    self.dispatch(channel, message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.subscribed.clear()
                self._lost = True
                log.warning(
                    "realtime: broker subscription lost",
                    extra={"error_type": type(exc).__name__, "retry_in_seconds": round(delay, 1)},
                )
            finally:
                if listener is not None:
                    with suppress(Exception):
                        await listener.close()
            await asyncio.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311 - jitter
            delay = min(delay * 2, 30.0)

    async def stop(self) -> None:
        for subscriber in list(self._all):
            subscriber.close(1012, "server restarting")
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        self.subscribed.clear()


_hub: RealtimeHub | None = None


def get_hub() -> RealtimeHub:
    global _hub
    if _hub is None:
        _hub = RealtimeHub()
    return _hub


async def close_hub() -> None:
    global _hub
    if _hub is not None:
        await _hub.stop()
    _hub = None
