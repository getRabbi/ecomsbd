"""Tracking SMS: the messaging SMS channel through Alpha SMS (V4 WP7.1).

Fake HTTP only (``httpx.MockTransport``); numbers are ``017000000xx``. SMS is
available only with the flag ``alpha_sms_enabled`` AND the admin-console
gateway account on, and a shop sends only after switching its own SMS channel
on (default off). The gateway takes no idempotency key: a send that may have
left is UNKNOWN and is never sent again.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.automation import jobs as automation_jobs
from app.automation.schemas import RecipeInput
from app.core.clock import utc_now
from app.db.session import system_session
from app.messaging import jobs
from app.messaging.models import Message
from app.sms_providers import service as sms_service
from tests.conftest import _global_flag_on
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_sms_alpha import (  # noqa: F401  (fixtures)
    KEY,
    _connect,
    _fake,
    _form,
    alpha_flag,
    clean_sms,
    sms_admin,
)

CUSTOMER = "01700000071"
OK = {"error": 0, "msg": "Request successfully submitted", "data": {"request_id": 4321}}
TEMPLATE = {
    "key": "sms_update",
    "purpose": "TRANSACTIONAL",
    "channel": "SMS",
    "body_en": "{shop_name}: order {order_number} is on its way.",
    "body_bn": "{shop_name}: order {order_number} pathano hoyeche.",
}


async def _gateway(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, answers: list[Any]
) -> list[httpx.Request]:
    """Connect the console account and answer the gateway's HTTP in order."""
    seen: list[httpx.Request] = []
    replies = iter(answers)

    def answer(request: httpx.Request) -> httpx.Response:
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    real = sms_service.AlphaSmsTransport

    def with_fake(*args: Any, **kwargs: Any) -> Any:
        return real(*args, client=_fake(answer, seen), **kwargs)

    monkeypatch.setattr(sms_service, "AlphaSmsTransport", with_fake)
    assert (await _connect(client, credentials={"api_key": KEY}, enabled=True)).status_code == 200
    return seen


async def _sms_shop(client: AsyncClient, phone: str) -> tuple[dict, dict, dict, dict]:
    """A shop with SMS on, an SMS order-update template and an SMS contact."""
    shop = await signed_in_shop(client, phone)
    headers = auth_header(shop)
    order = (await create_order(client, shop, phone=CUSTOMER))["order"]
    enabled = await client.post(
        "/v1/messaging/channels/SMS", headers=headers, json={"enabled": True}
    )
    assert enabled.status_code == 200, enabled.text
    saved = await client.post("/v1/messaging/templates", headers=headers, json=TEMPLATE)
    assert saved.status_code == 201, saved.text
    contact = await client.post(
        "/v1/messaging/conversations",
        headers=headers,
        json={
            "customer_id": order["customer_id"],
            "channel": "SMS",
            "use_customer_phone": True,
            "consent": True,
            "evidence": "Customer asked for SMS updates",
        },
    )
    assert contact.status_code == 201, contact.text
    return shop, headers, order, contact.json()


async def _queue(client: AsyncClient, headers: dict, order: dict, contact: dict) -> dict:
    response = await client.post(
        "/v1/messaging/messages",
        headers=headers,
        json={
            "conversation_id": contact["id"],
            "order_id": order["id"],
            "template_key": "sms_update",
            "locale": "en",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _deliver(shop: dict, message: dict, *, due: bool = False) -> Message:
    message_id = uuid.UUID(message["id"])
    if due:
        async with system_session("test: message due") as db:
            row = await db.get(Message, message_id)
            assert row is not None
            row.next_attempt_at = utc_now() - timedelta(seconds=1)
    await jobs.deliver_message(uuid.UUID(shop["tenant_id"]), message_id)
    async with system_session("test: read message") as db:
        row = await db.get(Message, message_id)
        assert row is not None
        return row


def _sms_row(listing: dict) -> dict:
    return next(item for item in listing["items"] if item["kind"] == "SMS")


# --------------------------------------------------------------- gates ---


async def test_sms_needs_the_flag_the_account_and_the_shop_switch(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    order = (await create_order(client, shop, phone=CUSTOMER))["order"]

    row = _sms_row((await client.get("/v1/messaging/channels", headers=headers)).json())
    assert row["available"] is False and row["blocker"] == "SMS_OFFICIAL_PROVIDER_REQUIRED"
    assert row["enabled"] is False and row["contact"] is True and row["reports"] == []

    await _gateway(client, monkeypatch, [])
    refused = await client.post(
        "/v1/messaging/channels/SMS", headers=headers, json={"enabled": True}
    )  # account on, flag still off (default)
    assert refused.status_code == 409
    assert refused.json()["details"]["blocker"] == "SMS_OFFICIAL_PROVIDER_REQUIRED"

    async for _ in _global_flag_on(settings, "alpha_sms_enabled"):
        row = _sms_row((await client.get("/v1/messaging/channels", headers=headers)).json())
        assert row["available"] is True and row["blocker"] is None
        assert row["enabled"] is False  # the shop's own switch stays off by default
        assert (
            await client.post("/v1/messaging/templates", headers=headers, json=TEMPLATE)
        ).status_code == 201
        contact = await client.post(
            "/v1/messaging/conversations",
            headers=headers,
            json={
                "customer_id": order["customer_id"],
                "channel": "SMS",
                "use_customer_phone": True,
                "consent": True,
                "evidence": "Customer asked for SMS updates",
            },
        )
        body = {
            "conversation_id": contact.json()["id"],
            "order_id": order["id"],
            "template_key": "sms_update",
            "locale": "en",
            "idempotency_key": str(uuid.uuid4()),
        }
        held = await client.post("/v1/messaging/messages", headers=headers, json=body)
        assert held.status_code == 409 and held.json()["details"]["blocker"] == "CHANNEL_DISABLED"
        assert (
            await client.post("/v1/messaging/channels/SMS", headers=headers, json={"enabled": True})
        ).status_code == 200
        assert (
            await client.post("/v1/messaging/messages", headers=headers, json=body)
        ).status_code == 202


async def test_sms_templates_and_contacts_are_checked(client: AsyncClient, unique_phone: str):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    order = (await create_order(client, shop, phone=CUSTOMER))["order"]

    offer = await client.post(
        "/v1/messaging/templates",
        headers=headers,
        json={
            **TEMPLATE,
            "key": "sms_offer",
            "purpose": "MARKETING",
            "body_en": "Hi from {shop_name}",
            "body_bn": "{shop_name} theke offer",
        },
    )
    assert offer.status_code == 422 and offer.json()["details"]["code"] == "SMS_ORDER_UPDATES_ONLY"
    long_bn = "আপনার অর্ডার পাঠানো হয়েছে। " * 10  # > 3 UCS-2 parts
    too_long = await client.post(
        "/v1/messaging/templates", headers=headers, json={**TEMPLATE, "body_bn": long_bn}
    )
    assert too_long.status_code == 422 and too_long.json()["details"]["code"] == "SMS_TOO_LONG"
    saved = await client.post(
        "/v1/messaging/templates",
        headers=headers,
        json={**TEMPLATE, "subject_en": "ignored", "subject_bn": "ignored"},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["subject_en"] == "" and saved.json()["provider_name"] is None

    contact = {
        "customer_id": order["customer_id"],
        "channel": "SMS",
        "consent": True,
        "evidence": "Customer asked for SMS updates",
    }
    bad = await client.post(
        "/v1/messaging/conversations", headers=headers, json={**contact, "recipient": "12345"}
    )
    assert bad.status_code == 422
    missing = await client.post("/v1/messaging/conversations", headers=headers, json=contact)
    assert missing.status_code == 422  # neither a number nor the saved phone
    typed = await client.post(
        "/v1/messaging/conversations",
        headers=headers,
        json={**contact, "recipient": "01700000072"},
    )
    assert typed.status_code == 201, typed.text
    assert typed.json()["recipient_masked"] == "01700****72"
    assert "01700000072" not in typed.text


def test_the_tracking_recipe_and_step_accept_sms() -> None:
    assert RecipeInput.model_validate({"channel": "SMS"}).channel == "SMS"


# ------------------------------------------------------------- sending ---


async def test_an_order_update_goes_out_once_through_alpha_sms(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen = await _gateway(client, monkeypatch, [httpx.Response(200, json=OK)])
    shop, headers, order, contact = await _sms_shop(client, unique_phone)
    assert contact["recipient_masked"] == "01700****71" and contact["channel"] == "SMS"
    message = await _queue(client, headers, order, contact)
    assert message["channel"] == "SMS" and message["subject"] == ""
    assert order["order_number"] in message["body"] and "is on its way" in message["body"]

    caplog.set_level(logging.DEBUG)
    row = await _deliver(shop, message)
    assert row.status == "SENT" and row.provider == "alpha_sms"
    assert row.provider_reference == "4321" and row.last_error is None
    (request,) = seen
    form = _form(request)
    assert form["to"] == "8801700000071" and form["msg"] == message["body"]
    assert await _deliver(shop, message, due=True) is not None
    assert len(seen) == 1  # SENT is final
    for secret in (KEY, "8801700000071", CUSTOMER, "is on its way"):
        assert secret not in caplog.text


async def test_a_lost_answer_is_unknown_and_never_resent(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = await _gateway(client, monkeypatch, [httpx.ReadTimeout("no answer")])
    shop, headers, order, contact = await _sms_shop(client, unique_phone)
    message = await _queue(client, headers, order, contact)
    row = await _deliver(shop, message)
    assert row.status == "UNKNOWN" and row.last_error == "ALPHA_SMS_NO_ANSWER"
    await _deliver(shop, message, due=True)
    assert len(seen) == 1
    retry = await client.post(f"/v1/messaging/messages/{message['id']}/retry", headers=headers)
    assert retry.status_code == 409


async def test_a_provable_non_send_is_retried(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    low_balance = httpx.Response(200, json={"error": 417, "msg": "Insufficient balance"})
    seen = await _gateway(client, monkeypatch, [low_balance, httpx.Response(200, json=OK)])
    shop, headers, order, contact = await _sms_shop(client, unique_phone)
    message = await _queue(client, headers, order, contact)
    assert (await _deliver(shop, message)).status == "RETRY"
    row = await _deliver(shop, message, due=True)
    assert row.status == "SENT" and row.attempts == 2
    assert len(seen) == 2 and _form(seen[0])["msg"] == _form(seen[1])["msg"]


async def test_a_rejected_send_fails_without_a_retry(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = httpx.Response(200, json={"error": 420, "msg": "Content blocked"})
    seen = await _gateway(client, monkeypatch, [blocked])
    shop, headers, order, contact = await _sms_shop(client, unique_phone)
    message = await _queue(client, headers, order, contact)
    row = await _deliver(shop, message)
    assert row.status == "FAILED" and row.failed_at is not None
    await _deliver(shop, message, due=True)
    assert len(seen) == 1


async def test_a_crash_mid_send_is_unknown_never_resent(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = await _gateway(client, monkeypatch, [])
    shop, headers, order, contact = await _sms_shop(client, unique_phone)
    message = await _queue(client, headers, order, contact)
    async with system_session("test: crashed sms") as db:
        row = await db.get(Message, uuid.UUID(message["id"]))
        assert row is not None
        row.status, row.first_attempt_at = "SENDING", utc_now()
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
    row = await _deliver(shop, message)
    assert row.status == "UNKNOWN" and row.last_error == "SEND_INTERRUPTED"
    assert seen == []


async def test_the_gateway_switched_off_after_queueing_sends_nothing(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = await _gateway(client, monkeypatch, [])
    shop, headers, order, contact = await _sms_shop(client, unique_phone)
    message = await _queue(client, headers, order, contact)
    assert (await _connect(client, enabled=False)).status_code == 200
    row = await _deliver(shop, message)
    assert row.status == "FAILED" and row.last_error == "CHANNEL_DISABLED"
    assert seen == []


async def test_the_tracking_step_sends_by_sms(
    client: AsyncClient,
    unique_phone: str,
    sms_admin: Any,  # noqa: F811
    clean_sms: None,  # noqa: F811
    alpha_flag: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = await _gateway(client, monkeypatch, [httpx.Response(200, json=OK)])
    shop, headers, _order, _contact = await _sms_shop(client, unique_phone)
    rule = await client.post(
        "/v1/automation/rules",
        headers=headers,
        json={
            "name": "Order update by SMS",
            "trigger": "order.created",
            "action": "SEND_TEMPLATE",
            "enabled": True,
            "config": {"template_key": "sms_update", "locale": "en", "channel": "SMS"},
        },
    )
    assert rule.status_code == 201, rule.text
    order = (await create_order(client, shop, phone=CUSTOMER))["order"]
    executions = (await client.get("/v1/automation/executions", headers=headers)).json()["items"]
    await automation_jobs.execute(uuid.UUID(shop["tenant_id"]), uuid.UUID(executions[0]["id"]))
    async with system_session("test: automation sms") as db:
        queued = await db.scalar(
            sa.select(Message).where(Message.order_id == uuid.UUID(order["id"]))
        )
        assert queued is not None and queued.channel == "SMS"
    row = await _deliver(shop, {"id": str(queued.id)})
    assert row.status == "SENT" and len(seen) == 1
    assert order["order_number"] in _form(seen[0])["msg"]
