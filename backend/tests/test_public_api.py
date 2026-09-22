import json
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.core.clock import utc_now
from app.core.errors import ValidationError
from app.db.session import system_session
from app.public_api import webhooks
from app.public_api.models import ApiKey, WebhookDelivery, WebhookEndpoint
from app.tenants.models import TenantUser
from tests.conftest_commerce import create_order, signed_in_shop
from tests.test_auth_flow import auth_header


async def test_delivery_pins_dns_and_keeps_original_tls_name(monkeypatch):
    async def resolve(host):
        assert host == "example.com"
        return "93.184.216.34"

    seen = {}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @asynccontextmanager
        async def stream(self, method, target, **kwargs):
            seen.update(kwargs)
            assert target.host == "93.184.216.34"
            assert target.path == "/hook"
            yield type("Response", (), {"status_code": 204})()

    monkeypatch.setattr(webhooks, "resolve_public", resolve)
    monkeypatch.setattr(webhooks.httpx, "AsyncClient", Client)
    assert await webhooks.post_signed("https://example.com/hook", b"{}", {}) == 204
    assert seen["headers"]["Host"] == "example.com"
    assert seen["extensions"]["sni_hostname"] == "example.com"


async def test_public_source_ingestion_requires_both_scopes(client, unique_phone):
    shop, _key, headers = await issue(client, unique_phone, ["sources:write", "orders:write"])
    source = (
        await client.post(
            "/v1/order-sources",
            headers=auth_header(shop),
            json={"name": "API store", "enabled": True},
        )
    ).json()
    path = "/public/v1/sources/" + source["id"] + "/orders"
    body = {
        "external_order_id": "api-source-order",
        "payload": {"phone": "01712345678", "items": [{"name": "Item", "unit_price_paisa": 100}]},
    }
    first = await client.post(path, headers=headers, json=body)
    assert first.status_code == 201, first.text
    second = await client.post(
        path, headers={**headers, "Idempotency-Key": str(uuid.uuid4())}, json=body
    )
    assert first.json()["order_id"] == second.json()["order_id"]
    limited = (
        await client.post(
            "/v1/developers/keys",
            headers=auth_header(shop),
            json={"name": "Limited", "scopes": ["sources:write"]},
        )
    ).json()
    assert (
        await client.post(
            path, headers={**headers, "Authorization": "Bearer " + limited["key"]}, json=body
        )
    ).status_code == 403


async def issue(client, phone, scopes, rate=60):
    shop = await signed_in_shop(client, phone)
    response = await client.post(
        "/v1/developers/keys",
        headers=auth_header(shop),
        json={"name": "Integration", "scopes": scopes, "rate_limit": rate},
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    key = response.json()
    return (
        shop,
        key,
        {"Authorization": "Bearer " + key["key"], "Idempotency-Key": str(uuid.uuid4())},
    )


async def test_keys_are_shown_once_hashed_scoped_revoked(client, unique_phone):
    shop, key, headers = await issue(client, unique_phone, ["orders:read"])
    listing = await client.get("/v1/developers/keys", headers=auth_header(shop))
    assert key["key"] not in listing.text and "secret_hash" not in listing.text
    async with system_session("test stored credential") as db:
        stored = await db.get(ApiKey, uuid.UUID(key["id"]))
        assert key["key"] not in stored.secret_hash
        assert len(stored.secret_hash) == 64
    assert (await client.get("/public/v1/orders", headers=headers)).status_code == 200
    assert (await client.get("/public/v1/customers", headers=headers)).status_code == 403
    await client.delete("/v1/developers/keys/" + key["id"], headers=auth_header(shop))
    assert (await client.get("/public/v1/orders", headers=headers)).status_code == 401


async def test_write_requires_key_and_replays_without_duplicate_orders(client, unique_phone):
    _shop, _key, headers = await issue(client, unique_phone, ["orders:read", "orders:write"])
    body = {"phone": "01712345678", "items": [{"name": "Shirt", "unit_price_paisa": 15000}]}
    first = await client.post("/public/v1/orders", headers=headers, json=body)
    assert first.status_code == 201, first.text
    second = await client.post("/public/v1/orders", headers=headers, json=body)
    assert first.json() == second.json()
    conflict = await client.post(
        "/public/v1/orders", headers=headers, json={**body, "note": "different"}
    )
    assert conflict.status_code == 409
    missing = await client.post(
        "/public/v1/orders", headers={"Authorization": headers["Authorization"]}, json=body
    )
    assert missing.status_code == 422
    assert len((await client.get("/public/v1/orders", headers=headers)).json()["items"]) == 1


async def test_scoped_reads_never_cross_tenants(client, unique_phone):
    shop, _key, headers = await issue(client, unique_phone, ["orders:read"])
    own = (await create_order(client, shop))["order"]
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    foreign = (await create_order(client, other))["order"]
    assert (
        await client.get("/public/v1/orders/" + foreign["id"], headers=headers)
    ).status_code == 404
    assert (await client.get("/public/v1/orders/" + own["id"], headers=headers)).status_code == 200
    result = (
        await client.get(
            "/public/v1/orders", headers={**headers, "X-Tenant-ID": other["tenant_id"]}
        )
    ).json()
    assert [row["id"] for row in result["items"]] == [own["id"]]


async def test_public_rate_limits_use_shared_cache(client, unique_phone):
    _shop, _key, headers = await issue(client, unique_phone, ["orders:read"], rate=1)
    assert (await client.get("/public/v1/orders", headers=headers)).status_code == 200
    assert (await client.get("/public/v1/orders", headers=headers)).status_code == 429


async def test_customer_product_and_inventory_writes_use_domain_services(client, unique_phone):
    _shop, _key, headers = await issue(
        client,
        unique_phone,
        [
            "customers:write",
            "customers:read",
            "products:read",
            "products:write",
            "inventory:read",
            "inventory:write",
        ],
    )
    response = await client.post(
        "/public/v1/customers", headers=headers, json={"phone": "01711112222", "name": "Buyer"}
    )
    assert response.status_code == 201, response.text
    assert "phone_enc" not in response.text
    product = await client.post(
        "/public/v1/products", headers=headers, json={"name": "Item", "opening_stock": 5}
    )
    assert product.status_code == 201, product.text
    product_id = product.json()["id"]
    products = await client.get("/public/v1/products", headers=headers)
    assert products.status_code == 200, products.text
    assert [row["id"] for row in products.json()["items"]] == [product_id]
    path = f"/public/v1/inventory/{product_id}/adjustments"
    body = {"quantity_delta": -2, "note": "Count correction"}
    first = await client.post(path, headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert (await client.post(path, headers=headers, json=body)).json() == first.json()
    assert (await client.get(f"/public/v1/inventory/{product_id}", headers=headers)).json()[
        "stock_on_hand"
    ] == 3
    denied = await client.post(
        path,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"quantity_delta": -20, "note": "Cannot oversell"},
    )
    assert denied.status_code == 409


async def test_product_scope_cannot_create_opening_inventory(client, unique_phone):
    _shop, _key, headers = await issue(client, unique_phone, ["products:write"])
    response = await client.post(
        "/public/v1/products", headers=headers, json={"name": "Item", "opening_stock": 1}
    )
    assert response.status_code == 403


async def test_only_owner_manages_keys_and_hooks(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    async with system_session("test role") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = "MANAGER"
    for path in ["/v1/developers/keys", "/v1/developers/webhooks", "/v1/developers/deliveries"]:
        assert (await client.get(path, headers=auth_header(shop))).status_code == 403


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://127.0.0.1/x",
        "https://169.254.169.254",
        "https://[::1]",
        "https://example.com:444",
        "https://user:pass@example.com",
        "https://service.local/x",
    ],
)
def test_webhook_private_and_unsafe_urls_rejected(url):
    with pytest.raises(ValidationError):
        webhooks.valid_url(url)


def test_signatures_authenticate_exact_body_and_reject_replays():
    signed = webhooks.signature("test-secret", b'{"event":1}', 1000)
    assert webhooks.verify_signature("test-secret", b'{"event":1}', signed, now=1100)
    assert not webhooks.verify_signature("test-secret", b'{"event":2}', signed, now=1100)
    assert not webhooks.verify_signature("wrong-secret", b'{"event":1}', signed, now=1100)
    assert not webhooks.verify_signature("test-secret", b'{"event":1}', signed, now=1400)


async def test_dns_rebinding_to_private_host_rejected(monkeypatch):
    async def private_lookup(*args, **kwargs):
        return [(2, 1, 6, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(webhooks.asyncio.get_running_loop(), "getaddrinfo", private_lookup)
    with pytest.raises(ValidationError):
        await webhooks.resolve_public("example.com")


async def test_webhook_fanout_signature_retry_and_secret_storage(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    hook = await client.post(
        "/v1/developers/webhooks",
        headers=headers,
        json={"url": "https://example.com/events", "topics": ["order.created"]},
    )
    assert hook.status_code == 201, hook.text
    secret = hook.json()["signing_secret"]
    assert secret not in (await client.get("/v1/developers/webhooks", headers=headers)).text
    async with system_session("test encrypted signing secret") as db:
        endpoint = await db.get(WebhookEndpoint, uuid.UUID(hook.json()["id"]))
        assert secret not in endpoint.secret_enc
    order = (await create_order(client, shop))["order"]
    deliveries = (await client.get("/v1/developers/deliveries", headers=headers)).json()["items"]
    assert len(deliveries) == 1
    delivery_id = uuid.UUID(deliveries[0]["id"])
    calls = []

    async def post(url, body, request_headers):
        assert webhooks.verify_signature(secret, body, request_headers["X-Ecomsbd-Signature"])
        assert json.loads(body)["data"] == {"order_id": order["id"]}
        calls.append(request_headers)
        return 503 if len(calls) == 1 else 200

    monkeypatch.setattr(webhooks, "post_signed", post)
    await webhooks.deliver(uuid.UUID(shop["tenant_id"]), delivery_id)
    async with system_session("test retry due") as db:
        row = await db.get(WebhookDelivery, delivery_id)
        assert row.status == "RETRY"
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
    await webhooks.deliver(uuid.UUID(shop["tenant_id"]), delivery_id)
    await webhooks.deliver(uuid.UUID(shop["tenant_id"]), delivery_id)
    assert len(calls) == 2
    assert calls[0]["X-Ecomsbd-Event-Id"] == calls[1]["X-Ecomsbd-Event-Id"]
    assert (
        len(
            (
                await client.get(
                    f"/v1/developers/deliveries/{delivery_id}/attempts", headers=headers
                )
            ).json()["items"]
        )
        == 2
    )
    assert (
        await client.post("/v1/developers/webhooks/" + hook.json()["id"] + "/test", headers=headers)
    ).status_code == 202
