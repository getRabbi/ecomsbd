"""The Chrome extension's server side (V4 Phase 5).

The extension pairs with the 6-digit code, reads the rules, and captures the
open chat (or the selected text) only after the seller taps Capture. Its key
is an ingest key: the capture answer carries ids and field names, never the
shop's data; the order is finished on the web quick-order page, signed in.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient

from app.chat_orders.models import ChatIdentity, ChatMessage, ChatOrderDraft
from app.common.feature_flags import FeatureFlag
from app.db.session import system_session
from app.device_links.models import DeviceIngestKey
from app.integrations.models import IntegrationConnection
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_device_links import BEAT, LINK, _device, _key_header

EXTENSION = "/v1/intake/captures/extension"
CAPTURES = "/v1/intake/captures"
UNLINK = "/v1/intake/unlink"
RULES = "/v1/intake/rules"
LISTED = "/v1/devices/extensions"
PHONE = "01700000021"
CHAT = ["কালো পাঞ্জাবিটা লাগবে", "XL দুইটা", PHONE, "মিরপুর ১০ ঢাকা"]
ANSWER_KEYS = {
    "draft_id",
    "status",
    "missing_fields",
    "accepted",
    "duplicates",
    "open_path",
    "replayed",
}


@pytest_asyncio.fixture
async def intake_on(settings: Any) -> AsyncIterator[None]:
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, "intake_device_enabled"):
        yield


@pytest_asyncio.fixture
async def extension_on(settings: Any, intake_on: None) -> AsyncIterator[None]:
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, "extension_enabled"):
        yield


async def _paired(client: AsyncClient, shop: dict[str, Any]) -> dict[str, Any]:
    code = (await client.post("/v1/devices/pair", headers=auth_header(shop))).json()["code"]
    claim = await client.post(
        "/v1/devices/pair/claim",
        json={**_device(platform="extension", covered_packages=[]), "code": code},
    )
    assert claim.status_code == 200, claim.text
    return claim.json()


def _capture(lines: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "host": "web.whatsapp.com",
        "origin": "CONVERSATION",
        "conversation_key": "Rahim",
        "messages": [{"text": line} for line in lines],
        **extra,
    }


async def _send(client: AsyncClient, key: str, body: dict[str, Any], capture: str | None = None):
    headers = {**_key_header(key), "Idempotency-Key": capture or f"cap-{uuid.uuid4().hex}"}
    return await client.post(EXTENSION, json=body, headers=headers)


# ------------------------------------------------------------- the flag ---


async def test_pairing_an_extension_needs_extension_enabled(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    code = (await client.post("/v1/devices/pair", headers=auth_header(shop))).json()["code"]
    refused = await client.post(
        "/v1/devices/pair/claim", json={**_device(platform="extension"), "code": code}
    )
    assert refused.status_code == 403 and refused.json()["code"] == "FEATURE_DISABLED"
    # The code is not spent: a phone may still claim it.
    phone = await client.post("/v1/devices/pair/claim", json={**_device(), "code": code})
    assert phone.status_code == 200, phone.text
    listed = await client.get(LISTED, headers=auth_header(shop))
    assert listed.status_code == 403 and listed.json()["code"] == "FEATURE_DISABLED"
    linked = await client.post(LINK, json=_device(platform="extension"), headers=auth_header(shop))
    assert linked.status_code == 403


async def test_the_kill_switch_stops_every_linked_extension_and_no_phone(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    key = (await _paired(client, shop))["ingest_key"]
    phone_key = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()[
        "ingest_key"
    ]
    assert (await client.get(RULES, headers=_key_header(key))).status_code == 200
    tenant_id = uuid.UUID(shop["tenant_id"])
    async with system_session("test: extension kill switch") as db:
        db.add(
            FeatureFlag(key="extension_enabled", scope="TENANT", tenant_id=tenant_id, enabled=False)
        )
    try:
        for response in (
            await client.get(RULES, headers=_key_header(key)),
            await client.post(BEAT, json={}, headers=_key_header(key)),
            await _send(client, key, _capture(CHAT)),
            await client.post(UNLINK, headers=_key_header(key)),
        ):
            assert response.status_code == 403 and response.json()["code"] == "FEATURE_DISABLED"
        assert (await client.post(BEAT, json={}, headers=_key_header(phone_key))).status_code == 200
    finally:
        async with system_session("test: extension kill switch off") as db:
            await db.execute(
                sa.delete(FeatureFlag).where(
                    FeatureFlag.key == "extension_enabled", FeatureFlag.tenant_id == tenant_id
                )
            )


# --------------------------------------------------------------- capture ---


async def test_a_capture_becomes_an_extension_draft_and_answers_ids_only(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    paired = await _paired(client, shop)
    listed = (await client.get(LISTED, headers=auth_header(shop))).json()["extensions"]
    assert [row["connection_id"] for row in listed] == [paired["connection_id"]]

    response = await _send(client, paired["ingest_key"], _capture(CHAT, sender_display="Rahim"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ANSWER_KEYS
    assert body["status"] == "READY_FOR_REVIEW" and body["missing_fields"] == []
    assert body["accepted"] == 4 and body["duplicates"] == 0 and body["replayed"] is False
    assert body["open_path"] == f"/quick-order?draft={body['draft_id']}"
    assert PHONE not in response.text and "মিরপুর" not in response.text

    async with system_session("test: extension draft") as db:
        draft = await db.get(ChatOrderDraft, uuid.UUID(body["draft_id"]))
        assert draft is not None
        identity = await db.get(ChatIdentity, draft.identity_id)
        conn = await db.get(IntegrationConnection, uuid.UUID(paired["connection_id"]))
    assert draft.capture_source == "EXTENSION" and draft.provider == "DEVICE"
    assert identity is not None and identity.account_ref == "web.whatsapp.com"
    assert conn is not None and conn.last_success_at is not None


async def test_a_repeated_capture_is_replayed_not_stored_twice(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    key = (await _paired(client, shop))["ingest_key"]
    first = await _send(client, key, _capture(CHAT), capture="cap-replay-0001")
    again = await _send(client, key, _capture(CHAT), capture="cap-replay-0001")
    assert first.status_code == again.status_code == 200
    assert again.json()["replayed"] is True
    assert again.json()["draft_id"] == first.json()["draft_id"]
    # A second tap with a new key on the same chat: every line is a duplicate.
    third = (await _send(client, key, _capture(CHAT))).json()
    assert third["accepted"] == 0 and third["duplicates"] == 4
    assert third["draft_id"] == first.json()["draft_id"]
    async with system_session("test: messages") as db:
        count = await db.scalar(
            sa.select(sa.func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
    assert count == 4


async def test_selections_are_their_own_sender_and_bad_bodies_are_refused(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    key = (await _paired(client, shop))["ingest_key"]

    def selection(text: str) -> dict[str, Any]:
        return {"host": "www.messenger.com", "origin": "SELECTION", "messages": [{"text": text}]}

    one = (await _send(client, key, selection(PHONE))).json()
    two = (await _send(client, key, selection("01700000022"))).json()
    assert one["draft_id"] != two["draft_id"], "no chat name: each selection is its own sender"
    assert one["status"] == "NEEDS_INFO" and set(one["missing_fields"]) == {"address", "items"}
    # The same number again joins its open draft (WP1.3), as every source does.
    same = (await _send(client, key, selection(f"{PHONE} Mirpur 10, Dhaka"))).json()
    assert same["draft_id"] == one["draft_id"]

    for bad in (
        _capture(CHAT, host="facebook.com"),
        _capture(CHAT, conversation_key=None),
        _capture(CHAT, origin="DOM"),
        _capture(["   "]),
        _capture([f"line {i}" for i in range(51)]),
        {**_capture(CHAT), "package": "com.whatsapp"},
    ):
        assert (await _send(client, key, bad)).status_code == 422
    no_key = await client.post(EXTENSION, json=_capture(CHAT), headers=_key_header(key))
    assert no_key.status_code == 422, "Idempotency-Key is required"


async def test_the_answer_never_carries_the_shops_customer_data(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    """A key that sends a known customer's number learns nothing about them:
    their saved name and address stay behind the seller's session."""
    shop = await signed_in_shop(client, unique_phone)
    await create_order(
        client,
        shop,
        phone=PHONE,
        customer_name="Karim Uddin",
        address="House 9, Road 4, Dhanmondi, Dhaka",
    )
    key = (await _paired(client, shop))["ingest_key"]
    response = await _send(client, key, _capture([PHONE], conversation_key="Karim"))
    assert response.status_code == 200, response.text
    assert set(response.json()) == ANSWER_KEYS
    assert "Karim Uddin" not in response.text and "Dhanmondi" not in response.text
    assert "address" in response.json()["missing_fields"]


async def test_keys_cross_neither_way_between_phone_and_extension_routes(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    key = (await _paired(client, shop))["ingest_key"]
    phone_key = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()[
        "ingest_key"
    ]
    phone = await _send(client, phone_key, _capture(CHAT))
    assert phone.status_code == 403 and phone.json()["details"]["reason"] == "NOT_AN_EXTENSION"
    batch = {
        "package": "com.whatsapp.w4b",
        "conversation_key": "Rahim",
        "messages": [{"text": PHONE}],
    }
    upload = await client.post(
        CAPTURES, json=batch, headers={**_key_header(key), "Idempotency-Key": "batch-ext-0001"}
    )
    assert upload.status_code == 403
    assert upload.json()["details"]["reason"] == "USE_EXTENSION_CAPTURE"
    # Only the open-chat capture; a seller route stays closed to the key.
    assert (
        await client.post(
            "/v1/intake/captures/manual",
            json={"source": "PASTE", "messages": [{"text": PHONE}]},
            headers=_key_header(key),
        )
    ).status_code == 401


async def test_the_rules_ship_no_unverified_selectors(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    key = (await _paired(client, shop))["ingest_key"]
    rules = (await client.get(RULES, headers=_key_header(key))).json()["rules"]
    hosts = rules["extension"]["hosts"]
    assert set(hosts) == {"business.facebook.com", "www.messenger.com", "web.whatsapp.com"}
    for host in hosts.values():
        assert host["verified_at"] is None
        assert set(host["selectors"].values()) == {None}


# ---------------------------------------------------------------- unpair ---


async def test_unpair_revokes_the_key_and_disconnects_the_link(
    client: AsyncClient, unique_phone: str, extension_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    paired = await _paired(client, shop)
    key = paired["ingest_key"]
    gone = await client.post(UNLINK, headers=_key_header(key))
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"connection_id": paired["connection_id"], "state": "DISCONNECTED"}
    assert (await client.post(BEAT, json={}, headers=_key_header(key))).status_code == 401
    assert (await client.post(UNLINK, headers=_key_header(key))).status_code == 401
    async with system_session("test: keys") as db:
        keys = (
            await db.scalars(
                sa.select(DeviceIngestKey).where(
                    DeviceIngestKey.connection_id == uuid.UUID(paired["connection_id"])
                )
            )
        ).all()
    assert keys and all(row.revoked_at is not None for row in keys)
    listed = (await client.get(LISTED, headers=auth_header(shop))).json()["extensions"]
    assert listed == []
