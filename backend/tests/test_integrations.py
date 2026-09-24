"""Integrations Hub: WooCommerce, Custom Website, Meta gating, RBAC, tenancy, recipes."""

import hashlib
import hmac
import json
import uuid

import sqlalchemy as sa
from pydantic import SecretStr

from app.db.session import system_session
from app.integrations import jobs, meta
from app.integrations.models import IntegrationConnection
from app.order_sources.models import ExternalOrder
from app.public_api import webhooks as public_webhooks
from app.public_api.models import ApiKey, WebhookDelivery
from app.tenants.models import TenantUser
from tests.conftest_commerce import signed_in_shop
from tests.integrations_fakes import install, live_settings, webhook_token, woo_hmac, woo_order
from tests.test_auth_flow import auth_header

ORDER = {
    "phone": "01712345678",
    "customer_name": "Web Buyer",
    "items": [{"name": "Kurti", "quantity": 1, "unit_price_paisa": 90000}],
}


async def _role(shop, role):
    async with system_session("test: role") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = role


async def _website(client, shop):
    created = await client.post(
        "/v1/integrations",
        headers=auth_header(shop),
        json={"provider": "CUSTOM_WEBSITE", "name": "My website"},
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    return created.json()


async def _woo(client, shop, monkeypatch, settings, store):
    monkeypatch.setattr(live_settings(), "public_base_url", "https://api.ecomsbd.test")
    fake = install(monkeypatch)
    headers = auth_header(shop)
    conn = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "WOOCOMMERCE", "name": "Woo"}
        )
    ).json()["connection"]
    started = await client.post(
        f"/v1/integrations/{conn['id']}/connect", headers=headers, json={"store_url": store}
    )
    assert started.status_code == 200, started.text
    return fake, conn, started.json()


# ------------------------------------------------------------- hub & RBAC ---


async def test_hub_shows_real_availability_and_hides_config_from_viewers(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    await _website(client, shop)
    hub = (await client.get("/v1/integrations", headers=auth_header(shop))).json()
    providers = {p["provider"]: p for p in hub["providers"]}
    assert providers["CUSTOM_WEBSITE"]["available"] and providers["WOOCOMMERCE"]["available"]
    assert providers["MESSENGER"]["blocker"] == "META_APP_SETUP_REQUIRED"
    assert hub["can_manage"] and hub["items"][0]["health"] == "SETUP_INCOMPLETE"
    assert "account_id" in hub["items"][0]
    await _role(shop, "VIEWER")
    viewer = await client.get("/v1/integrations", headers=auth_header(shop))
    assert viewer.status_code == 200 and not viewer.json()["can_manage"]
    assert "account_id" not in viewer.json()["items"][0]
    detail = await client.get(
        f"/v1/integrations/{hub['items'][0]['id']}", headers=auth_header(shop)
    )
    assert "custom" not in detail.json() and "source_id" not in detail.text
    denied = await client.post(
        "/v1/integrations",
        headers=auth_header(shop),
        json={"provider": "CUSTOM_WEBSITE", "name": "x"},
    )
    assert denied.status_code == 403


async def test_manager_and_operator_cannot_manage_but_operator_can_retry(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    website = await _website(client, shop)
    connection_id = website["connection"]["id"]
    for role in ("MANAGER", "ORDER_OPERATOR"):
        await _role(shop, role)
        headers = auth_header(shop)
        assert (
            await client.post(f"/v1/integrations/{connection_id}/api-key", headers=headers)
        ).status_code == 403
        assert (
            await client.post(f"/v1/integrations/{connection_id}/disconnect", headers=headers)
        ).status_code == 403
        hub = (await client.get("/v1/integrations", headers=headers)).json()
        assert hub["can_retry"] and not hub["can_manage"]
        # Retry is authorised for order writers; this id simply does not exist.
        missing = await client.post(
            f"/v1/integrations/events/{uuid.uuid4()}/retry", headers=headers
        )
        assert missing.status_code == 404


async def test_connections_are_tenant_isolated(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    website = await _website(client, shop)
    connection_id = website["connection"]["id"]
    headers = auth_header(other)
    assert (
        await client.get(f"/v1/integrations/{connection_id}", headers=headers)
    ).status_code == 404
    assert (
        await client.post(f"/v1/integrations/{connection_id}/disconnect", headers=headers)
    ).status_code == 404
    assert (await client.get("/v1/integrations", headers=headers)).json()["items"] == []
    # The other shop's API key cannot push into this source either.
    theirs = await _website(client, other)
    push = await client.post(
        f"/public/v1/sources/{website['package']['source_id']}/orders",
        headers={
            "Authorization": f"Bearer {theirs['api_key']}",
            "Idempotency-Key": "tenant-probe-1",
        },
        json={"external_order_id": "x-1", "payload": ORDER},
    )
    assert push.status_code == 404


# ------------------------------------------------------------ Custom Website ---


async def test_custom_website_package_key_once_and_public_api_flow(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    website = await _website(client, shop)
    key, package = website["api_key"], website["package"]
    connection_id = website["connection"]["id"]
    assert key.startswith("ec_live_") and package["api_base_url"].endswith("/public/v1")
    assert package["orders_endpoint"].endswith(f"/sources/{package['source_id']}/orders")
    detail = await client.get(f"/v1/integrations/{connection_id}", headers=headers)
    assert key not in detail.text and key.split(".", 1)[1] not in detail.text
    custom = detail.json()["custom"]
    assert custom["key_active"] and custom["last_api_call_at"] is None
    api = {"Authorization": f"Bearer {key}", "Idempotency-Key": "web-order-1001"}
    body = {"external_order_id": "1001", "payload": ORDER}
    path = f"/public/v1/sources/{package['source_id']}/orders"
    first = await client.post(path, headers=api, json=body)
    assert first.status_code == 201, first.text
    replay = await client.post(path, headers=api, json=body)
    assert replay.json() == first.json()
    # Scopes are exactly what a storefront needs: its orders, and reading the
    # catalogue and stock to map SKUs. No customers, no product or stock writes.
    assert (await client.get("/public/v1/customers", headers=api)).status_code == 403
    assert (await client.get("/public/v1/products", headers=api)).status_code == 200
    assert (
        await client.get(f"/public/v1/orders/{first.json()['order_id']}", headers=api)
    ).status_code == 200
    test = (await client.post(f"/v1/integrations/{connection_id}/test", headers=headers)).json()
    assert {c["key"]: c["ok"] for c in test["checks"]} == {
        "API_KEY": True,
        "FIRST_REQUEST": True,
        "FIRST_ORDER": True,
    }
    live = await client.post(f"/v1/integrations/{connection_id}/go-live", headers=headers)
    view = live.json()["connection"]
    assert view["state"] == "CONNECTED" and view["health"] == "CONNECTED"
    assert view["orders_today"] == 1 and view["last_success_at"]


async def test_custom_website_rotate_webhook_and_disconnect(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    website = await _website(client, shop)
    connection_id, old_key = website["connection"]["id"], website["api_key"]
    rotated = await client.post(f"/v1/integrations/{connection_id}/api-key", headers=headers)
    assert rotated.status_code == 201 and rotated.headers["cache-control"] == "no-store"
    new_key = rotated.json()["api_key"]
    probe = "/public/v1/orders"
    assert (
        await client.get(probe, headers={"Authorization": f"Bearer {old_key}"})
    ).status_code == 401
    assert (
        await client.get(probe, headers={"Authorization": f"Bearer {new_key}"})
    ).status_code == 200
    no_hook = await client.post(f"/v1/integrations/{connection_id}/webhook/test", headers=headers)
    assert no_hook.json()["details"]["code"] == "WEBHOOK_NOT_SET"
    unsafe = await client.post(
        f"/v1/integrations/{connection_id}/webhook",
        headers=headers,
        json={"url": "http://127.0.0.1/x"},
    )
    assert unsafe.status_code == 422
    hook = await client.post(
        f"/v1/integrations/{connection_id}/webhook",
        headers=headers,
        json={"url": "https://shop.example.com/ecomsbd"},
    )
    assert hook.status_code == 201 and hook.headers["cache-control"] == "no-store"
    secret = hook.json()["signing_secret"]
    assert (
        secret not in (await client.get(f"/v1/integrations/{connection_id}", headers=headers)).text
    )
    queued = await client.post(f"/v1/integrations/{connection_id}/webhook/test", headers=headers)
    assert queued.status_code == 202
    sent = {}

    async def post_signed(url, body, sent_headers):
        sent.update(url=url, body=body, headers=sent_headers)
        return 503

    monkeypatch.setattr(public_webhooks, "post_signed", post_signed)
    async with system_session("test: deliver") as db:
        delivery = await db.get(WebhookDelivery, uuid.UUID(queued.json()["delivery_id"]))
        tenant = delivery.tenant_id
    await public_webhooks.deliver(tenant, uuid.UUID(queued.json()["delivery_id"]))
    assert public_webhooks.verify_signature(
        secret, sent["body"], sent["headers"]["X-Ecomsbd-Signature"]
    )
    view = (await client.get(f"/v1/integrations/{connection_id}", headers=headers)).json()
    assert view["connection"]["webhook_state"] == "FAILING"
    gone = await client.post(f"/v1/integrations/{connection_id}/disconnect", headers=headers)
    assert gone.json()["connection"]["state"] == "DISCONNECTED"
    assert (
        await client.get(probe, headers={"Authorization": f"Bearer {new_key}"})
    ).status_code == 401
    async with system_session("test: keys") as db:
        keys = (await db.scalars(sa.select(ApiKey).where(ApiKey.tenant_id == tenant))).all()
        assert keys and all(k.revoked_at for k in keys)


# ---------------------------------------------------------------- WooCommerce ---


async def test_woocommerce_manual_keys_webhook_and_dedupe(
    client, unique_phone, monkeypatch, settings
):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    store = f"https://{uuid.uuid4().hex[:8]}.example.com/"
    fake, conn, started = await _woo(client, shop, monkeypatch, settings, store)
    assert started == {"authorize_url": None, "manual": True}  # no public web URL here
    bad = await client.post(
        f"/v1/integrations/{conn['id']}/woocommerce/keys",
        headers=headers,
        json={"consumer_key": "nope", "consumer_secret": "cs_abcdefgh12"},
    )
    assert bad.status_code == 422
    keys = {"consumer_key": "ck_abcdef1234567890", "consumer_secret": "cs_abcdef1234567890"}
    result = await client.post(
        f"/v1/integrations/{conn['id']}/woocommerce/keys", headers=headers, json=keys
    )
    assert result.status_code == 200, result.text
    assert result.json()["connection"]["state"] == "CONNECTED"
    assert "cs_abcdef" not in result.text and "ck_abcdef" not in result.text
    verify = next(c for c in fake.calls if c["url"].endswith("/wp-json/wc/v3/orders"))
    assert verify["auth"] == ("ck_abcdef1234567890", "cs_abcdef1234567890")
    hooks = [c for c in fake.calls if c["url"].endswith("/webhooks") and c["method"] == "POST"]
    assert {h["json_body"]["topic"] for h in hooks} == {"order.created", "order.updated"}
    secret = hooks[0]["json_body"]["secret"]
    token = await webhook_token(conn["id"])
    path = f"/v1/webhooks/integrations/woocommerce/{token}"
    ping = await client.post(
        path,
        content=b"webhook_id=12",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert ping.status_code == 200
    fake.woo_orders[88] = woo_order(88)
    raw = json.dumps(woo_order(88)).encode()
    signed = {
        "X-WC-Webhook-Signature": woo_hmac(secret, raw),
        "X-WC-Webhook-Topic": "order.created",
        "X-WC-Webhook-Delivery-ID": "dlv-1",
    }
    assert (await client.post(path, content=raw, headers=signed)).status_code == 200
    assert (await client.post(path, content=raw, headers=signed)).json()["duplicate"] is True
    forged = await client.post(
        path,
        content=raw,
        headers={**signed, "X-WC-Webhook-Signature": "AAAA", "X-WC-Webhook-Delivery-ID": "dlv-2"},
    )
    assert forged.status_code == 401
    await jobs.process_integration_events()
    updated = {**signed, "X-WC-Webhook-Topic": "order.updated", "X-WC-Webhook-Delivery-ID": "dlv-3"}
    await client.post(path, content=raw, headers=updated)
    await jobs.process_integration_events()
    async with system_session("test: woo receipts") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(conn["id"]))
        receipts = (
            await db.scalars(
                sa.select(ExternalOrder).where(ExternalOrder.source_id == row.source_id)
            )
        ).all()
    assert len(receipts) == 1 and receipts[0].external_order_id == "88"
    order = (await client.get(f"/v1/orders/{receipts[0].order_id}", headers=headers)).json()
    # A COD order: WooCommerce marks it paid at "processing", the courier still collects.
    assert order["cod_amount_paisa"] == 106000 and order["customer_name"] == "Karim Mia"
    items = order["items"]
    assert sum(i["line_total_paisa"] for i in items) == 100000


async def test_woocommerce_one_click_callback_and_sync(client, unique_phone, monkeypatch, settings):
    monkeypatch.setattr(live_settings(), "public_web_url", "https://app.ecomsbd.test")
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    store = f"https://{uuid.uuid4().hex[:8]}.example.com"
    fake, conn, started = await _woo(client, shop, monkeypatch, settings, store)
    url = started["authorize_url"]
    assert url.startswith(f"{store}/wc-auth/v1/authorize?") and "scope=read_write" in url
    assert (
        "callback_url=https%3A%2F%2Fapi.ecomsbd.test%2Fv1%2Fintegration-callbacks%2Fwoocommerce"
        in url
    )
    state = url.split("user_id=", 1)[1].split("&", 1)[0]
    callback = {
        "key_id": 1,
        "user_id": state,
        "consumer_key": "ck_1234567890abcdef",
        "consumer_secret": "cs_1234567890abcdef",
        "key_permissions": "read_write",
    }
    wrong = await client.post(
        "/v1/integration-callbacks/woocommerce", json={**callback, "user_id": "guess"}
    )
    assert wrong.status_code == 400
    ok = await client.post("/v1/integration-callbacks/woocommerce", json=callback)
    assert ok.status_code == 200
    again = await client.post("/v1/integration-callbacks/woocommerce", json=callback)
    assert again.status_code == 400  # the state was single use
    tested = await client.post(f"/v1/integrations/{conn['id']}/test", headers=headers)
    assert tested.json()["connection"]["state"] == "CONNECTED", tested.text
    fake.woo_orders = {
        1: woo_order(1),
        2: woo_order(2, status="pending"),
        3: woo_order(3, billing={"first_name": "X", "phone": ""}),
        4: woo_order(4, payment_method="bkash"),
    }
    run = await client.post(
        f"/v1/integrations/{conn['id']}/sync", headers=headers, json={"days": 30}
    )
    assert run.status_code == 201, run.text
    await jobs.run_integration_syncs()
    detail = (await client.get(f"/v1/integrations/{conn['id']}", headers=headers)).json()
    done = detail["runs"][0]
    assert (done["status"], done["imported"], done["skipped"], done["failed"], done["total"]) == (
        "COMPLETED",
        2,
        1,
        1,
        4,
    )
    missing = [e for e in detail["events"] if e["code"] == "MISSING_PHONE"]
    assert missing and missing[0]["external_ref"] == "3" and missing[0]["retryable"]
    fake.woo_orders[3] = woo_order(3)
    await client.post(f"/v1/integrations/events/{missing[0]['id']}/retry", headers=headers)
    await jobs.process_integration_events()
    async with system_session("test: woo sync receipts") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(conn["id"]))
        refs = sorted(
            (
                await db.scalars(
                    sa.select(ExternalOrder.external_order_id).where(
                        ExternalOrder.source_id == row.source_id
                    )
                )
            ).all()
        )
    assert refs == ["1", "3", "4"]


async def test_store_url_must_be_public_https(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    conn = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "WOOCOMMERCE", "name": "w"}
        )
    ).json()["connection"]
    for url in (
        "http://shop.example.com",
        "https://localhost",
        "https://10.0.0.5",
        "https://shop.example.com?x=1",
    ):
        result = await client.post(
            f"/v1/integrations/{conn['id']}/connect", headers=headers, json={"store_url": url}
        )
        assert result.status_code == 422, url


# ----------------------------------------------------------------- Meta ---


async def test_meta_is_gated_and_webhook_is_verified(client, unique_phone, monkeypatch, settings):
    shop = await signed_in_shop(client, unique_phone)
    blocked = await client.post(
        "/v1/integrations",
        headers=auth_header(shop),
        json={"provider": "MESSENGER", "name": "Page"},
    )
    assert (
        blocked.status_code == 409
        and blocked.json()["details"]["blocker"] == "META_APP_SETUP_REQUIRED"
    )
    challenge = await client.get(
        "/v1/webhooks/integrations/meta?hub.mode=subscribe&hub.verify_token=x&hub.challenge=42"
    )
    assert challenge.status_code == 403
    monkeypatch.setattr(live_settings(), "meta_app_id", "app")
    monkeypatch.setattr(live_settings(), "meta_app_secret", SecretStr("meta-secret"))
    monkeypatch.setattr(live_settings(), "meta_webhook_verify_token", SecretStr("verify-me"))
    ok = await client.get(
        "/v1/webhooks/integrations/meta?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42"
    )
    assert ok.status_code == 200 and ok.text == "42"
    body = json.dumps({"object": "page", "entry": [{"id": "123", "messaging": []}]}).encode()
    good = "sha256=" + hmac.new(b"meta-secret", body, hashlib.sha256).hexdigest()
    assert meta.valid_webhook(body, good) and not meta.valid_webhook(body, "sha256=00")
    assert (
        await client.post(
            "/v1/webhooks/integrations/meta",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=00"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/v1/webhooks/integrations/meta", content=body, headers={"X-Hub-Signature-256": good}
        )
    ).status_code == 200
    created = await client.post(
        "/v1/integrations",
        headers=auth_header(shop),
        json={"provider": "MESSENGER", "name": "Page"},
    )
    assert created.status_code == 201
    started = await client.post(
        f"/v1/integrations/{created.json()['connection']['id']}/connect",
        headers=auth_header(shop),
        json={},
    )
    url = started.json()["authorize_url"]
    assert (
        url.startswith("https://www.facebook.com/v26.0/dialog/oauth?") and "pages_messaging" in url
    )


# -------------------------------------------------------------- recipes ---


async def test_recipes_install_through_the_v2_rule_engine(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    catalog = (await client.get("/v1/integrations/recipes", headers=headers)).json()["items"]
    keys = {r["key"]: r for r in catalog}
    assert set(keys) == {
        "external_order_followup",
        "confirmed_notify",
        "booked_tracking",
        "cancelled_followup",
    }
    assert keys["booked_tracking"]["blocker"] == "CHANNEL_DISABLED"
    installed = await client.post(
        "/v1/integrations/recipes/external_order_followup", headers=headers, json={"locale": "bn"}
    )
    assert installed.status_code == 201, installed.text
    rule = installed.json()
    assert rule["trigger"] == "order.created" and rule["enabled"]
    assert rule["conditions"] == [{"field": "channel", "op": "eq", "value": "API"}]
    rules = (await client.get("/v1/automation/rules", headers=headers)).json()["items"]
    assert any(r["id"] == rule["id"] for r in rules)
    after = {
        r["key"]: r
        for r in (await client.get("/v1/integrations/recipes", headers=headers)).json()["items"]
    }
    assert after["external_order_followup"]["installed"]
    blocked = await client.post(
        "/v1/integrations/recipes/booked_tracking", headers=headers, json={"locale": "en"}
    )
    assert blocked.status_code == 409
    await _role(shop, "ORDER_OPERATOR")
    denied = await client.post(
        "/v1/integrations/recipes/confirmed_notify", headers=auth_header(shop), json={}
    )
    assert denied.status_code == 403
