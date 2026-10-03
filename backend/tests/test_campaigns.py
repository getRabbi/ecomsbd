"""V3.3 messaging & campaigns: consent separation, sending, receipts and flows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from app.core.clock import utc_now
from app.db.session import system_session
from app.integrations import http
from app.integrations.http import Reply
from app.messaging import campaign_jobs, jobs, service
from app.messaging import campaigns as engine
from app.messaging.models import Campaign, ConsentEvent, Conversation, Message
from app.notifications.transport import MockEmailTransport
from app.orders.models import Order
from tests.conftest_commerce import create_order, signed_in_shop
from tests.integrations_fakes import live_settings
from tests.test_auth_flow import auth_header
from tests.test_integrations import _role
from tests.test_rto import _Shop

MARKETING_TEMPLATE = {
    "key": "eid_offer",
    "purpose": "MARKETING",
    "channel": "EMAIL",
    "subject_en": "Eid offer from {shop_name}",
    "subject_bn": "{shop_name} এর ঈদ অফার",
    "body_en": "Hi {customer_name}, 10% off this week.",
    "body_bn": "প্রিয় {customer_name}, এই সপ্তাহে ১০% ছাড়।",
}


@pytest.fixture
def email(monkeypatch):
    transport = MockEmailTransport()
    monkeypatch.setattr(service, "capability", lambda _: (True, None))
    monkeypatch.setattr(jobs, "capability", lambda _: (True, None))
    monkeypatch.setattr(jobs, "get_email_transport", lambda: transport)
    monkeypatch.setattr(engine, "quiet", lambda _now: False)
    monkeypatch.setattr(live_settings(), "public_base_url", "https://api.ecomsbd.test")
    return transport


async def _shop(client, phone):
    shop = await signed_in_shop(client, phone)
    headers = auth_header(shop)
    assert (
        await client.post("/v1/messaging/channels/EMAIL", headers=headers, json={"enabled": True})
    ).status_code == 200
    assert (
        await client.post("/v1/messaging/templates", headers=headers, json=MARKETING_TEMPLATE)
    ).status_code == 201
    return shop, headers


async def _contact(client, shop, *, phone, address, marketing=True):
    order = (await create_order(client, shop, phone=phone))["order"]
    response = await client.post(
        "/v1/messaging/conversations",
        headers=auth_header(shop),
        json={
            "customer_id": order["customer_id"],
            "recipient": address,
            "consent": True,
            "marketing_consent": marketing,
            "evidence": "Ticked the offers box at checkout",
        },
    )
    assert response.status_code == 201, response.text
    return order, response.json()


async def _campaign(client, headers, **overrides: Any) -> dict[str, Any]:
    body = {
        "name": "Eid",
        "channel": "EMAIL",
        "template_key": "eid_offer",
        "locale": "en",
        **overrides,
    }
    response = await client.post("/v1/campaigns", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _launch(client, headers, campaign, **extra: Any):
    return await client.post(
        f"/v1/campaigns/{campaign['id']}/launch",
        headers=headers,
        json={"version": campaign["version"], **extra},
    )


async def _tick(shop):
    """One scheduler pass, then every due message through the delivery job."""
    tenant = uuid.UUID(shop["tenant_id"])
    await campaign_jobs.run_campaigns()
    async with system_session("test: due messages") as db:
        ids = (
            await db.scalars(
                sa.select(Message.id).where(
                    Message.tenant_id == tenant, Message.status.in_(["QUEUED", "RETRY"])
                )
            )
        ).all()
    for message_id in ids:
        await jobs.deliver_message(tenant, message_id)
    # The next minute's pass sees the sends finished and closes the campaign.
    await campaign_jobs.run_campaigns()


async def test_marketing_needs_its_own_consent_and_sends_with_unsubscribe(
    client, unique_phone, email
):
    shop, headers = await _shop(client, unique_phone)
    await _contact(client, shop, phone="01711111111", address="yes@example.com")
    await _contact(client, shop, phone="01722222222", address="no@example.com", marketing=False)
    estimate = await client.post(
        "/v1/campaigns/estimate", headers=headers, json={"channel": "EMAIL", "audience": {}}
    )
    assert estimate.json()["matched"] == 2
    assert estimate.json()["reachable"] == 1
    assert estimate.json()["excluded"]["NO_MARKETING_CONSENT"] == 1

    campaign = await _campaign(client, headers)
    launched = await _launch(client, headers, campaign)
    assert launched.status_code == 200, launched.text
    assert launched.json()["status"] == "SENDING"
    await _tick(shop)

    assert len(email.sent) == 1
    sent = email.sent[0]
    assert sent.to_address == "yes@example.com"
    assert sent.subject == "Eid offer from Test Shop"
    assert "/v1/messaging/unsubscribe/" in sent.text_body
    assert sent.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    link = sent.headers["List-Unsubscribe"].strip("<>")
    assert link in sent.text_body

    detail = (await client.get(f"/v1/campaigns/{campaign['id']}", headers=headers)).json()
    assert detail["campaign"]["status"] == "COMPLETED"
    analytics = detail["analytics"]
    assert analytics["sent"] == 1
    assert analytics["recipients"]["skipped_by"] == {"NO_MARKETING_CONSENT": 1}
    # Without the Resend webhook secret, delivery is not reported, not zero.
    assert analytics["delivered"] is None and analytics["reports"] == []

    # Scanners prefetch links: a GET only shows the confirmation.
    path = link.removeprefix("https://api.ecomsbd.test")
    page = await client.get(path)
    assert page.status_code == 200 and "Unsubscribe" in page.text
    async with system_session("test") as db:
        row = await db.scalar(
            sa.select(Conversation).where(Conversation.recipient_masked == "y***@example.com")
        )
        assert row.marketing_consent
    done = await client.post(path, data={"List-Unsubscribe": "One-Click"})
    assert done.status_code == 200
    async with system_session("test") as db:
        row = await db.scalar(
            sa.select(Conversation).where(Conversation.recipient_masked == "y***@example.com")
        )
        assert not row.marketing_consent and row.marketing_opted_out_at is not None
        # Order updates are a separate consent and keep working.
        assert row.consent
        event = await db.scalar(
            sa.select(ConsentEvent).where(
                ConsentEvent.conversation_id == row.id, ConsentEvent.source == "UNSUBSCRIBE_LINK"
            )
        )
        assert event.scope == "MARKETING" and str(event.campaign_id) == campaign["id"]
    detail = (await client.get(f"/v1/campaigns/{campaign['id']}", headers=headers)).json()
    assert detail["analytics"]["opt_outs"] == 1
    # An unknown token looks exactly like a valid one.
    assert (await client.post("/v1/messaging/unsubscribe/nope")).status_code == 200


async def test_opt_out_after_queueing_wins_and_frequency_cap_holds(client, unique_phone, email):
    shop, headers = await _shop(client, unique_phone)
    _order, contact = await _contact(client, shop, phone="01733333333", address="a@example.com")
    first = await _campaign(client, headers)
    assert (await _launch(client, headers, first)).status_code == 200
    await campaign_jobs.run_campaigns()  # queued, not yet sent
    opted = await client.patch(
        f"/v1/messaging/conversations/{contact['id']}/marketing-consent",
        headers=headers,
        json={"consent": False, "evidence": "Asked us on the phone to stop offers"},
    )
    assert opted.status_code == 200 and not opted.json()["marketing_consent"]
    await _tick(shop)
    assert email.sent == []
    rows = (
        await client.get(
            "/v1/messaging/messages", headers=headers, params={"campaign_id": first["id"]}
        )
    ).json()["items"]
    assert rows[0]["status"] == "SUPPRESSED" and rows[0]["last_error"] == "OPTED_OUT"

    # Consent again, then a second campaign inside the frequency window is skipped.
    await client.patch(
        f"/v1/messaging/conversations/{contact['id']}/marketing-consent",
        headers=headers,
        json={"consent": True, "evidence": "Customer asked to rejoin offers"},
    )
    # The cap counts every queued marketing message, even one later suppressed.
    capped = await _campaign(client, headers, name="Capped")
    await _launch(client, headers, capped)
    await _tick(shop)
    assert email.sent == []
    async with system_session("test") as db:
        row = await db.get(Conversation, uuid.UUID(contact["id"]))
        row.last_marketing_at = utc_now() - timedelta(days=4)
    second = await _campaign(client, headers, name="Second")
    await _launch(client, headers, second)
    await _tick(shop)
    assert len(email.sent) == 1
    third = await _campaign(client, headers, name="Third")
    await _launch(client, headers, third)
    await _tick(shop)
    assert len(email.sent) == 1
    detail = (await client.get(f"/v1/campaigns/{third['id']}", headers=headers)).json()
    assert detail["analytics"]["recipients"]["skipped_by"] == {"FREQUENCY_CAP": 1}


async def test_pause_holds_cancel_stops_and_roles(client, unique_phone, email):
    shop, headers = await _shop(client, unique_phone)
    await _contact(client, shop, phone="01744444444", address="p@example.com")
    campaign = await _campaign(client, headers)
    await _launch(client, headers, campaign)
    await campaign_jobs.run_campaigns()

    await _role(shop, "ORDER_OPERATOR")
    operator = auth_header(shop)
    assert (
        await client.post(f"/v1/campaigns/{campaign['id']}/pause", headers=operator)
    ).status_code == 200
    # Operators can stop a send; only the owner can restart or build one.
    assert (
        await client.post(f"/v1/campaigns/{campaign['id']}/resume", headers=operator)
    ).status_code == 403
    assert (
        await client.post(
            "/v1/campaigns",
            headers=operator,
            json={"name": "x", "channel": "EMAIL", "template_key": "eid_offer"},
        )
    ).status_code == 403
    await _tick(shop)
    assert email.sent == []
    async with system_session("test") as db:
        held = await db.scalar(
            sa.select(Message).where(Message.campaign_id == uuid.UUID(campaign["id"]))
        )
        assert held.status == "QUEUED" and held.attempts == 0 and held.first_attempt_at is None

    await _role(shop, "OWNER")
    assert (
        await client.post(f"/v1/campaigns/{campaign['id']}/cancel", headers=headers)
    ).status_code == 200
    async with system_session("test") as db:
        held = await db.get(Message, held.id)
        held.next_attempt_at = utc_now() - timedelta(seconds=1)
    await _tick(shop)
    assert email.sent == []
    async with system_session("test") as db:
        assert (await db.get(Message, held.id)).status == "CANCELLED"

    await _role(shop, "VIEWER")
    viewer = auth_header(shop)
    assert (await client.get("/v1/campaigns", headers=viewer)).status_code == 200
    assert (
        await client.post(f"/v1/campaigns/{campaign['id']}/pause", headers=viewer)
    ).status_code == 403


async def test_separation_isolation_and_scheduling(client, unique_phone, email):
    shop, headers = await _shop(client, unique_phone)
    # A transactional template never carries a campaign ...
    wrong = await client.post(
        "/v1/campaigns",
        headers=headers,
        json={"name": "x", "channel": "EMAIL", "template_key": "order_update"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["details"]["code"] == "TEMPLATE_PURPOSE_MISMATCH"
    # ... a marketing template never uses an order number ...
    bad = await client.post(
        "/v1/messaging/templates",
        headers=headers,
        json={**MARKETING_TEMPLATE, "key": "bad_offer", "body_en": "Order {order_number}"},
    )
    assert bad.status_code == 422
    # ... and an automation rule on an order event cannot send marketing.
    rule = await client.post(
        "/v1/automation/rules",
        headers=headers,
        json={
            "name": "Upsell",
            "trigger": "order.created",
            "action": "SEND_TEMPLATE",
            "config": {"template_key": "eid_offer", "locale": "bn", "channel": "EMAIL"},
        },
    )
    assert rule.status_code == 422

    await _contact(client, shop, phone="01755555555", address="s@example.com")
    campaign = await _campaign(client, headers)
    later = utc_now() + timedelta(hours=2)
    scheduled = await _launch(client, headers, campaign, scheduled_at=later.isoformat())
    assert scheduled.json()["status"] == "SCHEDULED"
    # Launching is bound to the version that was reviewed.
    stale = await client.post(
        f"/v1/campaigns/{campaign['id']}/launch",
        headers=headers,
        json={"version": campaign["version"] + 1},
    )
    assert stale.status_code == 409 and stale.json()["details"]["code"] == "STALE"
    await _tick(shop)
    assert email.sent == []
    async with system_session("test") as db:
        row = await db.get(Campaign, uuid.UUID(campaign["id"]))
        row.scheduled_at = utc_now() - timedelta(minutes=1)
    await _tick(shop)
    assert len(email.sent) == 1

    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (
        await client.get(f"/v1/campaigns/{campaign['id']}", headers=auth_header(other))
    ).status_code == 404
    assert (await client.get("/v1/campaigns", headers=auth_header(other))).json()["items"] == []


async def test_quiet_hours_hold_marketing(client, unique_phone, email, monkeypatch):
    shop, headers = await _shop(client, unique_phone)
    await _contact(client, shop, phone="01766666666", address="q@example.com")
    campaign = await _campaign(client, headers)
    await _launch(client, headers, campaign)
    monkeypatch.setattr(engine, "quiet", lambda _now: True)
    await _tick(shop)
    assert email.sent == []
    detail = (await client.get(f"/v1/campaigns/{campaign['id']}", headers=headers)).json()
    assert detail["campaign"]["status"] == "SENDING"
    assert detail["analytics"]["recipients"]["PENDING"] == 1
    night = utc_now().replace(hour=17, minute=0)  # 23:00 in Dhaka
    assert engine.quiet.__name__ == "<lambda>"
    monkeypatch.undo()
    assert engine.quiet(night)
    assert not engine.quiet(night.replace(hour=5))  # 11:00 in Dhaka


async def test_resend_receipts_bounces_and_complaints(client, unique_phone, email, monkeypatch):
    secret = base64.b64encode(b"resend-webhook-key").decode()
    monkeypatch.setattr(live_settings(), "email_webhook_secret", SecretStr(f"whsec_{secret}"))
    shop, headers = await _shop(client, unique_phone)
    await _contact(client, shop, phone="01777777777", address="r@example.com")
    await _contact(client, shop, phone="01788888888", address="b@example.com")
    campaign = await _campaign(client, headers)
    await _launch(client, headers, campaign)
    await _tick(shop)
    assert len(email.sent) == 2
    async with system_session("test") as db:
        rows = (
            await db.scalars(
                sa.select(Message).where(Message.campaign_id == uuid.UUID(campaign["id"]))
            )
        ).all()
        # The mock transport stands in for Resend here, with Resend-style ids.
        for row in rows:
            row.provider, row.provider_reference = "resend", f"re_{uuid.uuid4().hex}"
        refs = [row.provider_reference for row in rows]

    def signed(payload: dict[str, Any], *, stamp: int | None = None) -> tuple[bytes, dict]:
        body = json.dumps(payload).encode()
        msg_id, ts = "msg_" + uuid.uuid4().hex, str(stamp or int(time.time()))
        mac = hmac.new(b"resend-webhook-key", f"{msg_id}.{ts}.".encode() + body, hashlib.sha256)
        return body, {
            "svix-id": msg_id,
            "svix-timestamp": ts,
            "svix-signature": "v1," + base64.b64encode(mac.digest()).decode(),
        }

    ref_ok, ref_bounce = sorted(refs)
    body, sig = signed({"type": "email.delivered", "data": {"email_id": ref_ok}})
    assert (
        await client.post("/v1/webhooks/messaging/resend", content=body, headers=sig)
    ).status_code == 200
    forged = {**sig, "svix-signature": "v1,AAAA"}
    assert (
        await client.post("/v1/webhooks/messaging/resend", content=body, headers=forged)
    ).status_code == 401
    old, old_sig = signed(
        {"type": "email.opened", "data": {"email_id": ref_ok}}, stamp=int(time.time()) - 3600
    )
    assert (
        await client.post("/v1/webhooks/messaging/resend", content=old, headers=old_sig)
    ).status_code == 401
    for kind, ref in (
        ("email.opened", ref_ok),
        ("email.delivered", ref_ok),  # late duplicate: never moves a status back
        ("email.bounced", ref_bounce),
        ("email.complained", ref_ok),
    ):
        body, sig = signed({"type": kind, "data": {"email_id": ref}})
        assert (
            await client.post("/v1/webhooks/messaging/resend", content=body, headers=sig)
        ).status_code == 200
    async with system_session("test") as db:
        ok = await db.scalar(sa.select(Message).where(Message.provider_reference == ref_ok))
        bounced = await db.scalar(
            sa.select(Message).where(Message.provider_reference == ref_bounce)
        )
        assert ok.status == "READ" and ok.delivered_at and ok.read_at
        assert bounced.status == "FAILED" and bounced.last_error == "BOUNCED"
        bounced_contact = await db.get(Conversation, bounced.conversation_id)
        assert bounced_contact.undeliverable_reason == "BOUNCED"
        complained = await db.get(Conversation, ok.conversation_id)
        assert not complained.marketing_consent
    analytics = (await client.get(f"/v1/campaigns/{campaign['id']}", headers=headers)).json()[
        "analytics"
    ]
    assert analytics["delivered"] == 1 and analytics["read"] == 1 and analytics["failed"] == 1
    assert analytics["opt_outs"] == 1
    assert {"code": "BOUNCED", "count": 1} in analytics["errors"]


async def test_orders_after_a_campaign_are_counted_not_claimed(client, unique_phone, email):
    shop, headers = await _shop(client, unique_phone)
    await _contact(client, shop, phone="01799999999", address="o@example.com")
    campaign = await _campaign(client, headers, attribution_days=3)
    await _launch(client, headers, campaign)
    await _tick(shop)
    await create_order(client, shop, phone="01799999999")
    later = (await create_order(client, shop, phone="01799999999"))["order"]
    async with system_session("test") as db:
        row = await db.get(Order, uuid.UUID(later["id"]))
        row.created_at = utc_now() + timedelta(days=5)  # outside the window
    detail = (await client.get(f"/v1/campaigns/{campaign['id']}", headers=headers)).json()
    after = detail["analytics"]["orders_after"]
    assert after["window_days"] == 3 and after["customers"] == 1 and after["orders"] == 1
    assert after["order_value_paisa"] > 0


# -------------------------------------------------------------------- flows ---


async def test_win_back_flow_enrols_once_per_lapse(client, unique_phone, email, monkeypatch):
    shop, headers = await _shop(client, unique_phone)
    rto = _Shop(client, shop)
    lapsed = await rto.parcel("DELIVERED", phone="01811111111")
    recent = await rto.parcel("DELIVERED", phone="01822222222")
    never = await rto.parcel("RETURNED", phone="01833333333")
    async with system_session("test") as db:
        for placed in (lapsed, never):
            row = await db.get(Order, uuid.UUID(placed["order"]["id"]))
            row.created_at = utc_now() - timedelta(days=90)
    for placed, address in ((lapsed, "l@example.com"), (recent, "r@example.com")):
        await client.post(
            "/v1/messaging/conversations",
            headers=headers,
            json={
                "customer_id": placed["order"]["customer_id"],
                "recipient": address,
                "consent": True,
                "marketing_consent": True,
                "evidence": "Opted in to offers",
            },
        )
    estimate = await client.post(
        "/v1/campaigns/estimate",
        headers=headers,
        json={"channel": "EMAIL", "flow": "WIN_BACK", "flow_days": 60},
    )
    # Only the lapsed buyer who once received a parcel; never the one who did not.
    assert estimate.json()["matched"] == 1 and estimate.json()["reachable"] == 1
    flow = await _campaign(client, headers, kind="FLOW", flow="WIN_BACK", flow_days=60)
    assert flow["flow_days"] == 60
    activated = await _launch(client, headers, flow)
    assert activated.json()["status"] == "ACTIVE"
    monkeypatch.setattr(engine, "FLOW_HOUR", 0)
    await _tick(shop)
    assert [m.to_address for m in email.sent] == ["l@example.com"]
    # The same day, and again tomorrow: the same lapse is never messaged twice.
    await _tick(shop)
    async with system_session("test") as db:
        row = await db.get(Campaign, uuid.UUID(flow["id"]))
        row.last_run_at = utc_now() - timedelta(days=1)
    await _tick(shop)
    assert len(email.sent) == 1
    detail = (await client.get(f"/v1/campaigns/{flow['id']}", headers=headers)).json()
    assert detail["campaign"]["status"] == "ACTIVE"
    assert detail["campaign"]["total_recipients"] == 1


# ----------------------------------------------------------------- WhatsApp ---


class FakeGraph:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.status = "APPROVED"

    async def send(self, method: str, url: str, **kw: Any) -> Reply:
        path = url.split("graph.facebook.com/", 1)[1].split("/", 1)[1]
        if method == "GET" and path == "1234567890":
            return Reply(
                200,
                {"id": "1234567890", "display_phone_number": "+880 1700", "verified_name": "Shop"},
                {},
            )
        if method == "POST" and path == "9876543210/subscribed_apps":
            return Reply(200, {"success": True}, {})
        if method == "GET" and path == "9876543210/message_templates":
            return Reply(
                200,
                {
                    "data": [
                        {
                            "name": "eid_offer_wa",
                            "language": "bn",
                            "status": self.status,
                            "category": "MARKETING",
                        }
                    ]
                },
                {},
            )
        if method == "POST" and path == "1234567890/messages":
            self.sent.append(kw["json_body"])
            return Reply(
                200,
                {"messaging_product": "whatsapp", "messages": [{"id": f"wamid.{len(self.sent)}"}]},
                {},
            )
        return Reply(404, {"error": {"code": 100}}, {})


def _meta_signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(b"meta-secret", body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": "sha256=" + digest}


async def test_whatsapp_campaign_receipts_and_stop(client, unique_phone, email, monkeypatch):
    monkeypatch.setattr(live_settings(), "meta_whatsapp_manual_enabled", True)
    settings = live_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", SecretStr("meta-secret"))
    monkeypatch.setattr(settings, "meta_webhook_verify_token", SecretStr("verify-me"))
    graph = FakeGraph()
    monkeypatch.setattr(http, "send", graph.send)
    shop, headers = await _shop(client, unique_phone)

    # Channel stays off until this shop links its own WhatsApp Business number.
    blocked = await client.post(
        "/v1/messaging/channels/WHATSAPP", headers=headers, json={"enabled": True}
    )
    assert blocked.json()["details"]["blocker"] == "WHATSAPP_CONNECTION_REQUIRED"
    conn = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "WHATSAPP", "name": "WA"}
        )
    ).json()["connection"]
    linked = await client.post(
        f"/v1/integrations/{conn['id']}/whatsapp",
        headers=headers,
        json={
            "phone_number_id": "1234567890",
            "waba_id": "9876543210",
            "access_token": "EAAG" + "x" * 40,
        },
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["connection"]["state"] == "CONNECTED"
    assert "access_token" not in linked.text
    assert (
        await client.post(
            "/v1/messaging/channels/WHATSAPP", headers=headers, json={"enabled": True}
        )
    ).status_code == 200

    template = {
        "key": "eid_offer_wa",
        "purpose": "MARKETING",
        "channel": "WHATSAPP",
        "body_en": "Hi {customer_name}, Eid offer at {shop_name}. Reply STOP to opt out.",
        "body_bn": "প্রিয় {customer_name}, {shop_name} এ ঈদ অফার। বন্ধ করতে STOP লিখুন।",
        "provider_name": "eid_offer_wa",
        "provider_language_bn": "bn",
        "variables": ["customer_name", "shop_name"],
    }
    saved = await client.post("/v1/messaging/templates", headers=headers, json=template)
    assert saved.status_code == 201 and saved.json()["provider_status"] == "UNCHECKED"
    order = (await create_order(client, shop, phone="01912121212"))["order"]
    contact = await client.post(
        "/v1/messaging/conversations",
        headers=headers,
        json={
            "customer_id": order["customer_id"],
            "channel": "WHATSAPP",
            "use_customer_phone": True,
            "consent": True,
            "marketing_consent": True,
            "evidence": "Opted in on WhatsApp chat",
        },
    )
    assert contact.status_code == 201, contact.text
    assert contact.json()["recipient_masked"] == "01912****12"
    campaign = await _campaign(
        client, headers, channel="WHATSAPP", template_key="eid_offer_wa", locale="bn"
    )
    unapproved = await _launch(client, headers, campaign)
    assert unapproved.json()["details"]["blocker"] == "WHATSAPP_TEMPLATE_NOT_APPROVED"
    synced = await client.post(f"/v1/integrations/{conn['id']}/whatsapp/templates", headers=headers)
    assert synced.json() == {"updated": 1}
    assert (await _launch(client, headers, campaign)).status_code == 200
    await _tick(shop)
    assert len(graph.sent) == 1
    wire = graph.sent[0]
    assert wire["to"] == "8801912121212" and wire["type"] == "template"
    assert wire["template"]["name"] == "eid_offer_wa"
    assert wire["template"]["language"] == {"code": "bn"}
    params = wire["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params][1] == "Test Shop"

    def change(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "9876543210",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {"metadata": {"phone_number_id": "1234567890"}, **value},
                        }
                    ],
                }
            ],
        }

    for status in ("delivered", "read", "delivered"):
        body, sig = _meta_signed(
            change(
                {
                    "statuses": [
                        {"id": "wamid.1", "status": status, "timestamp": str(int(time.time()))}
                    ]
                }
            )
        )
        assert (
            await client.post("/v1/webhooks/integrations/meta", content=body, headers=sig)
        ).status_code == 200
    forged, _sig = _meta_signed(change({"messages": []}))
    assert (
        await client.post(
            "/v1/webhooks/integrations/meta",
            content=forged,
            headers={"X-Hub-Signature-256": "sha256=00"},
        )
    ).status_code == 401
    body, sig = _meta_signed(
        change(
            {
                "messages": [
                    {
                        "from": "8801912121212",
                        "id": "wamid.in",
                        "type": "text",
                        "text": {"body": " Stop "},
                    }
                ]
            }
        )
    )
    await client.post("/v1/webhooks/integrations/meta", content=body, headers=sig)
    detail = (await client.get(f"/v1/campaigns/{campaign['id']}", headers=headers)).json()
    assert detail["analytics"]["read"] == 1 and detail["analytics"]["delivered"] == 1
    async with system_session("test") as db:
        row = await db.get(Conversation, uuid.UUID(contact.json()["id"]))
        assert not row.marketing_consent and row.consent  # STOP ends offers only
        event = await db.scalar(
            sa.select(ConsentEvent).where(
                ConsentEvent.conversation_id == row.id, ConsentEvent.source == "KEYWORD"
            )
        )
        assert event is not None and event.actor_id is None
        stored = await db.scalar(sa.select(Message).where(Message.provider_reference == "wamid.1"))
        assert "Stop" not in stored.body  # inbound text is never stored

    # A timeout is ambiguous: WhatsApp has no idempotency key, so no blind resend.
    await client.patch(
        f"/v1/messaging/conversations/{contact.json()['id']}/marketing-consent",
        headers=headers,
        json={"consent": True, "evidence": "Rejoined offers in person"},
    )

    async def timeout(*_a: Any, **_k: Any) -> Reply:
        raise http.ProviderError("PROVIDER_TIMEOUT")

    monkeypatch.setattr(http, "send", timeout)
    again = await _campaign(
        client,
        headers,
        name="Again",
        channel="WHATSAPP",
        template_key="eid_offer_wa",
        locale="bn",
        frequency_cap_hours=24,
    )
    async with system_session("test") as db:
        row = await db.get(Conversation, uuid.UUID(contact.json()["id"]))
        row.last_marketing_at = utc_now() - timedelta(days=2)
    await _launch(client, headers, again)
    await _tick(shop)
    rows = (
        await client.get(
            "/v1/messaging/messages", headers=headers, params={"campaign_id": again["id"]}
        )
    ).json()["items"]
    assert rows[0]["status"] == "UNKNOWN" and rows[0]["last_error"] == "PROVIDER_TIMEOUT"


async def test_privacy_deletion_scrubs_messaging(client, unique_phone, email):
    from app.privacy.service import PrivacyService

    shop, _headers = await _shop(client, unique_phone)
    await _contact(client, shop, phone="01710101010", address="gone@example.com")
    tenant = uuid.UUID(shop["tenant_id"])
    async with system_session("test: anonymise") as db:
        await PrivacyService(db)._anonymise_customers(tenant)
    async with system_session("test") as db:
        row = await db.scalar(sa.select(Conversation).where(Conversation.tenant_id == tenant))
        assert row.recipient_enc == "" and row.recipient_hash is None
        assert not row.marketing_consent
