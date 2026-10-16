"""Realtime invalidation: who may listen, what they hear, and what can't break.

The socket is driven over raw ASGI messages in the test's own event loop, so
the HTTP client, the hub and the socket share one loop and one in-process
broker. Redis itself is exercised through a stand-in client at the end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header

ALLOWED_EVENT_KEYS = {"type", "topic", "entity_id", "version", "event_id", "occurred_at"}


class SocketClosed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class Socket:
    """One client socket, spoken to in ASGI messages."""

    def __init__(self, app: Any, *, query: bytes = b"", headers: list[tuple[bytes, bytes]]) -> None:
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.raw: list[str] = []
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": "/v1/realtime",
            "raw_path": b"/v1/realtime",
            "root_path": "",
            "query_string": query,
            "headers": [(b"host", b"testserver"), *headers],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 50000),
            "subprotocols": [],
            "state": {},
        }
        self._task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))

    async def open(self) -> str:
        await self._to_app.put({"type": "websocket.connect"})
        message = await asyncio.wait_for(self._from_app.get(), 3)
        return str(message["type"])

    async def send(self, data: Any) -> None:
        text = data if isinstance(data, str) else json.dumps(data)
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def recv(self, within: float = 3.0) -> dict[str, Any]:
        message = await asyncio.wait_for(self._from_app.get(), within)
        if message["type"] == "websocket.close":
            raise SocketClosed(int(message.get("code", 1000)))
        self.raw.append(message["text"])
        return dict(json.loads(message["text"]))

    async def next_event(self, within: float = 3.0) -> dict[str, Any]:
        """The next message that is not a heartbeat."""
        deadline = time.monotonic() + within
        while True:
            message = await self.recv(max(0.01, deadline - time.monotonic()))
            if message["type"] not in ("ping", "pong"):
                return message

    async def expect_close(self, within: float = 3.0) -> int:
        deadline = time.monotonic() + within
        while True:
            try:
                await self.recv(max(0.01, deadline - time.monotonic()))
            except SocketClosed as closed:
                return closed.code

    async def expect_silence(self, seconds: float = 0.4) -> None:
        try:
            message = await self.next_event(seconds)
        except TimeoutError:
            return
        raise AssertionError(f"expected nothing, got {message}")

    async def disconnect(self) -> None:
        if not self._task.done():
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
            try:
                await asyncio.wait_for(self._task, 3)
            except (TimeoutError, Exception):
                self._task.cancel()


@dataclass
class Harness:
    app: Any
    http: AsyncClient
    sockets: list[Socket] = field(default_factory=list)

    def socket(self, *, query: bytes = b"", origin: str | None = None) -> Socket:
        headers = [(b"origin", origin.encode())] if origin else []
        sock = Socket(self.app, query=query, headers=headers)
        self.sockets.append(sock)
        return sock

    async def connect(self, session: dict[str, Any]) -> tuple[Socket, dict[str, Any]]:
        """An authenticated socket and its ``ready`` message."""
        from app.realtime.hub import get_hub

        sock = self.socket()
        assert await sock.open() == "websocket.accept"
        await sock.send({"type": "auth", "access_token": session["access_token"]})
        ready = await sock.next_event()
        assert ready["type"] == "ready", ready
        await asyncio.wait_for(get_hub().subscribed.wait(), 3)
        return sock, ready


@pytest_asyncio.fixture
async def rt(settings: Any) -> AsyncIterator[Harness]:
    from app.main import create_app
    from app.realtime.broker import InMemoryBroker, set_broker
    from app.realtime.hub import close_hub

    await close_hub()
    set_broker(InMemoryBroker())
    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        harness = Harness(app=app, http=http)
        try:
            yield harness
        finally:
            for sock in harness.sockets:
                await sock.disconnect()
    await close_hub()
    set_broker(None)


async def settle() -> None:
    """Let committed changes reach the sockets."""
    from app.realtime.publisher import drain

    await drain()
    for _ in range(5):
        await asyncio.sleep(0.02)


# --------------------------------------------------------------------------- #
# Topic map
# --------------------------------------------------------------------------- #


def test_every_table_is_announced_or_deliberately_silent() -> None:
    import app.main  # noqa: F401  - loads every router and so every model
    from app.db.base import Base
    from app.realtime.topics import SILENT_TABLES, TABLE_TOPICS

    tables = set(Base.metadata.tables)
    mapped = set(TABLE_TOPICS)
    assert not mapped & SILENT_TABLES
    assert tables - mapped - SILENT_TABLES == set(), "decide: announce or silence"
    assert (mapped | SILENT_TABLES) - tables == set(), "stale entries"


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


class TestAuthentication:
    async def test_a_socket_that_never_authenticates_is_closed(
        self, rt: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import realtime
        from app.realtime.hub import get_hub

        monkeypatch.setattr(realtime, "AUTH_TIMEOUT_SECONDS", 0.2)
        sock = rt.socket()
        assert await sock.open() == "websocket.accept"
        assert await sock.expect_close() == 4408
        assert get_hub().connection_count == 0

    async def test_a_bad_token_is_refused(self, rt: Harness) -> None:
        sock = rt.socket()
        await sock.open()
        await sock.send({"type": "auth", "access_token": "not-a-token"})
        assert await sock.expect_close() == 4401

    async def test_any_first_message_but_auth_is_refused(self, rt: Harness) -> None:
        sock = rt.socket()
        await sock.open()
        await sock.send({"type": "subscribe", "shop_id": str(uuid.uuid4())})
        assert await sock.expect_close() == 4401

    async def test_a_token_in_the_url_authenticates_nothing(
        self, rt: Harness, unique_phone: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import realtime

        monkeypatch.setattr(realtime, "AUTH_TIMEOUT_SECONDS", 0.3)
        session = await signed_in_shop(rt.http, unique_phone)
        sock = rt.socket(query=f"access_token={session['access_token']}".encode())
        await sock.open()
        assert await sock.expect_close() == 4408

    async def test_a_session_without_a_shop_cannot_subscribe(
        self, rt: Harness, unique_phone: str
    ) -> None:
        from tests.test_auth_flow import sign_in

        session = await sign_in(rt.http, unique_phone)
        sock = rt.socket()
        await sock.open()
        await sock.send({"type": "auth", "access_token": session["access_token"]})
        assert await sock.expect_close() == 4403

    async def test_a_foreign_origin_is_refused_at_the_handshake(
        self, rt: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "cors_allow_origins", ["https://app.ecomsbd.com"])
        assert await rt.socket(origin="https://evil.example").open() == "websocket.close"
        assert await rt.socket(origin="https://app.ecomsbd.com").open() == "websocket.accept"

    async def test_switched_off_means_closed(
        self, rt: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "realtime_enabled", False)
        sock = rt.socket()
        await sock.open()
        assert await sock.expect_close() == 1013

    async def test_the_token_never_reaches_the_logs(
        self,
        rt: Harness,
        unique_phone: str,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = await signed_in_shop(rt.http, unique_phone)
        # Alembic's fileConfig disables loggers that existed before the
        # migrations ran, which depends on test order. This test reads them.
        for name in ("app", "app.api.v1.realtime", "app.realtime.hub", "app.api.deps"):
            monkeypatch.setattr(logging.getLogger(name), "disabled", False)
        caplog.set_level(logging.DEBUG)
        sock, _ = await rt.connect(session)
        await sock.send({"type": "auth", "access_token": session["access_token"]})
        await sock.next_event()
        await sock.disconnect()
        assert any(r.getMessage() == "realtime socket opened" for r in caplog.records)
        token = session["access_token"]
        for record in caplog.records:
            assert token not in record.getMessage()
            assert token not in repr(record.__dict__)


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #


class TestIsolation:
    async def test_a_shop_hears_its_own_changes_and_no_one_elses(self, rt: Harness) -> None:
        shop_a = await signed_in_shop(rt.http, "01710000001", shop_name="Shop A")
        shop_b = await signed_in_shop(rt.http, "01810000002", shop_name="Shop B")
        sock_a, ready_a = await rt.connect(shop_a)
        sock_b, ready_b = await rt.connect(shop_b)
        assert ready_a["shop_id"] == str(shop_a["tenant_id"])
        assert ready_b["shop_id"] == str(shop_b["tenant_id"])

        product = await create_product(rt.http, shop_a, name="Shop A Kurti")
        await settle()

        topics = set()
        while True:
            try:
                event = await sock_a.next_event(0.4)
            except TimeoutError:
                break
            assert event["type"] == "invalidate"
            topics.add(event["topic"])
            if event["topic"] == "products":
                assert event["entity_id"] == str(product["id"])
        assert "products" in topics
        await sock_b.expect_silence()

    async def test_extra_fields_cannot_choose_a_channel(self, rt: Harness) -> None:
        mine = await signed_in_shop(rt.http, "01710000003", shop_name="Mine")
        theirs = await signed_in_shop(rt.http, "01810000004", shop_name="Theirs")
        sock = rt.socket()
        await sock.open()
        await sock.send(
            {
                "type": "auth",
                "access_token": mine["access_token"],
                "shop_id": str(theirs["tenant_id"]),
                "channel": f"ecomsbd:rt:v1:tenant:{theirs['tenant_id']}",
            }
        )
        ready = await sock.next_event()
        assert ready["shop_id"] == str(mine["tenant_id"])
        await create_product(rt.http, theirs, name="Not yours")
        await settle()
        await sock.expect_silence()

    def test_channel_names_only_come_from_a_uuid(self) -> None:
        from app.realtime.broker import tenant_channel

        tenant = uuid.uuid4()
        assert tenant_channel(tenant) == f"ecomsbd:rt:v1:tenant:{tenant}"
        with pytest.raises(ValueError):
            tenant_channel("*")  # type: ignore[arg-type]

    async def test_events_carry_no_customer_data(self, rt: Harness) -> None:
        shop = await signed_in_shop(rt.http, "01710000005", shop_name="Private Shop")
        sock, _ = await rt.connect(shop)
        order = await create_order(
            rt.http,
            shop,
            phone="01755512345",
            customer_name="Rahima Khatun",
            address="House 12, Road 5, Dhanmondi",
        )
        await settle()
        events = []
        while True:
            try:
                events.append(await sock.next_event(0.4))
            except TimeoutError:
                break
        assert {e["topic"] for e in events} >= {"orders", "customers"}
        for event in events:
            assert set(event) == ALLOWED_EVENT_KEYS
        orders = next(e for e in events if e["topic"] == "orders")
        assert orders["entity_id"] == str(order["order"]["id"])
        wire = "".join(sock.raw)
        for secret in ("01755512345", "1755512345", "Rahima", "Dhanmondi", "125000"):
            assert secret not in wire

    async def test_a_stray_publish_is_rebuilt_from_allowed_fields(self, rt: Harness) -> None:
        from app.realtime.broker import get_broker, tenant_channel

        shop = await signed_in_shop(rt.http, "01710000006", shop_name="Strict Shop")
        sock, _ = await rt.connect(shop)
        channel = tenant_channel(uuid.UUID(str(shop["tenant_id"])))
        broker = get_broker()
        await broker.publish(
            channel,
            json.dumps(
                {
                    "type": "invalidate",
                    "topic": "orders",
                    "entity_id": "x",
                    "phone": "01755512345",
                    "event_id": str(uuid.uuid4()),
                }
            ),
        )
        await broker.publish(channel, json.dumps({"type": "invalidate", "topic": "secrets"}))
        await broker.publish(channel, "not json")
        await settle()
        event = await sock.next_event()
        assert set(event) == ALLOWED_EVENT_KEYS
        assert event["entity_id"] is None
        assert "01755512345" not in "".join(sock.raw)
        await sock.expect_silence()


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #


class TestSessionLifecycle:
    async def test_signing_out_closes_the_socket_at_once(
        self, rt: Harness, unique_phone: str
    ) -> None:
        session = await signed_in_shop(rt.http, unique_phone)
        sock, _ = await rt.connect(session)
        response = await rt.http.post(
            "/v1/auth/logout", json={"all_devices": False}, headers=auth_header(session)
        )
        assert response.status_code == 204, response.text
        await settle()
        assert await sock.expect_close() == 4401

    async def test_an_expired_token_stops_the_feed(
        self, rt: Harness, unique_phone: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import realtime

        monkeypatch.setattr(realtime, "_token_expiry", lambda _token: time.time() + 0.3)
        session = await signed_in_shop(rt.http, unique_phone)
        sock, _ = await rt.connect(session)
        assert await sock.expect_close() == 4401

    async def test_a_fresh_token_keeps_the_socket(
        self, rt: Harness, unique_phone: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import realtime

        lifetimes = iter([0.5, 3600.0])
        monkeypatch.setattr(realtime, "_token_expiry", lambda _token: time.time() + next(lifetimes))
        session = await signed_in_shop(rt.http, unique_phone)
        sock, _ = await rt.connect(session)
        await sock.send({"type": "auth", "access_token": session["access_token"]})
        assert (await sock.next_event())["type"] == "ready"
        await asyncio.sleep(0.7)
        await create_product(rt.http, session, name="Still listening")
        await settle()
        assert (await sock.next_event())["type"] == "invalidate"

    async def test_switching_shop_drops_the_old_shop_feed(
        self, rt: Harness, unique_phone: str
    ) -> None:
        first = await signed_in_shop(rt.http, unique_phone, shop_name="First")
        second = await rt.http.post(
            "/v1/tenants",
            json={"name": "Second", "business_category": "CLOTHING"},
            headers=auth_header(first),
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["tenant_id"]
        back = await rt.http.post(
            "/v1/auth/select-tenant",
            json={"tenant_id": str(first["tenant_id"])},
            headers=auth_header(first),
        )
        assert back.status_code == 200, back.text
        await settle()

        sock, ready = await rt.connect(first)
        assert ready["shop_id"] == str(first["tenant_id"])

        moved = await rt.http.post(
            "/v1/auth/select-tenant",
            json={"tenant_id": str(second_id)},
            headers=auth_header(first),
        )
        assert moved.status_code == 200, moved.text
        await settle()
        assert await sock.expect_close() == 4409

        again, ready = await rt.connect(first)
        assert ready["shop_id"] == str(second_id)
        # The old shop's changes no longer reach this session.
        from app.core.context import use_context
        from app.db.session import session_scope
        from app.tenants.models import Tenant

        with use_context(tenant_id=uuid.UUID(str(first["tenant_id"]))):
            async with session_scope() as db:
                tenant = await db.get(Tenant, uuid.UUID(str(first["tenant_id"])))
                assert tenant is not None
                tenant.name = "First, renamed"
        await settle()
        await again.expect_silence()

    def test_a_removed_member_and_a_disabled_account_are_cut_off(self) -> None:
        from app.realtime.broker import CONTROL_CHANNEL
        from app.realtime.hub import RealtimeHub, Subscriber

        hub = RealtimeHub()
        tenant, user = uuid.uuid4(), uuid.uuid4()
        member = Subscriber(session_id=uuid.uuid4(), user_id=user, tenant_id=tenant)
        other = Subscriber(session_id=uuid.uuid4(), user_id=uuid.uuid4(), tenant_id=tenant)
        hub._all.update({member, other})
        hub._by_tenant[tenant] = {member, other}

        hub.dispatch(
            CONTROL_CHANNEL,
            json.dumps({"kind": "member", "tenant_id": str(tenant), "user_id": str(user)}),
        )
        assert member.closed and member.outbox.get_nowait().code == 4409
        assert not other.closed
        assert member not in hub._by_tenant[tenant]

        hub.dispatch(CONTROL_CHANNEL, json.dumps({"kind": "user", "user_id": str(other.user_id)}))
        assert other.closed and other.outbox.get_nowait().code == 4401


# --------------------------------------------------------------------------- #
# Delivery guarantees
# --------------------------------------------------------------------------- #


class _FailingBroker:
    def __init__(self) -> None:
        self.attempts = 0

    async def publish(self, channel: str, message: str) -> None:
        self.attempts += 1
        raise ConnectionError("redis is down")

    async def listen(self) -> Any:
        raise ConnectionError("redis is down")

    async def close(self) -> None:
        return None


class TestDelivery:
    async def test_a_sale_succeeds_when_publishing_fails(
        self, rt: Harness, unique_phone: str
    ) -> None:
        from app.realtime.broker import set_broker

        shop = await signed_in_shop(rt.http, unique_phone)
        failing = _FailingBroker()
        set_broker(failing)
        order = await create_order(rt.http, shop, phone="01755500001")
        await settle()
        assert failing.attempts >= 1
        fetched = await rt.http.get(f"/v1/orders/{order['order']['id']}", headers=auth_header(shop))
        assert fetched.status_code == 200

    async def test_a_capture_failure_never_fails_the_flush(
        self, rt: Harness, unique_phone: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.realtime import publisher

        def broken(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("bug in capture")

        monkeypatch.setattr(publisher, "_record", broken)
        shop = await signed_in_shop(rt.http, unique_phone)
        await create_product(rt.http, shop, name="Saved anyway")

    async def test_a_rolled_back_change_is_never_announced(
        self, rt: Harness, unique_phone: str
    ) -> None:
        from app.core.context import use_context
        from app.db.session import get_sessionmaker
        from app.realtime.broker import get_broker
        from app.tenants.models import Tenant

        shop = await signed_in_shop(rt.http, unique_phone)
        tenant_id = uuid.UUID(str(shop["tenant_id"]))
        listener = await get_broker().listen()
        try:
            with use_context(tenant_id=tenant_id):
                async with get_sessionmaker()() as db:
                    tenant = await db.get(Tenant, tenant_id)
                    assert tenant is not None
                    tenant.name = "Never saved"
                    await db.flush()
                    await db.rollback()
                await settle()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(listener.next(), 0.3)

                async with get_sessionmaker()() as db:
                    tenant = await db.get(Tenant, tenant_id)
                    assert tenant is not None
                    tenant.name = "Saved"
                    await db.commit()
                await settle()
                channel, message = await asyncio.wait_for(listener.next(), 1)
                assert channel.endswith(str(tenant_id))
                assert json.loads(message)["topic"] == "shop"
        finally:
            await listener.close()

    async def test_a_lost_subscription_ends_in_a_resync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.realtime import hub as hub_module
        from app.realtime.broker import InMemoryBroker, set_broker

        class FlakyBroker(InMemoryBroker):
            calls = 0

            async def listen(self) -> Any:
                FlakyBroker.calls += 1
                if FlakyBroker.calls == 2:
                    raise ConnectionError("redis restarting")
                listener = await super().listen()
                if FlakyBroker.calls == 1:

                    async def dies() -> tuple[str, str]:
                        raise ConnectionError("connection reset")

                    listener.next = dies  # type: ignore[method-assign]
                return listener

        monkeypatch.setattr(hub_module, "_RETRY_BASE_SECONDS", 0.01)
        set_broker(FlakyBroker())
        hub = hub_module.RealtimeHub()
        sub = hub_module.Subscriber(
            session_id=uuid.uuid4(), user_id=uuid.uuid4(), tenant_id=uuid.uuid4()
        )
        try:
            hub.add(sub)
            message = await asyncio.wait_for(sub.outbox.get(), 2)
            assert json.loads(str(message)) == {"type": "resync"}
            assert FlakyBroker.calls >= 3
        finally:
            await hub.stop()
            set_broker(None)

    def test_a_stalled_socket_is_told_to_resync_not_buffered(self) -> None:
        from app.realtime.hub import Subscriber

        sub = Subscriber(session_id=uuid.uuid4(), user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        for index in range(200):
            sub.deliver(json.dumps({"n": index}))
        assert sub.outbox.qsize() <= 65
        drained = [sub.outbox.get_nowait() for _ in range(sub.outbox.qsize())]
        assert json.dumps({"type": "resync"}, separators=(",", ":")) in drained


# --------------------------------------------------------------------------- #
# Redis
# --------------------------------------------------------------------------- #


class _FakePubSub:
    def __init__(self) -> None:
        self.patterns: list[str] = []
        self.channels: list[str] = []
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.closed = False

    async def psubscribe(self, *patterns: str) -> None:
        self.patterns.extend(patterns)

    async def subscribe(self, *channels: str) -> None:
        self.channels.extend(channels)

    async def get_message(self, timeout: float) -> dict[str, Any] | None:  # noqa: ASYNC109 - redis-py's signature
        try:
            return await asyncio.wait_for(self.inbox.get(), timeout)
        except TimeoutError:
            return None

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.pubsub_instance = _FakePubSub()

    def pubsub(self, **_kwargs: Any) -> _FakePubSub:
        return self.pubsub_instance

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def aclose(self) -> None:
        return None


class TestRedisBroker:
    async def test_subscribes_to_server_owned_channels_only(self) -> None:
        from app.realtime.broker import CONTROL_CHANNEL, TENANT_CHANNEL_PREFIX, RedisBroker

        broker = RedisBroker("redis://localhost:6379/0")
        fake = _FakeRedis()
        broker._client = fake  # type: ignore[assignment]
        listener = await broker.listen()
        assert fake.pubsub_instance.patterns == [TENANT_CHANNEL_PREFIX + "*"]
        assert fake.pubsub_instance.channels == [CONTROL_CHANNEL]

        await fake.pubsub_instance.inbox.put({"type": "psubscribe", "channel": "x", "data": 1})
        await fake.pubsub_instance.inbox.put(
            {"type": "pmessage", "channel": TENANT_CHANNEL_PREFIX + "abc", "data": "{}"}
        )
        assert await listener.next() == (TENANT_CHANNEL_PREFIX + "abc", "{}")
        await listener.close()
        assert fake.pubsub_instance.closed

        await broker.publish(CONTROL_CHANNEL, "{}")
        assert fake.published == [(CONTROL_CHANNEL, "{}")]

    async def test_publishes_after_commit_through_redis(
        self, rt: Harness, unique_phone: str
    ) -> None:
        from app.realtime.broker import RedisBroker, set_broker

        shop = await signed_in_shop(rt.http, unique_phone)
        broker = RedisBroker("redis://localhost:6379/0")
        fake = _FakeRedis()
        broker._client = fake  # type: ignore[assignment]
        set_broker(broker)
        await create_product(rt.http, shop, name="Via Redis")
        await settle()
        channels = {channel for channel, _ in fake.published}
        assert f"ecomsbd:rt:v1:tenant:{shop['tenant_id']}" in channels
