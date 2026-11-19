"""The SMS confirm link (V4 WP2.3).

After the last unanswered call the customer gets one SMS with a link; the
public page reads without changing anything and takes one answer. Links
expire after six hours and every failure hands the row to the seller.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.common.outbox import OutboxEvent, OutboxTopic
from app.confirmations import jobs, links
from app.confirmations.links import FollowUp, format_taka, link_text
from app.confirmations.models import (
    AttemptChannel,
    ConfirmationAttempt,
    ConfirmationLink,
    ConfirmationState,
    OrderConfirmation,
)
from app.confirmations.service import open_for_order
from app.confirmations.state_machine import Cause
from app.core.clock import DHAKA, utc_now
from app.core.context import RequestContext, clear_context, set_context
from app.db.session import session_scope
from app.notifications.transport import (
    DeliveryOutcome,
    MockSmsTransport,
    SmsMessage,
    TransportResult,
    count_sms_segments,
)
from app.orders.models import Order
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header

BASE = "https://app.ecomsbd.com"
#: Tomorrow 12:00 and 23:00 in Dhaka: after any retry wait, outside / inside
#: the default 21:00–09:00 quiet hours.
_TOMORROW = (utc_now() + timedelta(days=1)).astimezone(DHAKA)
NOON = _TOMORROW.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC)
NIGHT = _TOMORROW.replace(hour=23, minute=0, second=0, microsecond=0).astimezone(UTC)

S = ConfirmationState

_TOKEN = re.compile(r"/c/([A-Za-z0-9_-]{22})$")


# ------------------------------------------------------------------ text ---


def test_the_link_text_is_banglish_and_one_gsm_segment():
    token = "A" * 22
    text = link_text("Rupkotha Fashion House BD", 125_000, f"{BASE}/c/{token}")
    assert text == (f"Rupkotha Fashion House B: order Tk 1,250. Confirm/cancel: {BASE}/c/{token}")
    assert count_sms_segments(text) == 1 and "৳" not in text
    assert format_taka(125_050) == "1,250.50" and format_taka(0) == "0"
    assert link_text("  ", 50_000, "u").startswith("ecomsbd: order Tk 500.")


def test_the_token_is_22_url_safe_characters_derived_per_link():
    from app.api.deps import get_hasher

    hasher = get_hasher()
    first, second = uuid.uuid4(), uuid.uuid4()
    token = hasher.confirmation_link_token(first)
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", token)
    assert hasher.confirmation_link_token(first) == token  # a retried send: same link
    assert hasher.confirmation_link_token(second) != token
    assert links.hash_token(token) == hashlib.sha256(token.encode()).hexdigest()


# ----------------------------------------------------------- the flow ---


@pytest.fixture
async def engine_on(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_ENGINE_ENABLED)):
        yield


@pytest.fixture
async def link_on(settings: Any, engine_on: None) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_SMS_LINK_ENABLED)):
        yield


@pytest.fixture
def sms(monkeypatch: pytest.MonkeyPatch) -> MockSmsTransport:
    transport = MockSmsTransport()
    monkeypatch.setattr(links, "sms_transport", lambda: transport)
    monkeypatch.setattr(links, "public_base", lambda: BASE)
    return transport


class _Tenant:
    def __init__(self, shop: dict[str, Any]) -> None:
        self.id = uuid.UUID(shop["tenant_id"])

    def __enter__(self) -> None:
        self._token = set_context(RequestContext(trace_id="test", tenant_id=self.id))

    def __exit__(self, *_: object) -> None:
        clear_context(self._token)


async def _unanswered(
    client: AsyncClient, shop: dict[str, Any], phone: str, *, calls: int = 2
) -> uuid.UUID:
    """An order whose confirmation rang out ``calls`` times (as the IVR would)."""
    order = (await create_order(client, shop, phone=phone))["order"]
    started = await client.post(
        f"/v1/orders/{order['id']}/confirmation",
        json={"mode": "REQUEST"},
        headers=auth_header(shop),
    )
    assert started.json()["confirmation"]["state"] == "CALL_QUEUED", started.text
    with _Tenant(shop):
        async with session_scope() as db:
            linker, _customers, _orders = jobs._services(db)
            svc = linker._confirmations
            row = await open_for_order(db, uuid.UUID(order["id"]), lock=True)
            assert row is not None
            for call in range(calls):
                if call:
                    await svc.transition(row, S.CALL_QUEUED, Cause.RETRY, now=row.next_action_at)
                await svc.add_attempt(row, AttemptChannel.IVR, provider="fake")
                await svc.transition(row, S.CALLING, Cause.DIALING)
                await svc.transition(row, S.NO_ANSWER, Cause.CALL_NO_ANSWER)
            return row.id


async def _follow(shop: dict[str, Any], confirmation_id: uuid.UUID, now: datetime) -> FollowUp:
    with _Tenant(shop):
        async with session_scope() as db:
            row = await db.scalar(
                sa.select(OrderConfirmation)
                .where(OrderConfirmation.id == confirmation_id)
                .with_for_update()
            )
            linker, _customers, _orders = jobs._services(db)
            return await linker.follow_up(row, now=now)


async def _row(shop: dict[str, Any], model: Any, *where: Any) -> list[Any]:
    with _Tenant(shop):
        async with session_scope() as db:
            return list((await db.scalars(sa.select(model).where(*where))).all())


async def _events(
    shop: dict[str, Any], topic: OutboxTopic, confirmation_id: uuid.UUID
) -> list[OutboxEvent]:
    rows = await _row(shop, OutboxEvent, OutboxEvent.topic == str(topic))
    return [row for row in rows if row.payload.get("confirmation_id") == str(confirmation_id)]


async def _send(shop: dict[str, Any], confirmation_id: uuid.UUID) -> None:
    """Run the link's outbox handler (twice: a retried event sends once)."""
    from app.worker.jobs import HANDLERS

    (event,) = await _events(shop, OutboxTopic.CONFIRMATION_LINK_REQUESTED, confirmation_id)
    with _Tenant(shop):
        for _ in range(2):
            async with session_scope() as db:
                await HANDLERS[event.topic](db, event)


def _token(message: SmsMessage) -> str:
    found = _TOKEN.search(message.body)
    assert found, message.body
    return found.group(1)


def _page(token: str) -> str:
    return f"/v1/public/confirmations/{token}"


_SHAPE = {
    "status",
    "result",
    "shop_name",
    "items",
    "item_count",
    "cod_amount_paisa",
    "phone_masked",
    "expires_at",
}


async def test_after_the_last_unanswered_call_the_customer_confirms_by_link(
    client: AsyncClient,
    unique_phone: str,
    link_on: None,
    sms: MockSmsTransport,
    monkeypatch: pytest.MonkeyPatch,
):
    shop = await signed_in_shop(client, unique_phone, shop_name="Rupkotha")
    confirmation_id = await _unanswered(client, shop, "01700000051")

    monkeypatch.setattr(jobs, "utc_now", lambda: NOON)
    counts = await jobs.follow_up_confirmations()
    assert counts["LINK"] >= 1 and counts["failed"] == 0
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SMS_LINK_SENT" and row.next_action_at == NOON + timedelta(hours=6)

    await _send(shop, confirmation_id)
    (message,) = sms.sent
    token = _token(message)
    assert message.body == f"Rupkotha: order Tk 1,250. Confirm/cancel: {BASE}/c/{token}"
    assert message.phone_e164.endswith("1700000051")
    (link,) = await _row(
        shop, ConfirmationLink, ConfirmationLink.confirmation_id == confirmation_id
    )
    assert link.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(link.__dict__)
    attempts = await _row(
        shop, ConfirmationAttempt, ConfirmationAttempt.confirmation_id == confirmation_id
    )
    sms_attempt = [a for a in attempts if a.channel == "SMS_LINK"]
    assert [(a.attempt_no, a.status, a.segments, a.provider) for a in sms_attempt] == [
        (3, "SENT", 1, "mock")
    ]

    # Opening the link (or a previewer fetching it) changes nothing.
    for _ in range(2):
        shown = await client.get(_page(token))
        assert shown.status_code == 200 and shown.headers["cache-control"] == "no-store"
    body = shown.json()
    assert set(body) == _SHAPE
    assert (body["status"], body["shop_name"], body["cod_amount_paisa"]) == (
        "OPEN",
        "Rupkotha",
        125_000,
    )
    assert body["items"] == [{"name": "Black Abaya XL", "variant": None, "quantity": 1}]
    assert body["phone_masked"] and "1700000051" not in str(body)
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SMS_LINK_SENT"

    answered = await client.post(_page(token), json={"result": "confirm"})
    late = await client.post(_page(token), json={"result": "cancel"})
    assert answered.status_code == late.status_code == 200, answered.text
    assert (answered.json()["status"], answered.json()["result"]) == ("ANSWERED", "CONFIRM")
    assert late.json() == answered.json()  # one use: the first answer stands
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert (row.state, row.method, row.order_outcome, row.resolved_by) == (
        "CONFIRMED",
        "SMS_LINK",
        "APPLIED",
        None,
    )
    (order,) = await _row(shop, Order, Order.id == row.order_id)
    assert order.status == "CONFIRMED"

    unknown = await client.get(_page("B" * 22))
    assert unknown.status_code == 200 and unknown.json() == {
        **dict.fromkeys(_SHAPE),
        "status": "UNAVAILABLE",
        "items": [],
        "item_count": 0,
    }
    posted = await client.post(_page("B" * 22), json={"result": "confirm"})
    assert posted.status_code == 200 and posted.json() == unknown.json()
    assert (await client.post(_page(token), json={"result": "maybe"})).status_code == 422


async def test_a_cancel_by_link_cancels_the_order_as_the_customer(
    client: AsyncClient, unique_phone: str, link_on: None, sms: MockSmsTransport
):
    shop = await signed_in_shop(client, unique_phone)
    confirmation_id = await _unanswered(client, shop, "01700000052")
    assert await _follow(shop, confirmation_id, NOON) is FollowUp.LINK
    await _send(shop, confirmation_id)

    response = await client.post(_page(_token(sms.sent[0])), json={"result": "cancel"})

    assert response.json()["result"] == "CANCEL"
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    (order,) = await _row(shop, Order, Order.id == row.order_id)
    assert (order.status, order.cancellation_reason) == ("CANCELLED", "CUSTOMER_DECLINED")


async def test_an_unanswered_link_expires_to_the_seller(
    client: AsyncClient, unique_phone: str, link_on: None, sms: MockSmsTransport
):
    shop = await signed_in_shop(client, unique_phone)
    confirmation_id = await _unanswered(client, shop, "01700000053")
    assert await _follow(shop, confirmation_id, NOON) is FollowUp.LINK
    await _send(shop, confirmation_id)
    token = _token(sms.sent[0])

    assert await _follow(shop, confirmation_id, NOON + timedelta(hours=5)) is FollowUp.WAITING
    assert await _follow(shop, confirmation_id, NOON + timedelta(hours=6)) is FollowUp.EXPIRED

    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SELLER_REVIEW"
    for response in (
        await client.get(_page(token)),
        await client.post(_page(token), json={"result": "confirm"}),
    ):
        assert response.json()["status"] == "UNAVAILABLE"
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SELLER_REVIEW"
    review = await _events(shop, OutboxTopic.CONFIRMATION_REVIEW_NEEDED, confirmation_id)
    assert [event.payload["cause"] for event in review] == ["LINK_EXPIRED"]


async def test_quiet_hours_hold_the_sms_until_morning(
    client: AsyncClient, unique_phone: str, link_on: None, sms: MockSmsTransport
):
    shop = await signed_in_shop(client, unique_phone)
    confirmation_id = await _unanswered(client, shop, "01700000054")

    assert await _follow(shop, confirmation_id, NIGHT) is FollowUp.QUIET
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "NO_ANSWER" and sms.sent == []
    assert await _follow(shop, confirmation_id, NOON + timedelta(days=1)) is FollowUp.LINK


async def test_before_the_last_call_the_sweep_calls_again(
    client: AsyncClient, unique_phone: str, link_on: None, sms: MockSmsTransport
):
    shop = await signed_in_shop(client, unique_phone)
    confirmation_id = await _unanswered(client, shop, "01700000055", calls=1)

    assert await _follow(shop, confirmation_id, utc_now()) is FollowUp.WAITING
    assert await _follow(shop, confirmation_id, NOON) is FollowUp.RETRY
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "CALL_QUEUED" and sms.sent == []


@pytest.mark.parametrize("missing", ["flag", "transport", "url"])
async def test_without_the_flag_a_transport_or_a_url_the_seller_gets_the_row(
    client: AsyncClient,
    unique_phone: str,
    engine_on: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
):
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    shop = await signed_in_shop(client, unique_phone)
    confirmation_id = await _unanswered(client, shop, "01700000056")
    transport = MockSmsTransport()
    monkeypatch.setattr(links, "sms_transport", lambda: transport)
    monkeypatch.setattr(links, "public_base", lambda: BASE)
    if missing == "transport":
        monkeypatch.setattr(links, "sms_transport", lambda: _Disabled())
    if missing == "url":
        monkeypatch.setattr(links, "public_base", lambda: None)

    if missing == "flag":
        outcome = await _follow(shop, confirmation_id, NOON)
    else:
        async for _ in _global_flag_on(settings, str(FlagKey.CONFIRMATION_SMS_LINK_ENABLED)):
            outcome = await _follow(shop, confirmation_id, NOON)

    assert outcome is FollowUp.REVIEW and transport.sent == []
    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SELLER_REVIEW"


class _Disabled:
    name = "disabled"
    is_configured = False

    async def send(self, message: SmsMessage) -> TransportResult:  # pragma: no cover
        raise AssertionError("must not send")


class _Rejecting:
    name = "rejecting"
    is_configured = True

    async def send(self, message: SmsMessage) -> TransportResult:
        return TransportResult(DeliveryOutcome.REJECTED, self.name, detail="bad number")


async def test_a_refused_send_kills_the_link_and_asks_the_seller(
    client: AsyncClient, unique_phone: str, link_on: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(links, "sms_transport", lambda: _Rejecting())
    monkeypatch.setattr(links, "public_base", lambda: BASE)
    shop = await signed_in_shop(client, unique_phone)
    confirmation_id = await _unanswered(client, shop, "01700000057")
    assert await _follow(shop, confirmation_id, NOON) is FollowUp.LINK

    await _send(shop, confirmation_id)

    (row,) = await _row(shop, OrderConfirmation, OrderConfirmation.id == confirmation_id)
    assert row.state == "SELLER_REVIEW"
    (attempt,) = await _row(
        shop,
        ConfirmationAttempt,
        ConfirmationAttempt.confirmation_id == confirmation_id,
        ConfirmationAttempt.channel == "SMS_LINK",
    )
    assert attempt.status == "REJECTED" and attempt.ended_at is not None


async def test_the_public_routes_are_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from app.api.v1 import confirmations_public

    monkeypatch.setattr(confirmations_public, "ANSWERS_PER_MINUTE", 2)
    token = uuid.uuid4().hex[:22]
    statuses = [
        (await client.post(_page(token), json={"result": "confirm"})).status_code for _ in range(3)
    ]
    assert statuses == [200, 200, 429]
