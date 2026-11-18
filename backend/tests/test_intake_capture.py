"""Capture endpoint + Intake service (V4 WP1.3).

Every source enters one Intake service and becomes ``chat_messages`` rows that
the existing chat-order worker folds into ``chat_order_drafts``: a linked
phone's captures, a share or paste, and (tested in test_chat_orders) Meta.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient

from app.chat_orders import jobs, service
from app.chat_orders.models import ChatIdentity, ChatMessage, ChatOrderDraft
from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.clock import utc_now
from app.db.session import system_session
from app.integrations.models import IntegrationConnection
from app.notifications.models import Notification
from app.orders.models import Order
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_device_links import LINK, _device, _key_header

CAPTURES = "/v1/intake/captures"
MANUAL = "/v1/intake/captures/manual"
BEAT = "/v1/intake/heartbeat"
WA_BUSINESS = "com.whatsapp.w4b"
PHONE = "01700000011"
CHAT = ["কালো পাঞ্জাবিটা লাগবে", "XL দুইটা", PHONE, "মিরপুর ১০ ঢাকা"]


@pytest_asyncio.fixture
async def intake_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.INTAKE_DEVICE_ENABLED)):
        yield


async def _linked(client: AsyncClient, shop: dict[str, Any], **device: Any) -> dict[str, Any]:
    response = await client.post(LINK, json=_device(**device), headers=auth_header(shop))
    assert response.status_code == 200, response.text
    return response.json()


def _batch(lines: list[str], *, package: str = WA_BUSINESS, chat: str = "Rahim") -> dict[str, Any]:
    base = utc_now() - timedelta(minutes=5)
    return {
        "package": package,
        "conversation_key": chat,
        "sender_display": chat,
        "messages": [
            {"text": line, "sent_at": (base + timedelta(seconds=20 * i)).isoformat()}
            for i, line in enumerate(lines)
        ],
    }


async def _upload(
    client: AsyncClient, key: str, body: dict[str, Any], batch: str | None = None
) -> Any:
    headers = {**_key_header(key), "Idempotency-Key": batch or f"batch-{uuid.uuid4().hex}"}
    return await client.post(CAPTURES, json=body, headers=headers)


@pytest_asyncio.fixture(autouse=True)
async def _drain() -> AsyncIterator[None]:
    """Leave no RECEIVED rows behind for other tests' worker runs."""
    yield
    for _ in range(5):
        if (await jobs.process_chat_messages())["processed"] == 0:
            break


async def _fold(shop: dict[str, Any]) -> None:
    """Run the chat-order worker over this shop's received messages only."""
    tenant_id = uuid.UUID(shop["tenant_id"])
    async with system_session("test: due messages") as db:
        due = (
            await db.scalars(
                sa.select(ChatMessage.id)
                .where(
                    ChatMessage.tenant_id == tenant_id, ChatMessage.processing_status == "RECEIVED"
                )
                .order_by(ChatMessage.provider_timestamp, ChatMessage.received_at)
            )
        ).all()
    for message_id in due:
        await jobs.process_one(tenant_id, message_id)


async def _rows(model: Any, shop: dict[str, Any]) -> list[Any]:
    async with system_session("test: rows") as db:
        return list(
            (
                await db.scalars(
                    sa.select(model)
                    .where(model.tenant_id == uuid.UUID(shop["tenant_id"]))
                    .order_by(model.created_at)
                )
            ).all()
        )


async def _count(model: Any, shop: dict[str, Any], *where: Any) -> int:
    async with system_session("test: count") as db:
        return int(
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(model)
                .where(model.tenant_id == uuid.UUID(shop["tenant_id"]), *where)
            )
            or 0
        )


async def _ready_events(shop: dict[str, Any]) -> int:
    return await _count(OutboxEvent, shop, OutboxEvent.topic == str(OutboxTopic.INTAKE_DRAFT_READY))


async def _ready_pushes(shop: dict[str, Any]) -> int:
    return await _count(Notification, shop, Notification.kind == "CHAT_ORDER_READY")


# ------------------------------------------------------------- the flag ---


async def test_capture_routes_are_off_without_the_flag(client: AsyncClient, unique_phone: str):
    shop = await signed_in_shop(client, unique_phone)
    manual = await client.post(
        MANUAL,
        json={"source": "PASTE", "messages": [{"text": "hello"}]},
        headers=auth_header(shop),
    )
    assert manual.status_code == 403 and manual.json()["code"] == "FEATURE_DISABLED"


# ------------------------------------------------------- device captures ---


async def test_a_phone_upload_becomes_one_draft_through_the_chat_order_worker(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    device = await _linked(client, shop)
    response = await _upload(client, device["ingest_key"], _batch(CHAT))
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 4 and response.json()["duplicates"] == 0

    messages = await _rows(ChatMessage, shop)
    assert len(messages) == 4
    assert {m.provider for m in messages} == {"DEVICE"}
    assert {m.capture_source for m in messages} == {"DEVICE_NOTIFICATION"}
    assert {m.account_ref for m in messages} == {WA_BUSINESS}
    assert {m.processing_status for m in messages} == {"RECEIVED"}, "recorded, not parsed"
    assert all(m.provider_message_id.startswith("h1:") for m in messages)
    assert all(PHONE not in m.provider_message_id for m in messages)
    (identity,) = await _rows(ChatIdentity, shop)
    assert identity.provider == "DEVICE" and identity.account_ref == WA_BUSINESS
    assert identity.external_key == service.get_hasher().chat_identity_hash(
        "DEVICE", WA_BUSINESS, "Rahim"
    )
    assert identity.phone_masked is None and "Rahim" not in identity.external_id_enc

    await jobs.process_chat_messages()
    (draft,) = await _rows(ChatOrderDraft, shop)
    assert draft.status == "READY_FOR_REVIEW" and draft.message_count == 4
    assert draft.capture_source == "DEVICE_NOTIFICATION" and draft.provider == "DEVICE"
    assert draft.selected_phone == "+8801700000011"
    assert await _ready_pushes(shop) == 1 and await _ready_events(shop) == 1

    listed = await client.get("/v1/chat-orders?status=ready", headers=auth_header(shop))
    (view,) = listed.json()["items"]
    assert view["capture_source"] == "DEVICE_NOTIFICATION"
    assert view["source_app"] == WA_BUSINESS and view["duplicate_of_order_id"] is None


async def test_batches_are_idempotent_and_messages_dedupe_by_hash(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    body = _batch(CHAT[:2])
    first = await _upload(client, key, body, batch="batch-0001")
    again = await _upload(client, key, body, batch="batch-0001")
    assert first.status_code == again.status_code == 200
    assert again.json() == {**first.json(), "replayed": True}

    changed = await _upload(client, key, _batch(CHAT[:1]), batch="batch-0001")
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    # The phone re-posts the same notification in a new batch: nothing new.
    resent = await _upload(client, key, body, batch="batch-0002")
    assert resent.json()["accepted"] == 0 and resent.json()["duplicates"] == 2
    # Within one batch, the same message twice is stored once.
    twice = _batch([CHAT[2]])
    twice["messages"] *= 2
    once = await _upload(client, key, twice)
    assert once.json() == {**once.json(), "accepted": 1, "duplicates": 1}
    assert await _count(ChatMessage, shop) == 3
    assert await _count(ChatIdentity, shop) == 1


async def test_batch_limits_and_the_idempotency_key_are_enforced(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    too_many = await _upload(client, key, _batch([f"m{i}" for i in range(51)]))
    too_long = await _upload(client, key, _batch(["x" * 4001]))
    blank = await _upload(client, key, _batch(["   "]))
    no_key = await client.post(CAPTURES, json=_batch(["hi"]), headers=_key_header(key))
    assert [r.status_code for r in (too_many, too_long, blank, no_key)] == [422] * 4
    fifty = await _upload(client, key, _batch([f"line {i}" for i in range(50)]))
    four_k = await _upload(client, key, _batch(["y" * 4000], chat="Karim"))
    assert fifty.status_code == four_k.status_code == 200
    session = await client.post(CAPTURES, json=_batch(["hi"]), headers=auth_header(shop))
    assert session.status_code == 401, "a seller session is not an ingest key"


async def test_only_apps_turned_on_for_the_shop_are_accepted(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    messenger = await _upload(client, key, _batch(CHAT, package="com.facebook.orca"))
    assert messenger.status_code == 403
    assert messenger.json()["details"]["reason"] == "PACKAGE_NOT_ENABLED"
    unknown = await _upload(client, key, _batch(CHAT, package="com.example.chat"))
    assert unknown.status_code == 403
    assert await _count(ChatMessage, shop) == 0

    turned_on = await client.patch(
        "/v1/intake/settings",
        json={"package_toggles": {"com.facebook.orca": True}},
        headers=auth_header(shop),
    )
    assert turned_on.status_code == 200, turned_on.text
    assert (await _upload(client, key, _batch(CHAT, package="com.facebook.orca"))).json()[
        "accepted"
    ] == 4


async def test_uploads_are_rate_limited_per_device(
    client: AsyncClient, unique_phone: str, intake_on: None, monkeypatch: Any
):
    from app.api.v1 import intake as intake_api

    monkeypatch.setattr(intake_api, "CAPTURES_PER_MINUTE", 2)
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    other = (await _linked(client, shop))["ingest_key"]
    codes = [(await _upload(client, key, _batch([f"m{i}"]))).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    assert (await _upload(client, other, _batch(["x"]))).status_code == 200, "per device"
    assert intake_api.CAPTURES_PER_MINUTE == 2 and intake_api.MAX_BATCH == 50


async def test_extension_captures_wait_for_phase_5(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop, platform="extension"))["ingest_key"]
    refused = await _upload(client, key, _batch(CHAT))
    assert refused.status_code == 403 and refused.json()["code"] == "FEATURE_DISABLED"


async def test_message_text_is_never_logged(
    client: AsyncClient, unique_phone: str, intake_on: None, caplog: Any
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    # Alembic's fileConfig (test_migrations) disables every logger that existed
    # when it ran; switch ecomsbd's back on for this test so it is not vacuous.
    disabled = [
        logger
        for name, logger in logging.root.manager.loggerDict.items()
        if name.startswith("app") and isinstance(logger, logging.Logger) and logger.disabled
    ]
    for logger in disabled:
        logger.disabled = False
    try:
        with caplog.at_level(logging.DEBUG):
            await _upload(client, key, _batch(CHAT, chat="Secret Sender"))
            await _fold(shop)
    finally:
        for logger in disabled:
            logger.disabled = True
    # Everything ecomsbd logs, and anything at INFO or above. (The SQLite
    # driver's own DEBUG trace prints query parameters; it is off in production.)
    records = [r for r in caplog.records if r.name.startswith("app") or r.levelno >= logging.INFO]
    assert any(r.name.startswith("app") for r in records)
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in records)
    for secret in (*CHAT, "Secret Sender", "+8801700000011"):
        assert secret not in logged


# ---------------------------------------------------------- manual path ---


async def test_a_paste_is_read_into_a_draft_at_once(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    response = await client.post(
        MANUAL,
        json={"source": "PASTE", "messages": [{"text": "\n".join(CHAT)}]},
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 1 and body["attention_id"] is None
    draft = body["draft"]
    assert draft["status"] == "READY_FOR_REVIEW" and draft["capture_source"] == "PASTE"
    assert draft["selected_phone"] == "+8801700000011" and draft["source_app"] == "paste"
    assert await _ready_events(shop) == 1

    # The shop's share/paste connection is hidden from the Integrations Hub.
    (conn,) = await _rows(IntegrationConnection, shop)
    assert conn.provider == "DEVICE" and conn.config["platform"] == "manual"
    hub = await client.get("/v1/integrations", headers=auth_header(shop))
    assert hub.status_code == 200 and hub.json()["items"] == []

    # A second paste is its own sender; it joins the open draft by its phone.
    second = await client.post(
        MANUAL,
        json={"source": "SHARE", "messages": [{"text": f"{PHONE} ar ekta lal shari"}]},
        headers=auth_header(shop),
    )
    assert second.json()["draft"]["id"] == draft["id"]
    assert second.json()["draft"]["message_count"] == 2
    assert len(await _rows(ChatOrderDraft, shop)) == 1
    assert len(await _rows(IntegrationConnection, shop)) == 1


async def test_manual_capture_rules(client: AsyncClient, unique_phone: str, intake_on: None):
    shop = await signed_in_shop(client, unique_phone)
    other = await signed_in_shop(client, f"0171{uuid.uuid4().int % 10_000_000:07d}")
    device = await _linked(client, shop)
    foreign = await _linked(client, other)

    no_chat = await client.post(
        MANUAL,
        json={"source": "DEVICE_NOTIFICATION", "messages": [{"text": "x"}]},
        headers=auth_header(shop),
    )
    assert no_chat.status_code == 422
    across = await client.post(
        MANUAL,
        json={
            "source": "SHARE",
            "messages": [{"text": "x"}],
            "connection_id": foreign["connection_id"],
        },
        headers=auth_header(shop),
    )
    assert across.status_code == 404
    with_key = await client.post(
        MANUAL,
        json={"source": "PASTE", "messages": [{"text": "x"}]},
        headers=_key_header(device["ingest_key"]),
    )
    assert with_key.status_code == 401, "an ingest key is not a seller session"

    body = {"source": "PASTE", "messages": [{"text": "\n".join(CHAT)}]}
    headers = {**auth_header(shop), "Idempotency-Key": "paste-000001"}
    first = await client.post(MANUAL, json=body, headers=headers)
    again = await client.post(MANUAL, json=body, headers=headers)
    assert again.json()["replayed"] is True
    assert again.json()["draft"]["id"] == first.json()["draft"]["id"]
    assert await _count(ChatMessage, shop) == 1


async def test_tapping_create_joins_what_the_phone_uploaded(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    device = await _linked(client, shop)
    batch = _batch(CHAT[:2])
    assert (await _upload(client, device["ingest_key"], batch)).status_code == 200
    tapped = await client.post(
        MANUAL,
        json={
            "source": "DEVICE_NOTIFICATION",
            "package": WA_BUSINESS,
            "conversation_key": "Rahim",
            "connection_id": device["connection_id"],
            "messages": [*batch["messages"], {"text": PHONE}, {"text": CHAT[3]}],
        },
        headers=auth_header(shop),
    )
    assert tapped.status_code == 200, tapped.text
    assert tapped.json()["accepted"] == 2 and tapped.json()["duplicates"] == 2
    draft = tapped.json()["draft"]
    assert draft["status"] == "READY_FOR_REVIEW" and draft["message_count"] == 4
    assert await _count(ChatIdentity, shop) == 1 and len(await _rows(ChatOrderDraft, shop)) == 1


# ------------------------------------------------------- duplicate merge ---


async def test_a_second_phone_joins_the_open_draft_for_the_same_number(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    first = (await _linked(client, shop))["ingest_key"]
    second = (await _linked(client, shop))["ingest_key"]
    await _upload(client, first, _batch(CHAT))
    await _fold(shop)
    await _upload(client, second, _batch([PHONE, "ar ekta lal shari"], chat="Rahim Bhai"))
    await _fold(shop)
    (draft,) = await _rows(ChatOrderDraft, shop)
    assert draft.message_count == 6 and draft.status == "READY_FOR_REVIEW"
    assert await _ready_pushes(shop) == 1 and await _ready_events(shop) == 1
    # Later messages from the second phone's conversation follow the merge.
    await _upload(client, second, _batch(["2ta lagbe"], chat="Rahim Bhai"))
    await _fold(shop)
    (draft,) = await _rows(ChatOrderDraft, shop)
    assert draft.message_count == 7


async def test_a_phone_with_an_order_today_is_attached_not_announced(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    order = (await create_order(client, shop, phone=PHONE))["order"]
    key = (await _linked(client, shop))["ingest_key"]
    await _upload(client, key, _batch(CHAT))
    await _fold(shop)
    (draft,) = await _rows(ChatOrderDraft, shop)
    assert str(draft.duplicate_of_order_id) == order["id"]
    assert "DUPLICATE_ORDER" in draft.warnings
    assert draft.status == "READY_FOR_REVIEW"
    assert await _ready_pushes(shop) == 0 and await _ready_events(shop) == 0
    # A later message keeps the link and the warning.
    await _upload(client, key, _batch(["kobe pabo?"]))
    await _fold(shop)
    (draft,) = await _rows(ChatOrderDraft, shop)
    assert str(draft.duplicate_of_order_id) == order["id"] and "DUPLICATE_ORDER" in draft.warnings


async def test_an_old_order_does_not_attach(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    order = (await create_order(client, shop, phone=PHONE))["order"]
    async with system_session("test: age the order") as db:
        await db.execute(
            sa.update(Order)
            .where(Order.id == uuid.UUID(order["id"]))
            .values(created_at=utc_now() - timedelta(hours=25))
        )
    key = (await _linked(client, shop))["ingest_key"]
    await _upload(client, key, _batch(CHAT))
    await _fold(shop)
    (draft,) = await _rows(ChatOrderDraft, shop)
    assert draft.duplicate_of_order_id is None and "DUPLICATE_ORDER" not in draft.warnings
    assert await _ready_events(shop) == 1


# --------------------------------------------------------------- confirm ---


async def test_confirming_a_phone_draft_uses_the_apps_channel(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    await _upload(client, key, _batch(CHAT))
    await _fold(shop)
    (draft,) = await _rows(ChatOrderDraft, shop)
    confirmed = await client.post(
        f"/v1/chat-orders/{draft.id}/confirm",
        json={
            "phone": PHONE,
            "address": "Mirpur 10, Dhaka",
            "items": [{"name": "Black Panjabi XL", "quantity": 2, "unit_price_paisa": 92_500}],
        },
        headers=auth_header(shop),
    )
    assert confirmed.status_code == 200, confirmed.text
    async with system_session("test: order") as db:
        order = await db.get(Order, uuid.UUID(confirmed.json()["order"]["id"]))
        assert order is not None and str(order.channel) == "WHATSAPP"
        assert order.metadata_json["chat"]["capture_source"] == "DEVICE_NOTIFICATION"


def test_order_channels_by_source() -> None:
    def draft(provider: str) -> ChatOrderDraft:
        return ChatOrderDraft(provider=provider)

    def sender(app: str) -> ChatIdentity:
        return ChatIdentity(account_ref=app)

    channel = service.order_channel
    assert channel(draft("WHATSAPP"), None) == "WHATSAPP"
    assert channel(draft("MESSENGER"), None) == "MESSENGER"
    assert channel(draft("DEVICE"), sender("com.whatsapp")) == "WHATSAPP"
    assert channel(draft("DEVICE"), sender("com.facebook.orca")) == "MESSENGER"
    # Business Suite carries several kinds of chat; a share or paste is a parse.
    for app in ("com.facebook.pages.app", "paste", "share"):
        assert channel(draft("DEVICE"), sender(app)) == "PASTE_PARSE"


# ---------------------------------------------------------- housekeeping ---


async def test_retention_removes_device_senders_with_nothing_left(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    key = (await _linked(client, shop))["ingest_key"]
    await _upload(client, key, _batch(["hello there"]))
    await _fold(shop)
    async with system_session("test: ignore") as db:
        await db.execute(
            sa.update(ChatOrderDraft)
            .where(ChatOrderDraft.tenant_id == uuid.UUID(shop["tenant_id"]))
            .values(status="IGNORED", closed_at=utc_now())
        )
    async with system_session("test: purge") as db:
        await service.purge_expired_data(db, utc_now() + timedelta(days=20))
    assert await _count(ChatIdentity, shop) == 1, "inside the window nothing goes"
    async with system_session("test: purge") as db:
        result = await service.purge_expired_data(db, utc_now() + timedelta(days=40))
    assert result["identities"] >= 1
    assert await _count(ChatMessage, shop) == 0
    assert await _count(ChatOrderDraft, shop) == 0
    assert await _count(ChatIdentity, shop) == 0


# ---------------------------------------------------------- device health ---


async def _age_heartbeat(connection_id: str, hours: int) -> None:
    async with system_session("test: age heartbeat") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(connection_id))
        assert conn is not None
        conn.config = {
            **conn.config,
            "last_heartbeat_at": (utc_now() - timedelta(hours=hours)).isoformat(),
        }


async def _health(client: AsyncClient, shop: dict[str, Any], connection_id: str) -> str:
    hub = await client.get("/v1/integrations", headers=auth_header(shop))
    (item,) = [i for i in hub.json()["items"] if i["id"] == connection_id]
    return str(item["health"])


async def _stale_notices(shop: dict[str, Any]) -> int:
    return await _count(Notification, shop, Notification.kind == "DEVICE_NEEDS_ATTENTION")


async def test_a_silent_phone_needs_attention_and_is_reported_once_a_day(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    from app.intake import jobs as intake_jobs

    shop = await signed_in_shop(client, unique_phone)
    device = await _linked(client, shop)
    beat = await client.post(
        BEAT, json={"listener_state": "ON"}, headers=_key_header(device["ingest_key"])
    )
    assert beat.status_code == 200
    await intake_jobs.scan_device_health()
    assert await _health(client, shop, device["connection_id"]) == "CONNECTED"

    await _age_heartbeat(device["connection_id"], hours=25)
    await intake_jobs.scan_device_health()
    assert await _health(client, shop, device["connection_id"]) == "NEEDS_ATTENTION"
    assert await _stale_notices(shop) == 1
    await intake_jobs.scan_device_health()
    assert await _stale_notices(shop) == 1, "at most one notification per 24 hours"
    async with system_session("test: state") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(device["connection_id"]))
        assert conn is not None and conn.state == "CONNECTED", "the phone can still report back"
        assert conn.last_error_code == "HEARTBEAT_STALE"

    beat = await client.post(
        BEAT, json={"listener_state": "ON"}, headers=_key_header(device["ingest_key"])
    )
    assert beat.status_code == 200
    assert await _health(client, shop, device["connection_id"]) == "CONNECTED"

    # Quiet again the same day: health flips, but no second notification.
    await _age_heartbeat(device["connection_id"], hours=30)
    await intake_jobs.scan_device_health()
    assert await _health(client, shop, device["connection_id"]) == "NEEDS_ATTENTION"
    assert await _stale_notices(shop) == 1


async def test_health_scan_skips_the_share_connection_and_reports_new_links_late(
    client: AsyncClient, unique_phone: str, intake_on: None
):
    from app.intake import jobs as intake_jobs

    shop = await signed_in_shop(client, unique_phone)
    await client.post(
        MANUAL,
        json={"source": "PASTE", "messages": [{"text": "hi"}]},
        headers=auth_header(shop),
    )
    device = await _linked(client, shop)  # never sends a heartbeat
    async with system_session("test: age the links") as db:
        await db.execute(
            sa.update(IntegrationConnection)
            .where(IntegrationConnection.tenant_id == uuid.UUID(shop["tenant_id"]))
            .values(created_at=utc_now() - timedelta(hours=25))
        )
    await intake_jobs.scan_device_health()
    assert await _health(client, shop, device["connection_id"]) == "NEEDS_ATTENTION"
    assert await _stale_notices(shop) == 1
    async with system_session("test: share connection") as db:
        rows = (
            await db.scalars(
                sa.select(IntegrationConnection).where(
                    IntegrationConnection.tenant_id == uuid.UUID(shop["tenant_id"])
                )
            )
        ).all()
        (manual,) = [row for row in rows if row.config.get("platform") == "manual"]
        assert "health" not in manual.config and manual.last_error_code is None


async def test_the_draft_ready_event_has_a_handler(client: AsyncClient, unique_phone: str):
    import app.intake.jobs  # noqa: F401  (registers the handler, as the worker does)
    from app.worker.jobs import HANDLERS

    assert str(OutboxTopic.INTAKE_DRAFT_READY) in HANDLERS
