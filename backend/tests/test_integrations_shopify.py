"""Shopify through the Integrations Hub: OAuth, webhooks, sync, dedupe, auth expiry."""

import json
import uuid
from datetime import timedelta

import sqlalchemy as sa

from app.core.clock import utc_now
from app.core.context import use_context
from app.db.session import system_session
from app.integrations import jobs, service
from app.integrations.models import IntegrationConnection, IntegrationEvent, IntegrationSyncRun
from app.order_sources.models import ExternalOrder
from tests.conftest_commerce import signed_in_shop
from tests.integrations_fakes import (
    connect_shopify,
    enable_shopify,
    install,
    shopify_hmac,
    shopify_node,
    unique_shop,
    webhook_token,
)
from tests.test_auth_flow import auth_header


async def _receipts(connection_id: str) -> list[ExternalOrder]:
    async with system_session("test: receipts") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(connection_id))
        return list(
            (
                await db.scalars(
                    sa.select(ExternalOrder).where(ExternalOrder.source_id == conn.source_id)
                )
            ).all()
        )


async def _post_webhook(client, token, shop, body: dict, *, topic="orders/create", delivery=None):
    raw = json.dumps(body).encode()
    return await client.post(
        f"/v1/webhooks/integrations/shopify/{token}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Hmac-Sha256": shopify_hmac(raw),
            "X-Shopify-Shop-Domain": shop,
            "X-Shopify-Topic": topic,
            "X-Shopify-Webhook-Id": delivery or str(uuid.uuid4()),
        },
    )


async def test_shopify_is_gated_until_the_platform_app_exists(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    hub = (await client.get("/v1/integrations", headers=auth_header(shop))).json()
    shopify = next(p for p in hub["providers"] if p["provider"] == "SHOPIFY")
    assert shopify == {
        "provider": "SHOPIFY",
        "available": False,
        "blocker": "SHOPIFY_APP_SETUP_REQUIRED",
    }
    created = await client.post(
        "/v1/integrations", headers=auth_header(shop), json={"provider": "SHOPIFY", "name": "x"}
    )
    assert created.status_code == 409
    assert created.json()["details"]["blocker"] == "SHOPIFY_APP_SETUP_REQUIRED"


async def test_oauth_connects_registers_webhooks_and_never_returns_tokens(
    client, unique_phone, monkeypatch, settings
):
    enable_shopify(monkeypatch, settings)
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    connected = await connect_shopify(client, shop, domain)
    exchange = next(c for c in fake.calls if c["url"].endswith("/admin/oauth/access_token"))
    assert exchange["json_body"]["expiring"] == "1" and exchange["json_body"]["code"] == "auth-code"
    assert {s["topic"] for s in fake.subscriptions} == {
        "ORDERS_CREATE",
        "ORDERS_UPDATED",
        "APP_UNINSTALLED",
    }
    assert all("/v1/webhooks/integrations/shopify/" in s["uri"] for s in fake.subscriptions)
    detail = await client.get(f"/v1/integrations/{connected['id']}", headers=auth_header(shop))
    view = detail.json()["connection"]
    assert view["state"] == "CONNECTED" and view["health"] == "CONNECTED"
    assert view["webhook_state"] == "ACTIVE" and view["account_name"] == "Test Store"
    for leaked in ("shpat_", "shprt_", "credentials", "webhook_token", "state_hash"):
        assert leaked not in detail.text
    async with system_session("test: sealed") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(connected["id"]))
        assert row.credentials_enc and "shpat_" not in row.credentials_enc
        assert row.state_hash is None
    # The OAuth state is single use.
    replay = await client.get(f"/v1/integration-callbacks/shopify?{connected['query']}")
    assert replay.json()["result"] == "STATE_INVALID"


async def test_forged_callback_and_foreign_shop_are_refused(
    client, unique_phone, monkeypatch, settings
):
    enable_shopify(monkeypatch, settings)
    install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    created = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "SHOPIFY", "name": "s"}
        )
    ).json()["connection"]
    domain = unique_shop()
    url = (
        await client.post(
            f"/v1/integrations/{created['id']}/connect", headers=headers, json={"shop": domain}
        )
    ).json()["authorize_url"]
    state = url.split("state=", 1)[1].split("&", 1)[0]
    forged = await client.get(
        f"/v1/integration-callbacks/shopify?code=c&shop={domain}&state={state}&hmac=deadbeef"
    )
    assert forged.json()["result"] == "SIGNATURE_INVALID"
    # A second shop cannot link a store that is already linked.
    other = await signed_in_shop(client, "019" + unique_phone[3:])
    await connect_shopify(client, shop, second := unique_shop())
    theirs = (
        await client.post(
            "/v1/integrations",
            headers=auth_header(other),
            json={"provider": "SHOPIFY", "name": "t"},
        )
    ).json()["connection"]
    taken = await client.post(
        f"/v1/integrations/{theirs['id']}/connect",
        headers=auth_header(other),
        json={"shop": second},
    )
    assert taken.status_code == 409 and taken.json()["details"]["code"] == "ALREADY_LINKED"
    assert (
        await client.get(f"/v1/integrations/{theirs['id']}", headers=headers)
    ).status_code == 404


async def test_webhook_retries_and_updates_make_one_order(
    client, unique_phone, monkeypatch, settings
):
    enable_shopify(monkeypatch, settings)
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    token = await webhook_token(conn["id"])
    fake.shopify_orders["501"] = shopify_node(501)
    first = await _post_webhook(client, token, domain, {"id": 501}, delivery="d-1")
    assert first.status_code == 200 and first.json() == {"received": True}
    again = await _post_webhook(client, token, domain, {"id": 501}, delivery="d-1")
    assert again.json()["duplicate"] is True
    await _post_webhook(client, token, domain, {"id": 501}, topic="orders/updated", delivery="d-2")
    await jobs.process_integration_events()
    await _post_webhook(client, token, domain, {"id": 501}, topic="orders/updated", delivery="d-3")
    await jobs.process_integration_events()
    receipts = await _receipts(conn["id"])
    assert len(receipts) == 1
    order = (
        await client.get(f"/v1/orders/{receipts[0].order_id}", headers=auth_header(shop))
    ).json()
    assert order["channel"] == "API" and order["cod_amount_paisa"] == 106000
    assert order["customer_name"] == "Rahim Uddin"
    graph_reads = [c for c in fake.calls if "query Order(" in json.dumps(c.get("json_body"))]
    assert len(graph_reads) == 1  # later deliveries for an imported order cost no provider call
    hub = (await client.get("/v1/integrations", headers=auth_header(shop))).json()
    view = next(i for i in hub["items"] if i["id"] == conn["id"])
    assert view["orders_today"] == 1 and view["last_webhook_at"]


async def test_signature_and_shop_domain_are_verified(client, unique_phone, monkeypatch, settings):
    enable_shopify(monkeypatch, settings)
    install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    token = await webhook_token(conn["id"])
    raw = json.dumps({"id": 9}).encode()
    bad = await client.post(
        f"/v1/webhooks/integrations/shopify/{token}",
        content=raw,
        headers={
            "X-Shopify-Hmac-Sha256": "bm9wZQ==",
            "X-Shopify-Shop-Domain": domain,
            "X-Shopify-Topic": "orders/create",
        },
    )
    assert bad.status_code == 401
    other_store = await _post_webhook(client, token, "someone-else.myshopify.com", {"id": 9})
    assert other_store.status_code == 401
    unknown = await _post_webhook(client, "not-a-token", domain, {"id": 9})
    assert unknown.status_code == 200 and unknown.json() == {"received": True}
    issues = (await client.get("/v1/integrations/issues", headers=auth_header(shop))).json()[
        "items"
    ]
    signature = [i for i in issues if i["code"] == "SIGNATURE_INVALID"]
    assert len(signature) == 1 and signature[0]["attempts"] == 2 and not signature[0]["retryable"]


async def test_rejected_order_is_an_issue_and_retry_is_idempotent(
    client, unique_phone, monkeypatch, settings
):
    enable_shopify(monkeypatch, settings)
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    token = await webhook_token(conn["id"])
    fake.shopify_orders["777"] = shopify_node(777, phone=None)
    await _post_webhook(client, token, domain, {"id": 777})
    await jobs.process_integration_events()
    issues = (await client.get("/v1/integrations/issues", headers=auth_header(shop))).json()[
        "items"
    ]
    issue = next(i for i in issues if i["external_ref"] == "777")
    assert issue["code"] == "MISSING_PHONE" and issue["retryable"] and issue["action"] == "RETRY"
    hub = (await client.get("/v1/integrations", headers=auth_header(shop))).json()
    assert next(i for i in hub["items"] if i["id"] == conn["id"])["health"] == "DEGRADED"
    fake.shopify_orders["777"] = shopify_node(777)  # the seller fixed the phone in Shopify
    retried = await client.post(
        f"/v1/integrations/events/{issue['id']}/retry", headers=auth_header(shop)
    )
    assert retried.status_code == 200 and retried.json()["status"] == "QUEUED"
    twice = await client.post(
        f"/v1/integrations/events/{issue['id']}/retry", headers=auth_header(shop)
    )
    assert twice.status_code == 409
    await jobs.process_integration_events()
    await jobs.process_integration_events()
    assert len(await _receipts(conn["id"])) == 1
    after = (await client.get("/v1/integrations/issues", headers=auth_header(shop))).json()["items"]
    assert not [i for i in after if i["external_ref"] == "777"]


async def test_expired_grant_refreshes_then_reports_auth_expired(
    client, unique_phone, monkeypatch, settings
):
    enable_shopify(monkeypatch, settings)
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    async with system_session("test: expire token") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(conn["id"]))
        creds = service.unseal(row)
        creds["expires_at"] = (utc_now() - timedelta(minutes=5)).isoformat()
        service.seal(row, creds)
    with use_context(tenant_id=uuid.UUID(shop["tenant_id"])):
        _link, fresh = await service.fresh_credentials(uuid.UUID(conn["id"]))
    assert fresh["access_token"] != creds["access_token"]
    refresh = [
        c for c in fake.calls if (c.get("json_body") or {}).get("grant_type") == "refresh_token"
    ]
    assert refresh and refresh[0]["json_body"]["refresh_token"] == creds["refresh_token"]
    # The store revokes the grant: the next read is a 401.
    fake.fail["/graphql.json"] = 401
    token = await webhook_token(conn["id"])
    await _post_webhook(client, token, domain, {"id": 42})
    await jobs.process_integration_events()
    hub = (await client.get("/v1/integrations", headers=auth_header(shop))).json()
    view = next(i for i in hub["items"] if i["id"] == conn["id"])
    assert view["state"] == "AUTH_EXPIRED" and view["health"] == "AUTH_EXPIRED"
    issues = (await client.get("/v1/integrations/issues", headers=auth_header(shop))).json()[
        "items"
    ]
    assert any(i["code"] == "AUTH_EXPIRED" and i["action"] == "RECONNECT" for i in issues)
    blocked = next(i for i in issues if i["external_ref"] == "42")
    assert (
        await client.post(
            f"/v1/integrations/events/{blocked['id']}/retry", headers=auth_header(shop)
        )
    ).json()["details"]["code"] == "RECONNECT_REQUIRED"


async def test_initial_sync_is_bounded_resumable_and_duplicate_free(
    client, unique_phone, monkeypatch, settings
):
    enable_shopify(monkeypatch, settings)
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    too_long = await client.post(
        f"/v1/integrations/{conn['id']}/sync", headers=headers, json={"days": 90}
    )
    assert too_long.status_code == 422 and too_long.json()["details"]["code"] == "WINDOW_TOO_LONG"
    fake.shopify_pages = [
        [shopify_node(1), shopify_node(2), shopify_node(3, cancelledAt="2026-09-21T00:00:00Z")],
        [shopify_node(4), shopify_node(2)],
    ]
    started = await client.post(
        f"/v1/integrations/{conn['id']}/sync", headers=headers, json={"days": 30}
    )
    assert started.status_code == 201, started.text
    busy = await client.post(
        f"/v1/integrations/{conn['id']}/sync", headers=headers, json={"days": 7}
    )
    assert busy.json()["details"]["code"] == "SYNC_ALREADY_RUNNING"
    run_id = uuid.UUID(started.json()["id"])
    tenant = uuid.UUID(shop["tenant_id"])
    # Page one lands; the provider then times out on page two.
    assert await jobs.sync_step(tenant, run_id) is True
    fake.fail['"after": "1"'] = 503
    assert await jobs.sync_step(tenant, run_id) is False
    async with system_session("test: run") as db:
        run = await db.get(IntegrationSyncRun, run_id)
        assert (run.status, run.cursor, run.imported, run.skipped) == ("RUNNING", "1", 2, 1)
        assert run.last_error_code == "PROVIDER_UNAVAILABLE"
        run.next_run_at = utc_now()  # skip the backoff
    fake.fail.clear()
    assert await jobs.sync_step(tenant, run_id) is False
    runs = (await client.get(f"/v1/integrations/{conn['id']}", headers=headers)).json()["runs"]
    done = next(r for r in runs if r["id"] == str(run_id))
    assert done["status"] == "COMPLETED" and done["imported"] == 3 and done["duplicates"] == 1
    assert len(await _receipts(conn["id"])) == 3
    query = next(c for c in fake.calls if "query Orders" in json.dumps(c.get("json_body")))
    assert "created_at:>=" in query["json_body"]["variables"]["query"]


async def test_uninstall_and_disconnect(client, unique_phone, monkeypatch, settings):
    enable_shopify(monkeypatch, settings)
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    token = await webhook_token(conn["id"])
    await _post_webhook(client, token, domain, {"id": 1}, topic="app/uninstalled")
    view = (await client.get(f"/v1/integrations/{conn['id']}", headers=auth_header(shop))).json()[
        "connection"
    ]
    assert view["state"] == "AUTH_EXPIRED" and view["last_error_code"] == "APP_UNINSTALLED"
    gone = await client.post(f"/v1/integrations/{conn['id']}/disconnect", headers=auth_header(shop))
    assert gone.json()["connection"]["state"] == "DISCONNECTED"
    async with system_session("test: wiped") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(conn["id"]))
        assert row.credentials_enc is None and row.account_key is None
        events = (
            await db.scalars(
                sa.select(IntegrationEvent).where(IntegrationEvent.connection_id == row.id)
            )
        ).all()
        assert any(e.code == "APP_UNINSTALLED" for e in events)
    # Reconnecting the same store works; the old grant was never reused.
    await connect_shopify_again(client, shop, conn["id"], domain)
    assert fake.token_counter == 2


async def connect_shopify_again(client, shop, connection_id, domain):
    from tests.integrations_fakes import signed_callback

    url = (
        await client.post(
            f"/v1/integrations/{connection_id}/connect",
            headers=auth_header(shop),
            json={"shop": domain},
        )
    ).json()["authorize_url"]
    state = url.split("state=", 1)[1].split("&", 1)[0]
    query = signed_callback({"code": "again", "shop": domain, "state": state, "timestamp": "1"})
    assert (await client.get(f"/v1/integration-callbacks/shopify?{query}")).json()[
        "result"
    ] == "CONNECTED"
