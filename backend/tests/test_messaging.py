import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.core.clock import utc_now
from app.db.session import system_session
from app.messaging import jobs, service
from app.messaging.models import Message, MessageAttempt
from app.notifications.transport import DeliveryOutcome, TransportResult
from app.tenants.models import TenantUser
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header


async def test_viewer_cannot_send_or_manage_channels(client, unique_phone, monkeypatch):
    shop, order, contact, headers = await setup(client, unique_phone, monkeypatch)
    async with system_session("test role") as db:
        membership = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        membership.role = "VIEWER"
    assert (
        await client.post("/v1/messaging/messages", headers=headers, json=payload(order, contact))
    ).status_code == 403
    assert (
        await client.post("/v1/messaging/channels/EMAIL", headers=headers, json={"enabled": False})
    ).status_code == 403


async def setup(client, phone, monkeypatch):
    shop = await signed_in_shop(client, phone)
    order = (await create_order(client, shop))["order"]
    headers = auth_header(shop)
    monkeypatch.setattr(service, "capability", lambda _: (True, None))
    monkeypatch.setattr(jobs, "capability", lambda _: (True, None))
    assert (
        await client.post("/v1/messaging/channels/EMAIL", headers=headers, json={"enabled": True})
    ).status_code == 200
    contact = await client.post(
        "/v1/messaging/conversations",
        headers=headers,
        json={
            "customer_id": order["customer_id"],
            "recipient": "buyer@example.com",
            "consent": True,
            "evidence": "Customer request",
        },
    )
    assert contact.status_code == 201, contact.text
    return shop, order, contact.json(), headers


def payload(order, contact):
    return {
        "conversation_id": contact["id"],
        "order_id": order["id"],
        "template_key": "order_update",
        "locale": "bn",
        "idempotency_key": str(uuid.uuid4()),
    }


async def test_send_dedupe_conflict_and_cross_shop(client, unique_phone, monkeypatch):
    _shop, order, contact, headers = await setup(client, unique_phone, monkeypatch)
    body = payload(order, contact)
    first = await client.post("/v1/messaging/messages", headers=headers, json=body)
    assert first.status_code == 202, first.text
    assert "অর্ডার" in first.json()["subject"]
    replay = await client.post("/v1/messaging/messages", headers=headers, json=body)
    assert replay.json()["id"] == first.json()["id"]
    assert (
        await client.post("/v1/messaging/messages", headers=headers, json={**body, "locale": "en"})
    ).status_code == 409
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (await client.get("/v1/messaging/messages", headers=auth_header(other))).json()[
        "items"
    ] == []
    assert (
        await client.post("/v1/messaging/messages", headers=auth_header(other), json=body)
    ).status_code == 404
    assert "recipient_enc" not in contact


async def test_opt_out_after_queue_suppresses_delivery(client, unique_phone, monkeypatch):
    shop, order, contact, headers = await setup(client, unique_phone, monkeypatch)
    message = (
        await client.post("/v1/messaging/messages", headers=headers, json=payload(order, contact))
    ).json()
    assert (
        await client.patch(
            f"/v1/messaging/conversations/{contact['id']}/consent",
            headers=headers,
            json={"consent": False, "evidence": "Customer opted out"},
        )
    ).status_code == 200
    await jobs.deliver_message(uuid.UUID(shop["tenant_id"]), uuid.UUID(message["id"]))
    rows = (await client.get("/v1/messaging/messages", headers=headers)).json()["items"]
    assert rows[0]["status"] == "SUPPRESSED"
    assert (
        await client.post("/v1/messaging/messages", headers=headers, json=payload(order, contact))
    ).status_code == 409


async def test_retry_uses_same_provider_key_and_stops_when_sent(client, unique_phone, monkeypatch):
    shop, order, contact, headers = await setup(client, unique_phone, monkeypatch)
    calls = []

    class Transport:
        async def send(self, message):
            calls.append(message)
            return TransportResult(
                DeliveryOutcome.FAILED if len(calls) == 1 else DeliveryOutcome.SENT,
                "test",
                provider_reference="ref",
            )

    monkeypatch.setattr(jobs, "get_email_transport", lambda: Transport())
    message = (
        await client.post("/v1/messaging/messages", headers=headers, json=payload(order, contact))
    ).json()
    args = uuid.UUID(shop["tenant_id"]), uuid.UUID(message["id"])
    await jobs.deliver_message(*args)
    async with system_session("test retry clock") as db:
        row = await db.get(Message, args[1])
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
    await jobs.deliver_message(*args)
    await jobs.deliver_message(*args)
    assert len(calls) == 2
    assert calls[0].idempotency_key == calls[1].idempotency_key
    assert calls[0].text_body == calls[1].text_body
    async with system_session("test attempts") as db:
        assert (
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(MessageAttempt)
                .where(MessageAttempt.message_id == args[1])
            )
            == 2
        )


async def test_expired_provider_window_never_resends(client, unique_phone, monkeypatch):
    shop, order, contact, headers = await setup(client, unique_phone, monkeypatch)
    message = (
        await client.post("/v1/messaging/messages", headers=headers, json=payload(order, contact))
    ).json()
    async with system_session("test crashed delivery") as db:
        row = await db.get(Message, uuid.UUID(message["id"]))
        row.status = "SENDING"
        row.first_attempt_at = utc_now() - timedelta(hours=24)
    await jobs.deliver_message(uuid.UUID(shop["tenant_id"]), uuid.UUID(message["id"]))
    result = (await client.get("/v1/messaging/messages", headers=headers)).json()["items"][0]
    assert result["status"] == "UNKNOWN"
    assert (
        await client.post(f"/v1/messaging/messages/{message['id']}/retry", headers=headers)
    ).status_code == 409


@pytest.mark.parametrize("channel", ["WHATSAPP", "MESSENGER", "SMS"])
async def test_unavailable_official_channels_are_disabled(client, unique_phone, channel):
    shop = await signed_in_shop(client, unique_phone)
    response = await client.post(
        f"/v1/messaging/channels/{channel}", headers=auth_header(shop), json={"enabled": True}
    )
    assert response.status_code == 409
    assert response.json()["details"]["blocker"] == f"{channel}_OFFICIAL_PROVIDER_REQUIRED"


async def test_template_validation_and_order_link(client, unique_phone, monkeypatch):
    shop, _order, contact, headers = await setup(client, unique_phone, monkeypatch)
    other_order = (await create_order(client, shop, phone="01912345678"))["order"]
    assert (
        await client.post(
            "/v1/messaging/messages", headers=headers, json=payload(other_order, contact)
        )
    ).status_code == 422
    response = await client.post(
        "/v1/messaging/templates",
        headers=headers,
        json={**service.BUILTIN, "key": "invalid", "body_en": "{customer.__class__}"},
    )
    assert response.status_code == 422
