"""V3.2 two-way sync: mapping, inventory authority, status/tracking push, conflicts."""

import json
import uuid
from datetime import timedelta

import sqlalchemy as sa

from app.common.outbox import OutboxTopic
from app.core.clock import utc_now
from app.db.session import system_session
from app.integrations import inventory, jobs
from app.integrations.models import IntegrationConnection, IntegrationEvent
from app.integrations.sync_models import IntegrationLink
from app.orders.models import Order
from app.products.models import StockMovement
from app.public_api.models import WebhookDelivery
from app.tenants.models import TenantUser
from app.worker.jobs import HANDLERS, dispatch_outbox
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.integrations_fakes import (
    FULL_SHOPIFY_SCOPES,
    connect_shopify,
    enable_shopify,
    install,
    live_settings,
    shopify_hmac,
    shopify_node,
    shopify_variant,
    unique_shop,
    webhook_token,
    woo_hmac,
    woo_order,
)
from tests.test_auth_flow import auth_header

NOW = utc_now()


# --------------------------------------------------------------- helpers ---


async def drain_outbox() -> None:
    for _ in range(40):
        result = await dispatch_outbox()
        if not any(result.values()):
            return


async def settle() -> None:
    """Run the outbox, then queued integration work, until both are idle."""
    for _ in range(3):
        await drain_outbox()
        await jobs.process_integration_events()


async def shopify_shop(client, phone, monkeypatch):
    enable_shopify(monkeypatch)
    fake = install(monkeypatch)
    fake.scope = FULL_SHOPIFY_SCOPES
    shop = await signed_in_shop(client, phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)
    return fake, shop, conn, domain


async def woo_shop(client, phone, monkeypatch):
    monkeypatch.setattr(live_settings(), "public_base_url", "https://api.ecomsbd.test")
    fake = install(monkeypatch)
    shop = await signed_in_shop(client, phone)
    headers = auth_header(shop)
    conn = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "WOOCOMMERCE", "name": "Woo"}
        )
    ).json()["connection"]
    store = f"https://{uuid.uuid4().hex[:8]}.example.com"
    await client.post(
        f"/v1/integrations/{conn['id']}/connect", headers=headers, json={"store_url": store}
    )
    keys = {"consumer_key": "ck_abcdef1234567890", "consumer_secret": "cs_abcdef1234567890"}
    done = await client.post(
        f"/v1/integrations/{conn['id']}/woocommerce/keys", headers=headers, json=keys
    )
    assert done.json()["connection"]["state"] == "CONNECTED", done.text
    hooks = [c for c in fake.calls if c["url"].endswith("/webhooks") and c["method"] == "POST"]
    return fake, shop, conn, hooks[0]["json_body"]["secret"]


async def put_sync(client, shop, conn_id, **settings):
    result = await client.put(
        f"/v1/integrations/{conn_id}/sync-settings", headers=auth_header(shop), json=settings
    )
    assert result.status_code == 200, result.text
    return result.json()


async def links(client, shop, conn_id, **params):
    result = await client.get(
        f"/v1/integrations/{conn_id}/links", headers=auth_header(shop), params=params
    )
    assert result.status_code == 200, result.text
    return result.json()


async def conflicts(client, shop, **params):
    return (
        await client.get("/v1/integrations/conflicts", headers=auth_header(shop), params=params)
    ).json()["items"]


async def age_pending(conn_id: str) -> None:
    async with system_session("test: age grace") as db:
        rows = (
            await db.scalars(
                sa.select(IntegrationLink).where(
                    IntegrationLink.connection_id == uuid.UUID(conn_id)
                )
            )
        ).all()
        for row in rows:
            if row.pending_since:
                row.pending_since = utc_now() - timedelta(minutes=31)


async def product_stock(client, shop, product_id) -> int:
    return (await client.get(f"/v1/products/{product_id}", headers=auth_header(shop))).json()[
        "stock_on_hand"
    ]


async def movements(tenant_id: str, reason: str) -> list[StockMovement]:
    async with system_session("test: movements") as db:
        return list(
            (
                await db.scalars(
                    sa.select(StockMovement).where(
                        StockMovement.tenant_id == uuid.UUID(tenant_id),
                        StockMovement.reason == reason,
                    )
                )
            ).all()
        )


def calls(fake, needle: str) -> list[dict]:
    return [
        c
        for c in fake.calls
        if needle in json.dumps(c.get("json_body") or {}) or needle in c["url"]
    ]


async def import_shopify_order(client, fake, shop, conn, domain, number: int):
    fake.shopify_orders[str(number)] = shopify_node(number)
    raw = json.dumps({"id": number}).encode()
    token = await webhook_token(conn["id"])
    await client.post(
        f"/v1/webhooks/integrations/shopify/{token}",
        content=raw,
        headers={
            "X-Shopify-Hmac-Sha256": shopify_hmac(raw),
            "X-Shopify-Shop-Domain": domain,
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": uuid.uuid4().hex,
        },
    )
    await jobs.process_integration_events()
    return await order_for(shop, str(number))


async def order_for(shop, external: str) -> str:
    from app.order_sources.models import ExternalOrder

    async with system_session("test: order for ref") as db:
        order_id = await db.scalar(
            sa.select(ExternalOrder.order_id).where(
                ExternalOrder.tenant_id == uuid.UUID(shop["tenant_id"]),
                ExternalOrder.external_order_id == external,
            )
        )
    assert order_id is not None
    return str(order_id)


async def woo_webhook(client, conn, secret, order, *, topic="order.updated"):
    raw = json.dumps(order).encode()
    token = await webhook_token(conn["id"])
    return await client.post(
        f"/v1/webhooks/integrations/woocommerce/{token}",
        content=raw,
        headers={
            "X-WC-Webhook-Signature": woo_hmac(secret, raw),
            "X-WC-Webhook-Topic": topic,
            "X-WC-Webhook-Delivery-ID": uuid.uuid4().hex,
        },
    )


async def import_woo_order(client, fake, shop, conn, secret, number: int, **overrides):
    fake.woo_orders[number] = woo_order(number, **overrides)
    await woo_webhook(client, conn, secret, fake.woo_orders[number], topic="order.created")
    await jobs.process_integration_events()
    return await order_for(shop, str(number))


async def dispatch(client, shop, order_id, *, tracking="TRK-1", provider="steadfast"):
    result = await client.post(
        f"/v1/consignments/orders/{order_id}/dispatch",
        headers=auth_header(shop),
        json={"provider": provider, "tracking_code": tracking},
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


async def set_status(client, shop, order_id, status):
    result = await client.patch(
        f"/v1/orders/{order_id}", headers=auth_header(shop), json={"status": status}
    )
    assert result.status_code == 200, result.text


# ------------------------------------------------------------ pure rules ---


def test_stock_decisions_never_overwrite_on_ambiguity():
    d = inventory.decide
    now = utc_now()
    old = now - timedelta(hours=1)
    kw = {"now": now, "pending_since": None}
    assert d("NONE", synced=1, basis=1, external=9, local=1, **kw).action == "NOOP"
    assert d("ECOMSBD", synced=None, basis=None, external=5, local=5, **kw).action == "ACCEPT"
    assert (
        d("ECOMSBD", synced=None, basis=None, external=5, local=7, **kw).conflict
        == "STOCK_INITIAL_MISMATCH"
    )
    push = d("ECOMSBD", synced=5, basis=5, external=5, local=8, **kw)
    assert (push.action, push.target) == ("PUSH", 8)
    assert d("ECOMSBD", synced=5, basis=5, external=3, local=5, **kw).action == "WAIT"
    assert (
        d("ECOMSBD", synced=5, basis=5, external=3, local=5, now=now, pending_since=old).conflict
        == "STOCK_CHANGED_EXTERNALLY"
    )
    # A store sale and its imported order: both moved, to the same number.
    assert d("ECOMSBD", synced=5, basis=5, external=3, local=3, **kw).action == "ACCEPT"
    assert (
        d("ECOMSBD", synced=5, basis=5, external=3, local=7, **kw).conflict == "STOCK_CHANGED_BOTH"
    )
    assert d("EXTERNAL", synced=5, basis=5, external=9, local=5, **kw).action == "WAIT"
    pull = d("EXTERNAL", synced=5, basis=5, external=9, local=5, now=now, pending_since=old)
    assert (pull.action, pull.target) == ("PULL", 9)
    assert (
        d("EXTERNAL", synced=5, basis=5, external=5, local=6, **kw).conflict
        == "STOCK_CHANGED_IN_ECOMSBD"
    )


def test_every_emitted_outbox_topic_has_a_handler():
    # A topic without one is re-queued at the head of the outbox forever and
    # starves the topics that do have work.
    for topic in (
        OutboxTopic.ORDER_CREATED,
        OutboxTopic.ORDER_UPDATED,
        OutboxTopic.ORDER_STATUS_CHANGED,
        OutboxTopic.ORDER_BOOKED,
        OutboxTopic.CONSIGNMENT_STATUS_CHANGED,
        OutboxTopic.INVENTORY_CHANGED,
        OutboxTopic.PRODUCT_UPDATED,
    ):
        assert str(topic) in HANDLERS, topic


# --------------------------------------------------------------- mapping ---


async def test_catalog_mapping_by_sku_with_ambiguity_duplicates_and_deletion(
    client, unique_phone, monkeypatch
):
    fake, shop, conn, _ = await shopify_shop(client, unique_phone, monkeypatch)
    headers = auth_header(shop)
    exact = await create_product(client, shop, name="Panjabi", sku="SKU-A", opening_stock=4)
    await create_product(client, shop, name="Plain", sku="AMB")
    kurti = await create_product(client, shop, name="Kurti", sku=None, opening_stock=0)
    variant = await client.post(
        f"/v1/products/{kurti['id']}/variants", headers=headers, json={"name": "Red", "sku": "amb"}
    )
    assert variant.status_code == 201, variant.text
    spare = await create_product(client, shop, name="Loose item", sku="SPARE")
    fake.catalog_pages = [
        [
            shopify_variant(100, 1000, "SKU-A"),
            shopify_variant(101, 1001, "sku-a", title="Copy"),
            shopify_variant(102, 1002, "AMB"),
            shopify_variant(103, 1003, None, title="No SKU"),
        ]
    ]
    await put_sync(client, shop, conn["id"], catalog=True)
    await jobs.run_integration_syncs()
    listing = await links(client, shop, conn["id"])
    state = {row["external_variant_id"]: row for row in listing["items"]}
    assert state["1000"]["state"] == "MATCHED" and state["1000"]["product_id"] == exact["id"]
    assert state["1000"]["internal_name"] == "Panjabi" and state["1000"]["match_source"] == "SKU"
    assert state["1001"]["state"] == "CONFLICT"  # same ecomsbd item claimed twice
    assert state["1002"]["state"] == "CONFLICT"  # SKU fits a product and a variant
    assert state["1003"]["state"] == "UNMATCHED"  # never matched by name
    kinds = {c["kind"] for c in await conflicts(client, shop, connection_id=conn["id"])}
    assert kinds == {"SKU_DUPLICATE", "SKU_AMBIGUOUS"}
    mapped = await client.put(
        f"/v1/integrations/{conn['id']}/links/{state['1003']['id']}",
        headers=headers,
        json={"product_id": spare["id"]},
    )
    assert mapped.status_code == 200 and mapped.json()["state"] == "MATCHED"
    taken = await client.put(
        f"/v1/integrations/{conn['id']}/links/{state['1001']['id']}",
        headers=headers,
        json={"product_id": exact["id"]},
    )
    assert taken.status_code == 409 and taken.json()["details"]["code"] == "ALREADY_MAPPED"
    needs_variant = await client.put(
        f"/v1/integrations/{conn['id']}/links/{state['1002']['id']}",
        headers=headers,
        json={"product_id": kurti["id"]},
    )
    assert needs_variant.json()["details"]["code"] == "VARIANT_REQUIRED"
    fixed = await client.put(
        f"/v1/integrations/{conn['id']}/links/{state['1002']['id']}",
        headers=headers,
        json={"product_id": kurti["id"], "variant_id": variant.json()["variants"][0]["id"]},
    )
    assert fixed.json()["state"] == "MATCHED"
    open_now = await conflicts(client, shop, connection_id=conn["id"])
    assert [c["kind"] for c in open_now] == ["SKU_DUPLICATE"]
    resolved = await client.post(
        f"/v1/integrations/conflicts/{open_now[0]['id']}/resolve",
        headers=headers,
        json={"resolution": "IGNORE_ITEM"},
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED"
    # The store deletes the exact-SKU variant: the next complete scan says so.
    fake.catalog_pages = [
        [shopify_variant(102, 1002, "AMB"), shopify_variant(103, 1003, None, title="No SKU")]
    ]
    run = await client.post(f"/v1/integrations/{conn['id']}/catalog/sync", headers=headers)
    assert run.status_code == 201, run.text
    await jobs.run_integration_syncs()
    gone = await conflicts(client, shop, connection_id=conn["id"])
    assert [c["kind"] for c in gone] == ["EXTERNAL_ITEM_DELETED"]
    deleted = await links(client, shop, conn["id"], state="DELETED")
    assert [row["external_variant_id"] for row in deleted["items"]] == ["1000"]
    # Another shop sees none of it.
    other = await signed_in_shop(client, "019" + unique_phone[3:])
    assert (
        await client.get(f"/v1/integrations/{conn['id']}/links", headers=auth_header(other))
    ).status_code == 404
    assert await conflicts(client, other) == []
    blocked = await client.post(
        f"/v1/integrations/conflicts/{gone[0]['id']}/resolve",
        headers=auth_header(other),
        json={"resolution": "DISMISS"},
    )
    assert blocked.status_code == 404


async def test_new_features_ask_for_their_shopify_scopes(client, unique_phone, monkeypatch):
    enable_shopify(monkeypatch)
    install(monkeypatch)
    shop = await signed_in_shop(client, unique_phone)
    domain = unique_shop()
    conn = await connect_shopify(client, shop, domain)  # granted read_orders only
    view = await put_sync(client, shop, conn["id"], inventory="ECOMSBD", order_status="TWO_WAY")
    assert view["settings"]["catalog"] is True  # stock sync works through mappings
    assert view["capabilities"]["inventory"]["blocker"] == "RECONNECT_FOR_PERMISSION"
    assert {"write_inventory", "read_locations", "write_orders"} <= set(view["reconnect_scopes"])
    assert "write_fulfillments" not in view["reconnect_scopes"]  # not asked for, not needed
    url = (
        await client.post(
            f"/v1/integrations/{conn['id']}/connect",
            headers=auth_header(shop),
            json={"shop": domain},
        )
    ).json()["authorize_url"]
    scope = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["scope"]
    assert "write_inventory" in scope and "write_fulfillments" not in scope
    await _role(shop, "MANAGER")
    denied = await client.put(
        f"/v1/integrations/{conn['id']}/sync-settings",
        headers=auth_header(shop),
        json={"inventory": "NONE"},
    )
    assert denied.status_code == 403


async def _role(shop, role):
    async with system_session("test: role") as db:
        member = await db.scalar(
            sa.select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(shop["tenant_id"]))
        )
        member.role = role


# ------------------------------------------------------------- inventory ---


async def test_ecomsbd_authority_pushes_with_compare_and_swap(client, unique_phone, monkeypatch):
    fake, shop, conn, _ = await shopify_shop(client, unique_phone, monkeypatch)
    headers = auth_header(shop)
    tenant = uuid.UUID(shop["tenant_id"])
    product = await create_product(client, shop, name="Shirt", sku="INV-1", opening_stock=10)
    fake.catalog_pages = [[shopify_variant(200, 2000, "INV-1", item=3000)]]
    fake.levels["3000"] = 10
    await put_sync(client, shop, conn["id"], inventory="ECOMSBD", location_id="1")
    await jobs.run_integration_syncs()
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"ACCEPT": 1}
    await client.post(
        f"/v1/products/{product['id']}/restocks", headers=headers, json={"quantity": 5}
    )
    # Two units sit on an open order that has not left the shelf yet.
    await create_order(
        client, shop, items=[{"product_id": product["id"], "quantity": 2, "unit_price_paisa": 5000}]
    )
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"PUSH": 1}
    await jobs.process_integration_events()
    sets = calls(fake, "inventorySetQuantities")
    assert len(sets) == 1
    change = sets[0]["json_body"]["variables"]["input"]["quantities"][0]
    assert (change["quantity"], change["changeFromQuantity"]) == (13, 10)
    assert "@idempotent(key:" in sets[0]["json_body"]["query"]
    await jobs.process_integration_events()
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"ACCEPT": 1}
    assert len(calls(fake, "inventorySetQuantities")) == 1  # nothing pushed twice
    # Someone edits stock in Shopify admin: waited on, then a conflict.
    fake.levels["3000"] = 11
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"WAIT": 1}
    await age_pending(conn["id"])
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"CONFLICT": 1}
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"FROZEN": 1}
    [conflict] = await conflicts(client, shop, connection_id=conn["id"])
    assert (
        conflict["kind"] == "STOCK_CHANGED_EXTERNALLY" and conflict["recommended"] == "USE_ECOMSBD"
    )
    assert (
        conflict["detail"]["ecomsbd"]["available"] == 13
        and conflict["detail"]["external"]["available"] == 11
    )
    await client.post(
        f"/v1/integrations/conflicts/{conflict['id']}/resolve",
        headers=headers,
        json={"resolution": "USE_EXTERNAL"},
    )
    assert (
        await product_stock(client, shop, product["id"]) == 13
    )  # 15 on hand - 2 to match the store
    [movement] = await movements(shop["tenant_id"], "EXTERNAL_SYNC")
    assert (movement.quantity_delta, movement.source) == (-2, "INTEGRATION")
    # A push that loses a race with the store is dropped, never forced.
    await client.post(
        f"/v1/products/{product['id']}/restocks", headers=headers, json={"quantity": 1}
    )
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"PUSH": 1}
    fake.stale = True
    await jobs.process_integration_events()
    async with system_session("test: stale") as db:
        event = await db.scalar(
            sa.select(IntegrationEvent)
            .where(
                IntegrationEvent.operation == "PUSH_INVENTORY", IntegrationEvent.tenant_id == tenant
            )
            .order_by(IntegrationEvent.created_at.desc())
        )
        assert (event.status, event.code) == ("IGNORED", "STOCK_CHANGED_DURING_PUSH")


async def test_external_authority_pulls_once_and_flags_local_edits(
    client, unique_phone, monkeypatch
):
    fake, shop, conn, _ = await woo_shop(client, unique_phone, monkeypatch)
    headers = auth_header(shop)
    tenant = uuid.UUID(shop["tenant_id"])
    product = await create_product(client, shop, name="Saree", sku="W-1", opening_stock=8)
    fake.woo_products = [
        {
            "id": 10,
            "name": "Saree",
            "type": "simple",
            "status": "publish",
            "sku": "W-1",
            "manage_stock": True,
            "stock_quantity": 8,
            "regular_price": "1000.00",
        }
    ]
    await put_sync(client, shop, conn["id"], inventory="EXTERNAL")
    await jobs.run_integration_syncs()
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"ACCEPT": 1}
    fake.woo_products[0]["stock_quantity"] = 5
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"WAIT": 1}
    await age_pending(conn["id"])
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"PULL": 1}
    assert await product_stock(client, shop, product["id"]) == 5
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"ACCEPT": 1}
    assert len(await movements(shop["tenant_id"], "EXTERNAL_SYNC")) == 1
    await client.post(
        f"/v1/products/{product['id']}/restocks", headers=headers, json={"quantity": 2}
    )
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"CONFLICT": 1}
    [conflict] = await conflicts(client, shop, connection_id=conn["id"])
    assert (
        conflict["kind"] == "STOCK_CHANGED_IN_ECOMSBD" and conflict["recommended"] == "USE_EXTERNAL"
    )
    await client.post(
        f"/v1/integrations/conflicts/{conflict['id']}/resolve",
        headers=headers,
        json={"resolution": "USE_ECOMSBD"},
    )
    await jobs.process_integration_events()
    assert fake.woo_products[0]["stock_quantity"] == 7
    assert (await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))) == {"ACCEPT": 1}


# ----------------------------------------------------- status and tracking ---


async def test_shopify_gets_tracking_delivery_and_cancel_once(client, unique_phone, monkeypatch):
    fake, shop, conn, domain = await shopify_shop(client, unique_phone, monkeypatch)
    await put_sync(client, shop, conn["id"], order_status="TWO_WAY", fulfillment="ON")
    order_id = await import_shopify_order(client, fake, shop, conn, domain, 700)
    await set_status(client, shop, order_id, "CONFIRMED")
    consignment = await dispatch(client, shop, order_id, tracking="STD-777")
    await settle()
    [fulfil] = calls(fake, "fulfillmentCreate")
    info = fulfil["json_body"]["variables"]["fulfillment"]["trackingInfo"]
    assert info == {"company": "Steadfast", "number": "STD-777"}
    await client.post(
        f"/v1/consignments/{consignment}/outcome",
        headers=auth_header(shop),
        json={"status": "DELIVERED"},
    )
    await settle()
    [event] = calls(fake, "fulfillmentEventCreate")
    assert (
        event["json_body"]["variables"]["event"]["fulfillmentId"]
        == "gid://shopify/Fulfillment/9700"
    )
    assert event["json_body"]["variables"]["event"]["status"] == "DELIVERED"
    await settle()
    assert (
        len(calls(fake, "fulfillmentCreate")) == 1
        and len(calls(fake, "fulfillmentEventCreate")) == 1
    )
    # No Shopify push exists for "confirmed": nothing was invented for it.
    async with system_session("test: ops") as db:
        ops = (
            await db.scalars(
                sa.select(IntegrationEvent.operation).where(
                    IntegrationEvent.connection_id == uuid.UUID(conn["id"]),
                    IntegrationEvent.kind == "OUTBOUND",
                )
            )
        ).all()
    assert sorted(ops) == ["PUSH_DELIVERED", "PUSH_FULFILLMENT"]
    second = await import_shopify_order(client, fake, shop, conn, domain, 701)
    await set_status(client, shop, second, "CANCELLED")
    await settle()
    await settle()
    assert len(calls(fake, "orderCancel(")) == 1


async def test_woocommerce_status_note_and_store_cancellations(client, unique_phone, monkeypatch):
    fake, shop, conn, secret = await woo_shop(client, unique_phone, monkeypatch)
    headers = auth_header(shop)
    await put_sync(client, shop, conn["id"], order_status="TWO_WAY", fulfillment="ON")
    first = await import_woo_order(client, fake, shop, conn, secret, 301, status="on-hold")
    await set_status(client, shop, first, "CONFIRMED")
    await settle()
    assert fake.woo_orders[301]["status"] == "processing"
    consignment = await dispatch(client, shop, first, tracking="RDX-9", provider="redx")
    await settle()
    [note] = fake.woo_notes[301]
    assert "RedX" in note["note"] and "RDX-9" in note["note"] and note["customer_note"] is False
    # A retried push finds its own note and does not write another.
    async with system_session("test: requeue") as db:
        event = await db.scalar(
            sa.select(IntegrationEvent).where(
                IntegrationEvent.connection_id == uuid.UUID(conn["id"]),
                IntegrationEvent.operation == "PUSH_FULFILLMENT",
            )
        )
        event.status, event.next_attempt_at = "QUEUED", utc_now()
    await jobs.process_integration_events()
    assert len(fake.woo_notes[301]) == 1
    await client.post(
        f"/v1/consignments/{consignment}/outcome", headers=headers, json={"status": "DELIVERED"}
    )
    await settle()
    assert fake.woo_orders[301]["status"] == "completed"

    # The store cancels an order nothing has been shipped for: ecomsbd follows,
    # and does not echo the cancel back.
    second = await import_woo_order(client, fake, shop, conn, secret, 302)
    fake.woo_orders[302]["status"] = "cancelled"
    await woo_webhook(client, conn, secret, fake.woo_orders[302])
    await jobs.process_integration_events()
    async with system_session("test: cancelled") as db:
        assert (await db.get(Order, uuid.UUID(second))).status == "CANCELLED"
    await settle()
    assert not [c for c in fake.calls if c["method"] == "PUT" and c["url"].endswith("/orders/302")]

    # After booking, a store cancellation is a conflict, not a cancel.
    third = await import_woo_order(client, fake, shop, conn, secret, 303)
    await dispatch(client, shop, third, tracking="RDX-10", provider="redx")
    fake.woo_orders[303]["status"] = "cancelled"
    await woo_webhook(client, conn, secret, fake.woo_orders[303])
    await jobs.process_integration_events()
    async with system_session("test: kept") as db:
        assert (await db.get(Order, uuid.UUID(third))).status != "CANCELLED"
    booked = [c for c in await conflicts(client, shop) if c["kind"] == "CANCELLED_AFTER_BOOKING"]
    assert booked and booked[0]["recommended"] == "CONTACT_COURIER"
    await _role(shop, "VIEWER")
    viewer = await client.post(
        f"/v1/integrations/conflicts/{booked[0]['id']}/resolve",
        headers=headers,
        json={"resolution": "KEEP_ORDER"},
    )
    assert viewer.status_code == 403
    await _role(shop, "ORDER_OPERATOR")
    kept = await client.post(
        f"/v1/integrations/conflicts/{booked[0]['id']}/resolve",
        headers=headers,
        json={"resolution": "KEEP_ORDER"},
    )
    assert kept.status_code == 200, kept.text
    await _role(shop, "OWNER")

    # A changed address is shown side by side and asked about once.
    fourth = await import_woo_order(client, fake, shop, conn, secret, 304)
    fake.woo_orders[304]["billing"] = {**fake.woo_orders[304]["billing"], "address_1": "New road 9"}
    await woo_webhook(client, conn, secret, fake.woo_orders[304])
    await jobs.process_integration_events()
    [changed] = [
        c for c in await conflicts(client, shop) if c["kind"] == "ORDER_CHANGED_EXTERNALLY"
    ]
    assert (
        changed["order_id"] == fourth and "New road 9" in changed["detail"]["external"]["address"]
    )
    assert "New road 9" not in (changed["detail"]["ecomsbd"]["address"] or "")
    await client.post(
        f"/v1/integrations/conflicts/{changed['id']}/resolve",
        headers=headers,
        json={"resolution": "MARK_REVIEWED"},
    )
    await woo_webhook(client, conn, secret, fake.woo_orders[304])
    await jobs.process_integration_events()
    assert not [c for c in await conflicts(client, shop) if c["kind"] == "ORDER_CHANGED_EXTERNALLY"]

    # The store already refunded: ecomsbd does not write over a final state.
    fifth = await import_woo_order(client, fake, shop, conn, secret, 305)
    fake.woo_orders[305]["status"] = "refunded"
    await set_status(client, shop, fifth, "CANCELLED")
    await settle()
    assert fake.woo_orders[305]["status"] == "refunded"
    assert [c["kind"] for c in await conflicts(client, shop) if c["order_id"] == fifth] == [
        "STATUS_DIVERGED"
    ]


# ------------------------------------------------------------ custom website ---


async def test_custom_website_status_api_and_sync_events(client, unique_phone, monkeypatch):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    website = (
        await client.post(
            "/v1/integrations", headers=headers, json={"provider": "CUSTOM_WEBSITE", "name": "Site"}
        )
    ).json()
    conn_id, key = website["connection"]["id"], website["api_key"]
    hook = await client.post(
        f"/v1/integrations/{conn_id}/webhook",
        headers=headers,
        json={"url": "https://shop.example.com/hook"},
    )
    assert hook.status_code == 201
    kurti = await create_product(client, shop, name="Kurti", sku=None, opening_stock=0)
    await client.post(
        f"/v1/products/{kurti['id']}/variants",
        headers=headers,
        json={"name": "Blue", "sku": "K-BLUE"},
    )
    api = {"Authorization": f"Bearer {key}"}
    found = await client.get("/public/v1/products", headers=api, params={"sku": "K-BLUE"})
    assert found.status_code == 200, found.text
    [item] = found.json()["items"]
    assert item["id"] == kurti["id"] and [v["sku"] for v in item["variants"]] == ["K-BLUE"]
    # Changing stock from the website is opt-in, on a replaced key.
    adjust = f"/public/v1/inventory/{kurti['id']}/adjustments"
    body = {"quantity_delta": 2, "variant_id": item["variants"][0]["id"], "note": "Website count"}
    denied = await client.post(adjust, headers={**api, "Idempotency-Key": "stock-0001"}, json=body)
    assert denied.status_code == 403
    rotated = await client.post(
        f"/v1/integrations/{conn_id}/api-key", headers=headers, json={"stock_write": True}
    )
    api = {"Authorization": f"Bearer {rotated.json()['api_key']}"}
    allowed = await client.post(adjust, headers={**api, "Idempotency-Key": "stock-0001"}, json=body)
    assert allowed.status_code == 201, allowed.text
    order = await client.post(
        f"/public/v1/sources/{website['package']['source_id']}/orders",
        headers={**api, "Idempotency-Key": "site-order-1"},
        json={
            "external_order_id": "W1",
            "payload": {
                "phone": "01712345678",
                "items": [{"name": "Kurti", "quantity": 1, "unit_price_paisa": 90000}],
            },
        },
    )
    order_id = order.json()["order_id"]
    confirm = await client.post(
        f"/public/v1/orders/{order_id}/status",
        headers={**api, "Idempotency-Key": "confirm-0001"},
        json={"status": "CONFIRMED"},
    )
    assert confirm.status_code == 200 and confirm.json()["result"] == "UPDATED"
    again = await client.post(
        f"/public/v1/orders/{order_id}/status",
        headers={**api, "Idempotency-Key": "confirm-0001"},
        json={"status": "CONFIRMED"},
    )
    assert again.json() == confirm.json()
    await dispatch(client, shop, order_id, tracking="STD-1")
    late = await client.post(
        f"/public/v1/orders/{order_id}/status",
        headers={**api, "Idempotency-Key": "cancel-0001"},
        json={"status": "CANCELLED"},
    )
    assert late.status_code == 409 and late.json()["code"] == "CANCELLED_AFTER_BOOKING"
    assert [c["kind"] for c in await conflicts(client, shop, connection_id=conn_id)] == [
        "CANCELLED_AFTER_BOOKING"
    ]
    async with system_session("test: deliveries") as db:
        rows = (
            await db.scalars(
                sa.select(WebhookDelivery).where(
                    WebhookDelivery.tenant_id == uuid.UUID(shop["tenant_id"])
                )
            )
        ).all()
    by_type = {row.payload["type"]: row.payload for row in rows}
    assert {"order.confirmed", "order.fulfilled", "tracking.assigned"} <= set(by_type)
    assert by_type["order.confirmed"]["data"] == {
        "order_id": order_id,
        "from": "DRAFT",
        "to": "CONFIRMED",
    }
    assert by_type["tracking.assigned"]["data"]["tracking_code"] == "STD-1"
    assert len({row.event_id for row in rows}) == len(rows)  # one id per delivered topic
    for payload in by_type.values():
        assert "phone" not in json.dumps(payload) and "01712345678" not in json.dumps(payload)


async def test_catalog_and_inventory_events_reach_signed_webhooks(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    headers = auth_header(shop)
    endpoint = await client.post(
        "/v1/developers/webhooks",
        headers=headers,
        json={
            "url": "https://shop.example.com/stock",
            "topics": ["inventory.updated", "product.updated"],
        },
    )
    assert endpoint.status_code == 201, endpoint.text
    product = await create_product(client, shop, name="Mug", sku="MUG-1", opening_stock=2)
    await client.post(
        f"/v1/products/{product['id']}/restocks", headers=headers, json={"quantity": 3}
    )
    await client.patch(
        f"/v1/products/{product['id']}",
        headers=headers,
        json={"default_selling_price_paisa": 45000},
    )
    async with system_session("test: stock deliveries") as db:
        rows = (
            await db.scalars(
                sa.select(WebhookDelivery).where(
                    WebhookDelivery.tenant_id == uuid.UUID(shop["tenant_id"])
                )
            )
        ).all()
    stock = [r.payload["data"] for r in rows if r.payload["type"] == "inventory.updated"]
    assert {
        "product_id": product["id"],
        "variant_id": None,
        "sku": "MUG-1",
        "stock_on_hand": 5,
        "reason": "RESTOCK",
    } in stock
    [updated] = [r.payload["data"] for r in rows if r.payload["type"] == "product.updated"]
    assert updated["changed"] == ["price"] and updated["sku"] == "MUG-1"


# ------------------------------------------------------------ retry safety ---


async def test_outbound_retries_dead_letters_and_reports_auth_expiry(
    client, unique_phone, monkeypatch
):
    fake, shop, conn, _ = await woo_shop(client, unique_phone, monkeypatch)
    headers = auth_header(shop)
    tenant = uuid.UUID(shop["tenant_id"])
    product = await create_product(client, shop, name="Tee", sku="T-1", opening_stock=4)
    fake.woo_products = [
        {
            "id": 20,
            "name": "Tee",
            "type": "simple",
            "status": "publish",
            "sku": "T-1",
            "manage_stock": True,
            "stock_quantity": 4,
            "regular_price": "300.00",
        }
    ]
    await put_sync(client, shop, conn["id"], inventory="ECOMSBD")
    await jobs.run_integration_syncs()
    await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))
    await client.post(
        f"/v1/products/{product['id']}/restocks", headers=headers, json={"quantity": 1}
    )
    await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))
    fake.fail["/products/20 "] = 503
    for _ in range(6):
        async with system_session("test: due now") as db:
            for row in (
                await db.scalars(
                    sa.select(IntegrationEvent).where(IntegrationEvent.tenant_id == tenant)
                )
            ).all():
                row.next_attempt_at = utc_now()
        await jobs.process_integration_events()
    issues = (await client.get("/v1/integrations/issues", headers=headers)).json()["items"]
    [dead] = [i for i in issues if i["kind"] == "OUTBOUND"]
    assert (dead["code"], dead["attempts"], dead["retryable"], dead["action"]) == (
        "PROVIDER_UNAVAILABLE",
        5,
        True,
        "RETRY",
    )
    fake.fail.clear()
    assert (
        await client.post(f"/v1/integrations/events/{dead['id']}/retry", headers=headers)
    ).status_code == 200
    await jobs.process_integration_events()
    assert fake.woo_products[0]["stock_quantity"] == 5
    await client.post(
        f"/v1/products/{product['id']}/restocks", headers=headers, json={"quantity": 1}
    )
    await jobs.run_inventory(tenant, uuid.UUID(conn["id"]))
    fake.fail["/products/20 "] = 401
    await jobs.process_integration_events()
    view = (await client.get(f"/v1/integrations/{conn['id']}", headers=headers)).json()[
        "connection"
    ]
    assert view["state"] == "AUTH_EXPIRED"
    issues = (await client.get("/v1/integrations/issues", headers=headers)).json()["items"]
    assert any(i["code"] == "AUTH_EXPIRED" and i["action"] == "RECONNECT" for i in issues)
    async with system_session("test: sealed") as db:
        row = await db.get(IntegrationConnection, uuid.UUID(conn["id"]))
        assert "cs_abcdef" not in (row.credentials_enc or "")


# -------------------------------------------------------- product authority ---


async def test_product_authority_creates_from_store_or_pushes_prices(
    client, unique_phone, monkeypatch
):
    fake, shop, conn, _ = await shopify_shop(client, unique_phone, monkeypatch)
    headers = auth_header(shop)
    # The store is in charge: an unknown SKU comes in as an ecomsbd product.
    fake.catalog_pages = [[shopify_variant(300, 3000, "NEW-1", price="650.00")]]
    await put_sync(client, shop, conn["id"], products="EXTERNAL")
    await jobs.run_integration_syncs()
    [row] = (await links(client, shop, conn["id"]))["items"]
    assert (row["state"], row["match_source"]) == ("MATCHED", "CREATED")
    created = (await client.get(f"/v1/products/{row['product_id']}", headers=headers)).json()
    assert (created["sku"], created["default_selling_price_paisa"], created["stock_on_hand"]) == (
        "NEW-1",
        65000,
        0,
    )
    # ecomsbd is in charge: a price edit goes to the mapped store variant.
    await put_sync(client, shop, conn["id"], products="ECOMSBD")
    await client.patch(
        f"/v1/products/{row['product_id']}",
        headers=headers,
        json={"default_selling_price_paisa": 70000},
    )
    await settle()
    [push] = calls(fake, "productVariantsBulkUpdate")
    variables = push["json_body"]["variables"]
    assert variables["productId"] == "gid://shopify/Product/300"
    assert variables["variants"] == [{"id": "gid://shopify/ProductVariant/3000", "price": "700.00"}]
    await settle()
    assert len(calls(fake, "productVariantsBulkUpdate")) == 1
