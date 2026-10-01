"""Chat-to-order: Meta webhooks in, one seller-reviewed draft out, one order on confirm."""

from __future__ import annotations

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

from app.chat_orders import jobs, service
from app.chat_orders.models import ChatAttention, ChatIdentity, ChatMessage, ChatOrderDraft
from app.core.clock import utc_now
from app.db.session import system_session
from app.integrations.models import IntegrationConnection
from app.notifications.models import Notification
from app.orders.models import Order
from app.orders.parser import DeterministicOrderParser
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.integrations_fakes import live_settings
from tests.test_auth_flow import auth_header

PAGE = "1122334455"
WA_NUMBER = "5566778899"
SECRET = b"meta-secret"
CHAT = ["কালো পাঞ্জাবিটা লাগবে", "XL দুইটা", "01712345678", "মিরপুর ১০ ঢাকা"]


def _account_id() -> str:
    return str(uuid.uuid4().int)[:12]


@pytest.fixture(autouse=True)
def _fresh_accounts(monkeypatch):
    # One Page or number links to one shop across the whole (shared) test
    # database, so every test gets its own.
    monkeypatch.setitem(globals(), "PAGE", _account_id())
    monkeypatch.setitem(globals(), "WA_NUMBER", _account_id())


@pytest.fixture(autouse=True)
def _meta_configured(monkeypatch):
    settings = live_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", SecretStr("meta-secret"))
    monkeypatch.setattr(settings, "meta_webhook_verify_token", SecretStr("verify-me"))


async def _connect(shop: dict[str, Any], provider: str, account: str, state="CONNECTED") -> str:
    async with system_session("test: chat connection") as db:
        conn = IntegrationConnection(
            tenant_id=uuid.UUID(shop["tenant_id"]),
            provider=provider,
            name=provider.title(),
            state=state,
            account_id=account,
            account_key=account if state != "DISCONNECTED" else None,
            account_name="Test Page",
            config={},
            created_by=uuid.uuid4(),
        )
        db.add(conn)
        await db.flush()
        return str(conn.id)


def _signed(payload: dict[str, Any], secret: bytes = SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": "sha256=" + digest}


def _messenger(
    text: str | None,
    *,
    mid: str,
    sender: str = "psid-1",
    page: str | None = None,
    at: float | None = None,
    echo: bool = False,
    attachment: str | None = None,
) -> dict[str, Any]:
    page = page or PAGE
    message: dict[str, Any] = {"mid": mid}
    if text is not None:
        message["text"] = text
    if echo:
        message["is_echo"] = True
    if attachment:
        message["attachments"] = [{"type": attachment, "payload": {"url": "https://x/y"}}]
    return {
        "object": "page",
        "entry": [
            {
                "id": page,
                "time": int(time.time() * 1000),
                "messaging": [
                    {
                        "sender": {"id": page if echo else sender},
                        "recipient": {"id": sender if echo else page},
                        "timestamp": int((at or time.time()) * 1000),
                        "message": message,
                    }
                ],
            }
        ],
    }


def _whatsapp(value: dict[str, Any], number: str | None = None) -> dict[str, Any]:
    number = number or WA_NUMBER
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": number},
                            **value,
                        },
                    }
                ],
            }
        ],
    }


def _wa_text(text: str, *, wamid: str, sender: str = "8801812345678", at: float | None = None):
    return {
        "contacts": [{"wa_id": sender, "profile": {"name": "Rahim"}}],
        "messages": [
            {
                "from": sender,
                "id": wamid,
                "timestamp": str(int(at or time.time())),
                "type": "text",
                "text": {"body": text},
            }
        ],
    }


async def _post(client, payload: dict[str, Any], *, secret: bytes = SECRET) -> int:
    body, headers = _signed(payload, secret)
    response = await client.post("/v1/webhooks/integrations/meta", content=body, headers=headers)
    return response.status_code


async def _send_chat(client, lines: list[str], *, sender="psid-1", prefix="m", start=None):
    base = start or time.time()
    for index, line in enumerate(lines):
        assert (
            await _post(
                client,
                _messenger(line, mid=f"{prefix}.{index}", sender=sender, at=base + index * 30),
            )
            == 200
        )
    await jobs.process_chat_messages()


async def _drafts(shop) -> list[ChatOrderDraft]:
    async with system_session("test: drafts") as db:
        return list(
            (
                await db.scalars(
                    sa.select(ChatOrderDraft)
                    .where(ChatOrderDraft.tenant_id == uuid.UUID(shop["tenant_id"]))
                    .order_by(ChatOrderDraft.created_at)
                )
            ).all()
        )


async def _count(model, shop) -> int:
    async with system_session("test: count") as db:
        return int(
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(model)
                .where(model.tenant_id == uuid.UUID(shop["tenant_id"]))
            )
            or 0
        )


async def _ready_notifications(shop) -> int:
    async with system_session("test: notifications") as db:
        return int(
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(Notification)
                .where(
                    Notification.tenant_id == uuid.UUID(shop["tenant_id"]),
                    Notification.kind == "CHAT_ORDER_READY",
                )
            )
            or 0
        )


async def _panjabi_catalogue(client, shop) -> dict[str, Any]:
    product = await create_product(
        client, shop, name="Black Panjabi", price_paisa=92_500, opening_stock=0
    )
    for name in ("Black / XL", "Black / L"):
        created = await client.post(
            f"/v1/products/{product['id']}/variants",
            json={"name": name, "opening_stock": 5},
            headers=auth_header(shop),
        )
        assert created.status_code == 201, created.text
        product = created.json()
    return product


# ------------------------------------------------------------ Messenger ---


async def test_four_messages_become_one_ready_draft_with_one_notification(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    product = await _panjabi_catalogue(client, shop)

    await _send_chat(client, CHAT[:2])
    drafts = await _drafts(shop)
    assert len(drafts) == 1 and drafts[0].status == "NEEDS_INFO"
    assert set(drafts[0].missing_fields) == {"phone", "address"}
    assert await _ready_notifications(shop) == 0

    await _send_chat(client, CHAT[2:], prefix="n", start=time.time() + 60)
    drafts = await _drafts(shop)
    assert len(drafts) == 1, "a burst of messages is one candidate order"
    draft = drafts[0]
    assert draft.status == "READY_FOR_REVIEW" and draft.message_count == 4
    assert draft.selected_phone == "+8801712345678"
    assert draft.address == "মিরপুর 10 ঢাকা"
    (item,) = draft.items
    assert (item["quantity"], item["size"], item["color"]) == (2, "XL", "কালো")
    assert item["match"]["status"] == "MATCHED"
    assert item["match"]["product_id"] == product["id"]
    assert item["match"]["variant_status"] == "MATCHED"
    assert item["match"]["variant_name"] == "Black / XL"
    assert draft.cod_amount_paisa == 185_000 and draft.cod_source == "CATALOG"
    assert await _ready_notifications(shop) == 1

    # The same parser as "paste a message": the transcript reads the same way.
    async with system_session("test: transcript") as db:
        db_draft = await db.get(ChatOrderDraft, draft.id)
        text = await service.transcript(db, db_draft)
    assert text.splitlines() == CHAT
    parsed = DeterministicOrderParser().parse(text)
    assert parsed.selected_phone == draft.selected_phone and parsed.address == draft.address

    # A later message updates the same draft silently: no second push.
    await _send_chat(client, ["নাম রহিম"], prefix="o", start=time.time() + 200)
    assert len(await _drafts(shop)) == 1
    assert await _ready_notifications(shop) == 1

    # No order exists until the seller confirms.
    assert await _count(Order, shop) == 0


async def test_messenger_capture_rules(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)

    # Invalid signature: refused, nothing stored.
    assert await _post(client, _messenger("hello", mid="bad.1"), secret=b"wrong") == 401
    # The Page's own reply (echo) is never a customer message.
    assert await _post(client, _messenger("কত দাম?", mid="echo.1", echo=True)) == 200
    # A Page no shop has connected.
    assert await _post(client, _messenger("01712345678", mid="other.1", page=_account_id())) == 200
    assert await _count(ChatMessage, shop) == 0

    # A retry of the same delivery is stored once.
    for _ in range(2):
        assert await _post(client, _messenger("01712345678", mid="dup.1")) == 200
    assert await _count(ChatMessage, shop) == 1

    # An attachment is recorded as one, never downloaded.
    assert await _post(client, _messenger(None, mid="img.1", attachment="image")) == 200
    async with system_session("test: rows") as db:
        rows = (
            await db.scalars(
                sa.select(ChatMessage).where(ChatMessage.tenant_id == uuid.UUID(shop["tenant_id"]))
            )
        ).all()
        assert {(r.message_type, r.text) for r in rows} == {
            ("TEXT", "01712345678"),
            ("IMAGE", None),
        }
        identity = await db.scalar(
            sa.select(ChatIdentity).where(ChatIdentity.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        assert "psid-1" not in identity.external_id_enc and identity.phone_masked is None
    await jobs.process_chat_messages()
    (draft,) = await _drafts(shop)
    assert "ATTACHMENT" in draft.warnings


async def test_disconnected_page_and_tenant_isolation(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    await _connect(shop, "MESSENGER", PAGE)
    gone = _account_id()
    await _connect(other, "MESSENGER", gone, state="DISCONNECTED")
    assert await _post(client, _messenger("01712345678", mid="x.1", page=gone)) == 200
    assert await _count(ChatMessage, other) == 0

    await _send_chat(client, CHAT)
    assert len(await _drafts(shop)) == 1 and await _drafts(other) == []
    draft_id = str((await _drafts(shop))[0].id)
    theirs = auth_header(other)
    assert (await client.get(f"/v1/chat-orders/{draft_id}", headers=theirs)).status_code == 404
    assert (await client.get("/v1/chat-orders", headers=theirs)).json()["items"] == []
    refused = await client.post(f"/v1/chat-orders/{draft_id}/ignore", headers=theirs)
    assert refused.status_code == 404


async def test_same_psid_on_two_pages_is_two_identities(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    second = _account_id()
    await _connect(shop, "MESSENGER", second)
    await _post(client, _messenger("01712345678", mid="a.1", sender="same"))
    await _post(client, _messenger("01712345678", mid="b.1", sender="same", page=second))
    assert await _count(ChatIdentity, shop) == 2
    await jobs.process_chat_messages()
    assert len(await _drafts(shop)) == 2


# ------------------------------------------------------------- WhatsApp ---


async def test_whatsapp_messages_receipts_and_stop(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "WHATSAPP", WA_NUMBER)
    # A receipt is not a customer message.
    receipt = {"statuses": [{"id": "wamid.out", "status": "delivered", "timestamp": "1"}]}
    assert await _post(client, _whatsapp(receipt)) == 200
    # STOP stays an opt-out for messaging and is not order data.
    assert await _post(client, _whatsapp(_wa_text("STOP", wamid="wamid.stop"))) == 200
    # Wrong phone_number_id.
    assert await _post(client, _whatsapp(_wa_text("hi", wamid="wamid.x"), number="1")) == 200
    assert await _count(ChatMessage, shop) == 0

    base = time.time()
    for index, line in enumerate(CHAT):
        payload = _whatsapp(_wa_text(line, wamid=f"wamid.{index}", at=base + index))
        assert await _post(client, payload) == 200
    assert await _post(client, _whatsapp(_wa_text(CHAT[0], wamid="wamid.0"))) == 200
    assert await _count(ChatMessage, shop) == 4
    await jobs.process_chat_messages()
    (draft,) = await _drafts(shop)
    assert draft.provider == "WHATSAPP" and draft.status == "READY_FOR_REVIEW"
    # The customer typed a number: it wins over the number they write from.
    assert draft.selected_phone == "+8801712345678"
    assert draft.customer_name == "Rahim" and "name" in draft.uncertain_fields
    async with system_session("test: identity") as db:
        identity = await db.get(ChatIdentity, draft.identity_id)
        assert identity.phone_masked == "01812****78"


async def test_whatsapp_sender_number_links_an_existing_customer(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "WHATSAPP", WA_NUMBER)
    order = (await create_order(client, shop, phone="01812345678"))["order"]
    payload = _whatsapp(_wa_text("কালো পাঞ্জাবি XL", wamid="wamid.k"))
    assert await _post(client, payload) == 200
    await jobs.process_chat_messages()
    (draft,) = await _drafts(shop)
    assert draft.customer_match == "MATCHED"
    assert str(draft.customer_id) == order["customer_id"]
    assert draft.selected_phone == "+8801812345678"


# ---------------------------------------------------------- the drafts ---


async def test_messenger_phone_suggests_and_two_phones_are_ambiguous(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    first = (await create_order(client, shop, phone="01712345678"))["order"]
    await create_order(client, shop, phone="01912345678")
    await _send_chat(client, ["কালো পাঞ্জাবি", "01712345678", "Mirpur 10, Dhaka"])
    (draft,) = await _drafts(shop)
    # Messenger is never linked on a phone alone; the seller confirms it.
    assert draft.customer_match == "SUGGESTED" and str(draft.customer_id) == first["customer_id"]

    await _send_chat(client, ["অথবা 01912345678"], prefix="p", start=time.time() + 120)
    (draft,) = await _drafts(shop)
    assert draft.customer_match == "AMBIGUOUS" and draft.customer_id is None
    assert "CUSTOMER_AMBIGUOUS" in draft.warnings and "MULTIPLE_PHONES" in draft.warnings


async def test_ambiguous_product_needs_the_sellers_choice(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    await create_product(client, shop, name="Cotton Panjabi")
    silk = await create_product(client, shop, name="Silk Panjabi")
    await _send_chat(client, ["panjabi lagbe 2ta", "01712345678", "Uttara sector 7, Dhaka"])
    (draft,) = await _drafts(shop)
    assert draft.items[0]["match"]["status"] == "AMBIGUOUS"
    assert {c["name"] for c in draft.items[0]["match"]["candidates"]} == {
        "Cotton Panjabi",
        "Silk Panjabi",
    }
    headers = auth_header(shop)
    body = {
        "phone": "01712345678",
        "address": "Uttara sector 7, Dhaka",
        "items": [{"name": draft.items[0]["name"], "quantity": 2}],
    }
    refused = await client.post(f"/v1/chat-orders/{draft.id}/confirm", headers=headers, json=body)
    assert refused.status_code == 422 and refused.json()["details"]["code"] == "PRODUCT_AMBIGUOUS"
    body["items"] = [{"product_id": silk["id"], "quantity": 2}]
    accepted = await client.post(f"/v1/chat-orders/{draft.id}/confirm", headers=headers, json=body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["order"]["items"][0]["product_name"] == "Silk Panjabi"


async def test_confirm_is_idempotent_and_remembers_the_customer(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    product = await _panjabi_catalogue(client, shop)
    await _send_chat(client, CHAT)
    (draft,) = await _drafts(shop)
    variant = next(v for v in product["variants"] if v["name"] == "Black / XL")
    body = {
        "phone": "01712345678",
        "customer_name": "Rahim",
        "address": "Mirpur 10, Dhaka",
        "items": [{"product_id": product["id"], "variant_id": variant["id"], "quantity": 2}],
        "cod_amount_paisa": 185_000,
    }
    headers = auth_header(shop)
    first = await client.post(f"/v1/chat-orders/{draft.id}/confirm", headers=headers, json=body)
    second = await client.post(f"/v1/chat-orders/{draft.id}/confirm", headers=headers, json=body)
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert first.json()["order"]["id"] == second.json()["order"]["id"]
    assert second.json()["replayed"] is True
    order = first.json()["order"]
    assert order["channel"] == "MESSENGER" and order["status"] == "DRAFT"
    assert await _count(Order, shop) == 1
    detail = (await client.get(f"/v1/chat-orders/{draft.id}", headers=headers)).json()
    assert detail["status"] == "CONFIRMED" and detail["confirmed_order_id"] == order["id"]
    async with system_session("test: identity") as db:
        identity = await db.get(ChatIdentity, draft.identity_id)
        assert str(identity.customer_id) == order["customer_id"]
        assert identity.customer_link_source == "CONFIRMED_ORDER"

    # Later: "cancel" from the same sender is a prompt for the seller only.
    await _send_chat(client, ["order ta cancel korte chai"], prefix="q", start=time.time() + 4000)
    assert len(await _drafts(shop)) == 1
    items = (await client.get("/v1/chat-orders/attention", headers=headers)).json()["items"]
    assert [(i["intent"], i["order_id"]) for i in items] == [("CANCEL_REQUEST", order["id"])]
    still = (await client.get(f"/v1/orders/{order['id']}", headers=headers)).json()
    assert still["status"] == "DRAFT", "a chat message never cancels an order"
    done = await client.post(f"/v1/chat-orders/attention/{items[0]['id']}/resolve", headers=headers)
    assert done.status_code == 200 and done.json()["status"] == "DONE"


async def test_ignore_expire_and_new_session_after_the_window(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    headers = auth_header(shop)
    await _send_chat(client, CHAT)
    (draft,) = await _drafts(shop)
    ignored = await client.post(f"/v1/chat-orders/{draft.id}/ignore", headers=headers)
    assert ignored.status_code == 200 and ignored.json()["status"] == "IGNORED"
    closed = await client.post(
        f"/v1/chat-orders/{draft.id}/confirm",
        headers=headers,
        json={"phone": "01712345678", "items": [{"name": "x"}]},
    )
    assert closed.status_code == 409

    # The next conversation is a new session, and so is one hours later.
    await _send_chat(client, ["সাদা পাঞ্জাবি"], prefix="r", start=time.time() + 60)
    await _send_chat(client, ["নীল শার্ট"], prefix="s", start=time.time() + 3 * 3600)
    drafts = await _drafts(shop)
    assert [d.status for d in drafts] == ["IGNORED", "NEEDS_INFO", "NEEDS_INFO"]

    async with system_session("test: expire") as db:
        expired = await service.expire_due(db, utc_now() + timedelta(days=4))
    assert expired >= 2  # the shared test database may hold other shops' drafts
    assert [d.status for d in await _drafts(shop)] == ["IGNORED", "EXPIRED", "EXPIRED"]
    summary = (await client.get("/v1/chat-orders/summary", headers=headers)).json()
    assert summary["needs_info"] == 0 and summary["channels"]["MESSENGER"] is True

    assert await _count(ChatMessage, shop) == 6
    async with system_session("test: retention") as db:
        await service.purge_expired_data(db, utc_now() + timedelta(days=40))
    assert await _count(ChatMessage, shop) == 0
    assert await _drafts(shop) == []


async def test_list_filters_and_viewers_cannot_read_drafts(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    await _connect(shop, "WHATSAPP", WA_NUMBER)
    await _send_chat(client, CHAT)
    await _post(client, _whatsapp(_wa_text("কালো পাঞ্জাবি", wamid="wamid.f")))
    await jobs.process_chat_messages()
    headers = auth_header(shop)
    every = (await client.get("/v1/chat-orders", headers=headers)).json()["items"]
    assert {d["status"] for d in every} == {"READY_FOR_REVIEW", "NEEDS_INFO"}
    ready = (await client.get("/v1/chat-orders?status=ready", headers=headers)).json()["items"]
    assert [d["provider"] for d in ready] == ["MESSENGER"]
    wa = (await client.get("/v1/chat-orders?provider=WHATSAPP", headers=headers)).json()["items"]
    assert [d["status"] for d in wa] == ["NEEDS_INFO"] and wa[0]["missing_fields"] == ["address"]

    from tests.test_integrations import _role

    await _role(shop, "VIEWER")
    assert (await client.get("/v1/chat-orders", headers=auth_header(shop))).status_code == 403


async def test_account_deletion_erases_chat_data(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    await _send_chat(client, CHAT)
    async with system_session("test: erase") as db:
        removed = await service.erase_tenant(db, uuid.UUID(shop["tenant_id"]))
    assert removed == 4 + 1 + 1  # messages, draft, identity
    for model in (ChatMessage, ChatOrderDraft, ChatIdentity, ChatAttention):
        assert await _count(model, shop) == 0


async def test_failed_processing_logs_no_message_text(client, unique_phone, monkeypatch, caplog):
    shop = await signed_in_shop(client, unique_phone)
    await _connect(shop, "MESSENGER", PAGE)
    await jobs.process_chat_messages()  # nothing left over from other tests
    assert await _post(client, _messenger("গোপন ঠিকানা 01712345678", mid="secret.1")) == 200

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("গোপন ঠিকানা 01712345678")

    monkeypatch.setattr(service, "rebuild", boom)
    caplog.set_level("DEBUG")
    result = await jobs.process_chat_messages()
    assert result == {"processed": 0, "failed": 1}
    assert "01712345678" not in caplog.text and "গোপন" not in caplog.text


# ------------------------------------------------------------- the parser ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("কালো পাঞ্জাবিটা লাগবে\nXL দুইটা", ("কালো পাঞ্জাবি", 2, "XL")),
        ("hi\nkalo panjabi ta lagbe\nXL 2ta", ("kalo panjabi", 2, "XL")),
        ("XL দুইটা\nকালো পাঞ্জাবি", ("কালো পাঞ্জাবি", 2, "XL")),
        ("শাড়ি ২টি", ("শাড়ি", 2, None)),
    ],
)
def test_parser_reads_chat_style_quantities(text, expected):
    parsed = DeterministicOrderParser().parse(text)
    (item,) = parsed.items
    assert (item.name, item.quantity, item.size) == expected
    assert parsed.customer_name is None


def test_detect_intent():
    assert service.detect_intent("order ta cancel korte chai") == "CANCEL_REQUEST"
    assert service.detect_intent("ঠিকানা পরিবর্তন করতে চাই") == "ADDRESS_CHANGE"
    assert service.detect_intent("ভাই কবে পাবো?") == "STATUS_QUESTION"
    assert service.detect_intent("কালো পাঞ্জাবি XL") is None


async def test_legacy_paste_channel_is_accepted(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    created = await create_order(client, shop, channel="PASTE")
    assert created["order"]["channel"] == "PASTE_PARSE"
