"""Connecting stores and channels from the mobile app, with no web dashboard.

WooCommerce's approval page, Shopify and Facebook sign-in all end on a page the
provider redirects to. A seller who started in the app comes back through the
API's own HTTPS return page, which hands over to the app's URL scheme; the web
flow is unchanged.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlsplit

import sqlalchemy as sa
from pydantic import SecretStr

from app.db.session import system_session
from app.integrations.models import IntegrationConnection
from tests.conftest_commerce import signed_in_shop
from tests.integrations_fakes import install, live_settings
from tests.test_auth_flow import auth_header

KEYS = {"consumer_key": "ck_aaaabbbbcccc", "consumer_secret": "cs_aaaabbbbcccc"}


async def _woo_connection(client, shop) -> dict:
    created = await client.post(
        "/v1/integrations",
        headers=auth_header(shop),
        json={"provider": "WOOCOMMERCE", "name": "My store"},
    )
    assert created.status_code == 201, created.text
    return created.json()["connection"]


async def test_app_one_click_returns_to_the_app_without_a_web_dashboard(
    client, unique_phone, monkeypatch
):
    monkeypatch.setattr(live_settings(), "public_base_url", "https://api.ecomsbd.test")
    monkeypatch.setattr(live_settings(), "public_web_url", None)
    install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    hub = (await client.get("/v1/integrations", headers=headers)).json()
    woo = next(p for p in hub["providers"] if p["provider"] == "WOOCOMMERCE")
    assert woo["one_click"] is False and woo["one_click_app"] is True

    conn = await _woo_connection(client, shop)
    store = f"https://{uuid.uuid4().hex[:8]}.example.com"
    web = await client.post(
        f"/v1/integrations/{conn['id']}/connect", headers=headers, json={"store_url": store}
    )
    assert web.json() == {"authorize_url": None, "manual": True}  # the web flow, unchanged
    started = await client.post(
        f"/v1/integrations/{conn['id']}/connect",
        headers=headers,
        json={"store_url": store, "return_to": "app"},
    )
    assert started.status_code == 200, started.text
    url = started.json()["authorize_url"]
    query = parse_qs(urlsplit(url).query)
    assert query["return_url"] == [
        f"https://api.ecomsbd.test/v1/integration-callbacks/return?connection={conn['id']}"
        "&result=woocommerce"
    ]
    assert query["callback_url"] == [
        "https://api.ecomsbd.test/v1/integration-callbacks/woocommerce"
    ]

    detail = (await client.get(f"/v1/integrations/{conn['id']}", headers=headers)).json()
    assert detail["connection"]["keys_received"] is False
    callback = {
        "key_id": 1,
        "user_id": query["user_id"][0],
        **KEYS,
        "key_permissions": "read_write",
    }
    assert (
        await client.post("/v1/integration-callbacks/woocommerce", json=callback)
    ).status_code == 200
    detail = (await client.get(f"/v1/integrations/{conn['id']}", headers=headers)).json()
    assert detail["connection"]["keys_received"] is True
    assert "ck_aaaa" not in str(detail) and "cs_aaaa" not in str(detail)

    page = await client.get(
        f"/v1/integration-callbacks/return?connection={conn['id']}&result=woocommerce"
        f"&success=1&user_id={query['user_id'][0]}"
    )
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert (
        f"com.ecomsbd.app://integrations/return?connection={conn['id']}&amp;result=woocommerce"
        in page.text
    )
    assert query["user_id"][0] not in page.text, "the spent OAuth state is never echoed"

    tested = await client.post(f"/v1/integrations/{conn['id']}/test", headers=headers)
    assert tested.json()["connection"]["state"] == "CONNECTED", tested.text

    # Disconnect, then reconnect the same store with keys typed by hand.
    off = await client.post(f"/v1/integrations/{conn['id']}/disconnect", headers=headers)
    assert off.json()["connection"]["state"] == "DISCONNECTED"
    again = await client.post(
        f"/v1/integrations/{conn['id']}/connect",
        headers=headers,
        json={"store_url": store, "return_to": "app"},
    )
    assert again.status_code == 200
    keys = await client.post(
        f"/v1/integrations/{conn['id']}/woocommerce/keys", headers=headers, json=KEYS
    )
    assert keys.json()["connection"]["state"] == "CONNECTED", keys.text


async def test_declined_approval_and_bad_return_links(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    conn = await _woo_connection(client, shop)
    declined = await client.get(
        f"/v1/integration-callbacks/return?connection={conn['id']}&result=woocommerce&success=0"
    )
    assert "result=ACCESS_DENIED" in declined.text
    odd = await client.get(
        f"/v1/integration-callbacks/return?connection={conn['id']}&result=<script>"
    )
    assert "result=UNKNOWN" in odd.text and "<script>alert" not in odd.text
    bad = await client.get("/v1/integration-callbacks/return?connection=nope&result=x")
    assert bad.status_code == 400


async def test_invalid_keys_are_reported_not_trusted(client, unique_phone, monkeypatch):
    monkeypatch.setattr(live_settings(), "public_base_url", "https://api.ecomsbd.test")
    fake = install(monkeypatch)
    fake.fail["/wp-json/wc/v3/orders"] = 401
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    conn = await _woo_connection(client, shop)
    store = f"https://{uuid.uuid4().hex[:8]}.example.com"
    await client.post(
        f"/v1/integrations/{conn['id']}/connect",
        headers=headers,
        json={"store_url": store, "return_to": "app"},
    )
    result = await client.post(
        f"/v1/integrations/{conn['id']}/woocommerce/keys", headers=headers, json=KEYS
    )
    body = result.json()
    assert body["ok"] is False and body["connection"]["state"] != "CONNECTED"
    assert body["checks"][0]["code"] == "AUTH_EXPIRED"


async def test_messenger_sign_in_from_the_app_returns_to_the_app(client, unique_phone, monkeypatch):
    settings = live_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", SecretStr("meta-secret"))
    monkeypatch.setattr(settings, "meta_webhook_verify_token", SecretStr("verify-me"))
    monkeypatch.setattr(settings, "public_web_url", "https://app.ecomsbd.test")
    install(monkeypatch)  # Graph answers 190: the code exchange fails
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    conn = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "MESSENGER", "name": "Page"}
        )
    ).json()["connection"]
    started = await client.post(
        f"/v1/integrations/{conn['id']}/connect", headers=headers, json={"return_to": "app"}
    )
    state = parse_qs(urlsplit(started.json()["authorize_url"]).query)["state"][0]
    back = await client.get(f"/v1/integration-callbacks/meta?state={state}&code=abc")
    assert back.status_code == 200 and "text/html" in back.headers["content-type"]
    assert f"com.ecomsbd.app://integrations/return?connection={conn['id']}" in back.text
    async with system_session("test: conn") as db:
        row = await db.scalar(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.id == uuid.UUID(conn["id"])
            )
        )
        assert row.config["return_to"] == "app"


async def test_custom_website_key_once_test_order_and_go_live(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    created = await client.post(
        "/v1/integrations",
        headers=headers,
        json={"provider": "CUSTOM_WEBSITE", "name": "My website"},
    )
    assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
    key = created.json()["api_key"]
    connection_id = created.json()["connection"]["id"]
    detail = (await client.get(f"/v1/integrations/{connection_id}", headers=headers)).json()
    assert key not in str(detail), "the key is shown once, at creation"
    custom = detail["custom"]
    assert custom["api_base_url"].endswith("/public/v1") and custom["key_active"] is True
    assert custom["last_api_call_at"] is None and custom["last_order_at"] is None
    assert "order.confirmed" in custom["topics"]
    checked = await client.post(
        f"/v1/integrations/{connection_id}/test-order", headers=headers, json={}
    )
    assert checked.json()["valid"] is True and checked.json()["creates_data"] is False
    live = await client.post(f"/v1/integrations/{connection_id}/go-live", headers=headers)
    assert live.json()["connection"]["state"] == "CONNECTED"
    rotated = await client.post(f"/v1/integrations/{connection_id}/api-key", headers=headers)
    assert rotated.status_code == 201 and rotated.json()["api_key"] != key
    no_hook = await client.post(f"/v1/integrations/{connection_id}/webhook/test", headers=headers)
    assert no_hook.status_code == 409
    hook = await client.post(
        f"/v1/integrations/{connection_id}/webhook",
        headers=headers,
        json={"url": "https://shop.example.com/hooks/ecomsbd"},
    )
    assert hook.status_code == 201 and hook.json()["signing_secret"]
    sent = await client.post(f"/v1/integrations/{connection_id}/webhook/test", headers=headers)
    assert sent.status_code == 202
