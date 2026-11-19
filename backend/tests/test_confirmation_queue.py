"""The manual call queue (V4 WP2.3).

``GET /v1/confirmations/queue`` and ``POST /v1/orders/{id}/confirmation/{action}``:
what the queue lists and in which order, quiet hours, the number handed out to
dial, and how the seller's answer moves the confirmation and the order.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.common.audit import AuditAction, AuditLog
from app.common.outbox import OutboxEvent, OutboxTopic
from app.confirmations import manual
from app.confirmations.manual import QuietHours
from app.confirmations.models import ConfirmationAttempt
from app.orders.models import Order
from app.tenants.roles import TenantRole
from tests.conftest_commerce import create_order, grant_plan, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_team import _member_session

#: 12:00 and 23:00 in Dhaka (UTC+6): outside and inside the default 21:00–09:00.
NOON = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
NIGHT = datetime(2026, 11, 19, 17, 0, tzinfo=UTC)


# ---------------------------------------------------------- quiet hours ---


@pytest.mark.parametrize(
    ("start", "end", "dhaka", "active"),
    [
        ("21:00", "09:00", "20:59", False),
        ("21:00", "09:00", "21:00", True),
        ("21:00", "09:00", "02:30", True),
        ("21:00", "09:00", "08:59", True),
        ("21:00", "09:00", "09:00", False),
        ("13:00", "14:00", "13:30", True),
        ("13:00", "14:00", "14:00", False),
        ("13:00", "14:00", "12:59", False),
        ("00:00", "00:00", "03:00", False),
    ],
)
def test_quiet_hours_use_dhaka_wall_time_and_may_wrap_midnight(
    start: str, end: str, dhaka: str, active: bool
):
    hours, minutes = (int(part) for part in dhaka.split(":"))
    # Dhaka is UTC+6 with no daylight saving.
    utc_hour = (hours - 6) % 24
    day = 19 if hours >= 6 else 20
    now = datetime(2026, 11, day, utc_hour, minutes, tzinfo=UTC)
    assert QuietHours(start, end).active(now) is active


def test_quiet_hours_say_when_they_end():
    quiet = QuietHours("21:00", "09:00")
    assert quiet.ends_at(NOON) is None
    # 23:00 Dhaka on the 19th → 09:00 Dhaka on the 20th = 03:00 UTC.
    assert quiet.ends_at(NIGHT) == datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    # 02:00 Dhaka on the 20th (20:00 UTC on the 19th) → the same morning.
    assert quiet.ends_at(datetime(2026, 11, 19, 20, 0, tzinfo=UTC)) == datetime(
        2026, 11, 20, 3, 0, tzinfo=UTC
    )
    assert quiet.as_dict(NIGHT)["active"] is True


# ------------------------------------------------------------------ API ---


@pytest.fixture
async def confirmation_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        yield


@pytest.fixture
def at_noon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manual, "utc_now", lambda: NOON)


def _clock(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr(manual, "utc_now", lambda: moment)


async def _queued(
    client: AsyncClient, shop: dict[str, Any], phone: str, price: int = 125_000
) -> dict[str, Any]:
    """A new customer's order, confirmation requested: the policy queues a call."""
    order = (
        await create_order(
            client,
            shop,
            phone=phone,
            items=[{"name": "Black Abaya", "quantity": 1, "unit_price_paisa": price}],
        )
    )["order"]
    started = await client.post(
        f"/v1/orders/{order['id']}/confirmation",
        json={"mode": "REQUEST"},
        headers=auth_header(shop),
    )
    assert started.status_code == 200, started.text
    assert started.json()["confirmation"]["state"] == "CALL_QUEUED"
    return order


def _act(order: dict[str, Any], action: str) -> str:
    return f"/v1/orders/{order['id']}/confirmation/{action}"


async def _queue(client: AsyncClient, shop: dict[str, Any]) -> dict[str, Any]:
    response = await client.get("/v1/confirmations/queue", headers=auth_header(shop))
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(model: Any, *where: Any) -> list[Any]:
    from app.core.config import get_settings
    from app.db.session import get_sessionmaker
    from app.db.tenancy import mark_session_system

    async with get_sessionmaker(get_settings())() as raw:
        mark_session_system(raw.sync_session, "test: read confirmation rows")
        return list((await raw.scalars(sa.select(model).where(*where))).all())


async def _order(client: AsyncClient, shop: dict[str, Any], order: dict[str, Any]) -> Any:
    return (await client.get(f"/v1/orders/{order['id']}", headers=auth_header(shop))).json()


async def test_the_queue_and_actions_are_off_until_the_flag_is_on(
    client: AsyncClient, unique_phone: str
):
    shop = await signed_in_shop(client, unique_phone)
    order = (await create_order(client, shop, phone="01700000031"))["order"]
    for response in (
        await client.get("/v1/confirmations/queue", headers=auth_header(shop)),
        await client.post(_act(order, "call_now"), headers=auth_header(shop)),
    ):
        assert response.status_code == 403 and response.json()["code"] == "FEATURE_DISABLED"


async def test_the_queue_lists_waiting_calls_by_cod_then_age(
    client: AsyncClient, unique_phone: str, confirmation_on: None
):
    shop = await signed_in_shop(client, unique_phone)
    small = await _queued(client, shop, "01700000032", price=50_000)
    big = await _queued(client, shop, "01700000033", price=200_000)
    older = await _queued(client, shop, "01700000034", price=100_000)
    newer = await _queued(client, shop, "01700000035", price=100_000)
    chat = (await create_order(client, shop, phone="01700000036"))["order"]
    done = await client.post(
        f"/v1/orders/{chat['id']}/confirmation",
        json={"mode": "SELLER_CHAT"},
        headers=auth_header(shop),
    )
    assert done.json()["confirmation"]["state"] == "CONFIRMED"

    body = await _queue(client, shop)

    assert body["voice_available"] is False
    assert set(body["quiet_hours"]) == {"start", "end", "active", "ends_at"}
    ids = [item["order"]["id"] for item in body["items"]]
    assert ids == [big["id"], older["id"], newer["id"], small["id"]]
    first = body["items"][0]
    assert first["confirmation"]["reason_code"] == "VERIFY_NEW"
    assert first["order"]["cod_amount_paisa"] == 200_000
    assert first["order"]["customer_phone_masked"] and "1700000033" not in str(first)
    assert (first["last_call"], first["manual_calls"]) == (None, 0)

    other = await signed_in_shop(client, "018" + unique_phone[3:], shop_name="Other")
    assert (await _queue(client, other))["items"] == []


async def test_call_now_hands_out_the_number_once_per_call_and_tells_nobody(
    client: AsyncClient, unique_phone: str, confirmation_on: None, at_noon: None
):
    shop = await signed_in_shop(client, unique_phone)
    order = await _queued(client, shop, "01700000037")

    called = await client.post(_act(order, "call_now"), headers=auth_header(shop))
    again = await client.post(_act(order, "call_now"), json={}, headers=auth_header(shop))

    assert called.status_code == again.status_code == 200, called.text
    body = called.json()
    assert body["phone"].endswith("1700000037") and body["quiet_override"] is False
    assert body["confirmation"]["state"] == "SELLER_REVIEW"
    assert body["call"]["status"] == "STARTED" and body["call"]["attempt_no"] == 1
    # Dialing again before saying how it went reuses the open call.
    assert again.json()["call"]["attempt_no"] == 1
    assert again.json()["confirmation"]["attempt_count"] == 1

    confirmation_id = uuid.UUID(body["confirmation"]["id"])
    attempts = await _rows(
        ConfirmationAttempt, ConfirmationAttempt.confirmation_id == confirmation_id
    )
    assert [(a.channel, a.status) for a in attempts] == [("MANUAL_CALL", "STARTED")]
    audits = await _rows(AuditLog, AuditLog.tenant_id == uuid.UUID(shop["tenant_id"]))
    actions = [row.action for row in audits]
    assert actions.count(str(AuditAction.CONFIRMATION_CALL_STARTED)) == 2
    assert actions.count(str(AuditAction.PHONE_REVEALED)) == 2
    assert not any("1700000037" in str(row.context) for row in audits)
    # The seller took the row themselves: no "review needed" alert.
    events = await _rows(
        OutboxEvent,
        OutboxEvent.tenant_id == uuid.UUID(shop["tenant_id"]),
        OutboxEvent.topic == str(OutboxTopic.CONFIRMATION_REVIEW_NEEDED),
    )
    assert events == []
    # Still in the queue, now with the open call.
    item = (await _queue(client, shop))["items"][0]
    assert item["last_call"]["status"] == "STARTED" and item["manual_calls"] == 1


async def test_quiet_hours_stop_a_call_unless_the_seller_overrides(
    client: AsyncClient,
    unique_phone: str,
    confirmation_on: None,
    monkeypatch: pytest.MonkeyPatch,
):
    shop = await signed_in_shop(client, unique_phone)
    order = await _queued(client, shop, "01700000038")
    _clock(monkeypatch, NIGHT)

    refused = await client.post(_act(order, "call_now"), headers=auth_header(shop))
    assert refused.status_code == 409
    details = refused.json()["details"]
    assert details["code"] == "CONFIRMATION_QUIET_HOURS"
    assert details["ends_at"] == "2026-11-20T03:00:00+00:00"
    assert refused.json()["message_bn"]

    allowed = await client.post(
        _act(order, "call_now"), json={"override_quiet": True}, headers=auth_header(shop)
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["quiet_override"] is True
    audits = await _rows(
        AuditLog,
        AuditLog.tenant_id == uuid.UUID(shop["tenant_id"]),
        AuditLog.action == str(AuditAction.CONFIRMATION_CALL_STARTED),
    )
    assert [row.context["quiet_override"] for row in audits] == [True]
    assert audits[0].reason == "quiet hours overridden"


async def test_confirm_after_a_call_is_a_manual_call_and_moves_the_order(
    client: AsyncClient, unique_phone: str, confirmation_on: None, at_noon: None
):
    shop = await signed_in_shop(client, unique_phone)
    order = await _queued(client, shop, "01700000039")
    await client.post(_act(order, "call_now"), headers=auth_header(shop))

    first = await client.post(_act(order, "mark_confirmed"), headers=auth_header(shop))
    again = await client.post(_act(order, "mark_confirmed"), headers=auth_header(shop))

    assert first.status_code == again.status_code == 200, first.text
    row = first.json()["confirmation"]
    assert (row["state"], row["method"], row["order_outcome"]) == (
        "CONFIRMED",
        "MANUAL_CALL",
        "APPLIED",
    )
    assert again.json()["confirmation"]["id"] == row["id"]
    assert (await _order(client, shop, order))["status"] == "CONFIRMED"
    attempts = await _rows(
        ConfirmationAttempt, ConfirmationAttempt.confirmation_id == uuid.UUID(row["id"])
    )
    assert [(a.status, a.ended_at is not None) for a in attempts] == [("CONFIRMED", True)]
    assert (await _queue(client, shop))["items"] == []
    # The other answer after it ended is refused, not applied.
    late = await client.post(_act(order, "mark_cancelled"), headers=auth_header(shop))
    assert late.status_code == 409
    assert late.json()["details"]["code"] == "CONFIRMATION_NOT_IN_QUEUE"


@pytest.mark.parametrize(
    ("dial", "method", "reason"),
    [
        (True, "MANUAL_CALL", "CUSTOMER_DECLINED"),
        (False, "SELLER", "CONFIRMATION_CANCELLED_BY_SELLER"),
    ],
)
async def test_cancel_says_who_decided(
    client: AsyncClient,
    unique_phone: str,
    confirmation_on: None,
    at_noon: None,
    dial: bool,
    method: str,
    reason: str,
):
    shop = await signed_in_shop(client, unique_phone)
    order = await _queued(client, shop, "01700000040")
    if dial:
        await client.post(_act(order, "call_now"), headers=auth_header(shop))

    cancelled = await client.post(_act(order, "mark_cancelled"), headers=auth_header(shop))

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["confirmation"]["method"] == method
    (stored,) = await _rows(Order, Order.id == uuid.UUID(order["id"]))
    assert (stored.status, stored.cancellation_reason) == ("CANCELLED", reason)


async def test_no_answer_records_the_call_and_keeps_the_row(
    client: AsyncClient, unique_phone: str, confirmation_on: None, at_noon: None
):
    shop = await signed_in_shop(client, unique_phone)
    dialed = await _queued(client, shop, "01700000041", price=200_000)
    outside = await _queued(client, shop, "01700000042", price=100_000)
    await client.post(_act(dialed, "call_now"), headers=auth_header(shop))

    for order in (dialed, outside):
        response = await client.post(_act(order, "mark_no_answer"), headers=auth_header(shop))
        assert response.status_code == 200, response.text
        row = response.json()["confirmation"]
        assert (row["state"], row["attempt_count"]) == ("SELLER_REVIEW", 1)

    items = (await _queue(client, shop))["items"]
    assert [item["last_call"]["status"] for item in items] == ["NO_ANSWER", "NO_ANSWER"]
    # A second round opens a second call.
    again = await client.post(_act(dialed, "call_now"), headers=auth_header(shop))
    assert again.json()["call"]["attempt_no"] == 2
    events = await _rows(
        OutboxEvent,
        OutboxEvent.tenant_id == uuid.UUID(shop["tenant_id"]),
        OutboxEvent.topic == str(OutboxTopic.CONFIRMATION_NO_ANSWER),
    )
    assert events == []  # no state machine NO_ANSWER: the row never left review


async def test_actions_refuse_orders_outside_the_queue_and_other_shops(
    client: AsyncClient, unique_phone: str, confirmation_on: None, at_noon: None
):
    shop = await signed_in_shop(client, unique_phone)
    plain = (await create_order(client, shop, phone="01700000043"))["order"]
    for action in ("call_now", "mark_confirmed", "mark_cancelled", "mark_no_answer"):
        response = await client.post(_act(plain, action), headers=auth_header(shop))
        assert response.status_code == 409, (action, response.text)
        assert response.json()["details"] == {"code": "CONFIRMATION_NOT_IN_QUEUE", "state": None}

    order = await _queued(client, shop, "01700000044")
    other = await signed_in_shop(client, "018" + unique_phone[3:], shop_name="Other")
    for action in ("call_now", "mark_confirmed"):
        response = await client.post(_act(order, action), headers=auth_header(other))
        assert response.status_code == 404
    unknown = await client.post(_act(order, "call_later"), headers=auth_header(shop))
    assert unknown.status_code == 422
    extra = await client.post(_act(order, "call_now"), json={"x": 1}, headers=auth_header(shop))
    assert extra.status_code == 422


async def test_dialing_is_rate_limited_per_user(
    client: AsyncClient,
    unique_phone: str,
    confirmation_on: None,
    at_noon: None,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(manual, "CALLS_PER_WINDOW", 2)
    shop = await signed_in_shop(client, unique_phone)
    order = await _queued(client, shop, "01700000045")
    for _ in range(2):
        ok = await client.post(_act(order, "call_now"), headers=auth_header(shop))
        assert ok.status_code == 200, ok.text
    limited = await client.post(_act(order, "call_now"), headers=auth_header(shop))
    assert limited.status_code == 429


async def test_order_staff_work_the_queue_and_viewers_cannot(
    client: AsyncClient, unique_phone: str, confirmation_on: None, at_noon: None
):
    owner = await signed_in_shop(client, unique_phone)
    await grant_plan(str(owner["tenant_id"]), "pro")
    order = await _queued(client, owner, "01700000046")
    staff = await _member_session(client, owner, "01700000047", TenantRole.ORDER_OPERATOR)
    viewer = await _member_session(client, owner, "01700000048", TenantRole.VIEWER)

    assert len((await _queue(client, staff))["items"]) == 1
    called = await client.post(_act(order, "call_now"), headers=auth_header(staff))
    assert called.status_code == 200 and called.json()["phone"].endswith("1700000046")

    for response in (
        await client.get("/v1/confirmations/queue", headers=auth_header(viewer)),
        await client.post(_act(order, "call_now"), headers=auth_header(viewer)),
    ):
        assert response.status_code == 403
