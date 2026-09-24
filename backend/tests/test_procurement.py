"""V3.5 inventory + procurement: suppliers, purchase orders, receiving, payables, locations."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.common.audit import AuditLog
from app.common.outbox import OutboxEvent
from app.core.clock import utc_now
from app.db.session import session_scope, system_session
from app.orders.models import OrderItem
from app.procurement.models import PurchaseOrder, WarehouseStock
from app.products.models import Product, StockMovement
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_integrations import _role
from tests.test_inventory_v2 import _variant, _variant_product


def _h(shop: dict) -> dict[str, str]:
    return auth_header(shop)


async def _supplier(client, shop, name="Karim Traders", **extra: Any) -> dict:
    response = await client.post(
        "/v1/procurement/suppliers",
        headers=_h(shop),
        json={"name": name, "phone": "01711111111", "payment_terms_days": 7, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _po(client, shop, supplier, lines, **extra: Any) -> dict:
    response = await client.post(
        "/v1/procurement/purchase-orders",
        headers=_h(shop),
        json={"supplier_id": supplier["id"], "lines": lines, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _order(client, shop, po_id) -> dict:
    response = await client.post(f"/v1/procurement/purchase-orders/{po_id}/order", headers=_h(shop))
    assert response.status_code == 200, response.text
    return response.json()


async def _receive(client, shop, po_id, lines, key=None, **extra: Any):
    return await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/receive",
        headers=_h(shop),
        json={"lines": lines, "idempotency_key": key or uuid.uuid4().hex, **extra},
    )


async def _stock(product_id: str) -> int:
    async with system_session("test") as db:
        return (await db.get(Product, uuid.UUID(product_id))).stock_on_hand


async def test_supplier_is_shop_private(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    supplier = await _supplier(client, shop)
    duplicate = await client.post(
        "/v1/procurement/suppliers", headers=_h(shop), json={"name": "  karim   traders "}
    )
    assert duplicate.status_code == 409
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (await client.get("/v1/procurement/suppliers", headers=_h(other))).json()["items"] == []
    assert (
        await client.get(f"/v1/procurement/suppliers/{supplier['id']}", headers=_h(other))
    ).status_code == 404
    product = await create_product(client, other, name="Other shop item")
    assert (
        await client.post(
            "/v1/procurement/purchase-orders",
            headers=_h(other),
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "quantity": 1, "unit_cost_paisa": 100}],
            },
        )
    ).status_code == 404


async def test_purchase_order_lifecycle_partial_damaged_idempotent(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(
        client, shop, name="Cotton Kurti", opening_stock=5, cost_paisa=40_000
    )
    supplier = await _supplier(client, shop)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 10, "unit_cost_paisa": 45_000}],
        expected_at=(utc_now() + timedelta(days=3)).isoformat(),
    )
    po = created["purchase_order"]
    assert po["status"] == "DRAFT" and po["number"] == "PO-00001"
    assert po["total_paisa"] == 450_000
    line = created["lines"][0]
    # Nothing arrives against a draft.
    assert (
        await _receive(client, shop, po["id"], [{"line_id": line["id"], "accepted": 1}])
    ).status_code == 409
    ordered = await _order(client, shop, po["id"])
    assert ordered["purchase_order"]["status"] == "ORDERED"
    stock = await client.get("/v1/procurement/stock", headers=_h(shop))
    row = next(r for r in stock.json()["items"] if r["product_id"] == product["id"])
    assert row["on_hand"] == 5 and row["incoming"] == 10
    assert stock.json()["reservations_tracked"] is False

    key = uuid.uuid4().hex
    body = [{"line_id": line["id"], "accepted": 4, "rejected": 2, "reject_reason": "DAMAGED"}]
    first = await _receive(client, shop, po["id"], body, key=key, supplier_reference="CH-77")
    assert first.status_code == 201, first.text
    assert first.json()["purchase_order"]["status"] == "PARTIALLY_RECEIVED"
    assert await _stock(product["id"]) == 9  # damaged units never enter stock
    replay = await _receive(client, shop, po["id"], body, key=key, supplier_reference="CH-77")
    assert replay.json()["replayed"] is True and await _stock(product["id"]) == 9
    changed = await _receive(
        client, shop, po["id"], [{"line_id": line["id"], "accepted": 5}], key=key
    )
    assert changed.status_code == 409
    no_reason = await _receive(
        client, shop, po["id"], [{"line_id": line["id"], "accepted": 0, "rejected": 1}]
    )
    assert no_reason.status_code == 422

    async with system_session("test") as db:
        movement = await db.scalar(
            sa.select(StockMovement).where(
                StockMovement.product_id == uuid.UUID(product["id"]),
                StockMovement.source == "PURCHASE",
            )
        )
        assert movement.reason == "RESTOCK" and movement.quantity_delta == 4
        assert movement.reference == "PO-00001" and movement.unit_cost_paisa == 45_000
        # Downstream sync hears about received stock through the one ledger event.
        events = (
            await db.scalars(
                sa.select(OutboxEvent).where(
                    OutboxEvent.topic.in_(["inventory.updated", "purchase_order.received"])
                )
            )
        ).all()
        assert {e.topic for e in events if str(e.tenant_id) == shop["tenant_id"]} == {
            "inventory.updated",
            "purchase_order.received",
        }
        recount = await db.scalar(
            sa.select(sa.func.sum(StockMovement.quantity_delta)).where(
                StockMovement.product_id == uuid.UUID(product["id"])
            )
        )
        assert recount == 9

    # More than ordered needs a manager's reason, and is audited.
    over = await _receive(client, shop, po["id"], [{"line_id": line["id"], "accepted": 7}])
    assert over.status_code == 409 and over.json()["details"]["code"] == "OVER_RECEIPT"
    done = await _receive(
        client,
        shop,
        po["id"],
        [{"line_id": line["id"], "accepted": 7}],
        over_receipt_reason="Supplier sent a bonus carton",
    )
    assert done.status_code == 201, done.text
    final = done.json()
    assert final["purchase_order"]["status"] == "RECEIVED"
    assert final["lines"][0]["quantity_received"] == 11
    assert final["lines"][0]["quantity_rejected"] == 2
    assert await _stock(product["id"]) == 16
    async with system_session("test") as db:
        audit = await db.scalar(
            sa.select(AuditLog).where(
                AuditLog.action == "procurement.goods_over_received",
                AuditLog.entity_id == po["id"],
            )
        )
        assert audit is not None
    closed = await _receive(client, shop, po["id"], [{"line_id": line["id"], "accepted": 1}])
    assert closed.status_code == 409


async def test_received_cost_becomes_current_and_history_is_kept(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(client, shop, name="Abaya", opening_stock=5, cost_paisa=50_000)
    earlier = (
        await create_order(
            client,
            shop,
            items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 90_000}],
        )
    )["order"]
    supplier = await _supplier(client, shop)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 3, "unit_cost_paisa": 62_000}],
    )
    await _order(client, shop, created["purchase_order"]["id"])
    received = await _receive(
        client,
        shop,
        created["purchase_order"]["id"],
        [{"line_id": created["lines"][0]["id"], "accepted": 3}],
    )
    assert received.status_code == 201
    async with system_session("test") as db:
        assert (await db.get(Product, uuid.UUID(product["id"]))).cost_paisa == 62_000
        item = await db.scalar(
            sa.select(OrderItem).where(OrderItem.order_id == uuid.UUID(earlier["id"]))
        )
        assert item.unit_cost_snapshot_paisa == 50_000  # past orders keep their cost
    detail = (
        await client.get(f"/v1/procurement/suppliers/{supplier['id']}", headers=_h(shop))
    ).json()
    assert detail["items"][0]["last_unit_cost_paisa"] == 62_000

    # A line that says so leaves the current cost alone.
    keep = await _po(
        client,
        shop,
        supplier,
        [
            {
                "product_id": product["id"],
                "quantity": 1,
                "unit_cost_paisa": 99_000,
                "update_cost": False,
            }
        ],
    )
    await _order(client, shop, keep["purchase_order"]["id"])
    await _receive(
        client,
        shop,
        keep["purchase_order"]["id"],
        [{"line_id": keep["lines"][0]["id"], "accepted": 1}],
    )
    async with system_session("test") as db:
        assert (await db.get(Product, uuid.UUID(product["id"]))).cost_paisa == 62_000


async def test_payables_are_separate_and_bounded(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(client, shop, name="Hijab", opening_stock=0)
    supplier = await _supplier(client, shop)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 10, "unit_cost_paisa": 10_000}],
    )
    po_id = created["purchase_order"]["id"]
    draft_pay = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 1_000, "method": "BKASH", "idempotency_key": uuid.uuid4().hex},
    )
    assert draft_pay.status_code == 409
    await _order(client, shop, po_id)
    # An advance before delivery, up to the ordered value.
    key = uuid.uuid4().hex
    advance = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 30_000, "method": "BKASH", "idempotency_key": key},
    )
    assert advance.status_code == 201, advance.text
    assert advance.json()["payable"]["advance_paisa"] == 30_000
    again = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 30_000, "method": "BKASH", "idempotency_key": key},
    )
    assert again.json()["replayed"] is True
    await _receive(client, shop, po_id, [{"line_id": created["lines"][0]["id"], "accepted": 6}])
    payables = (await client.get("/v1/procurement/payables", headers=_h(shop))).json()
    row = next(r for r in payables["items"] if r["id"] == po_id)
    assert row["payable"]["status"] == "PARTIALLY_PAID"
    assert row["payable"]["balance_paisa"] == 30_000
    assert payables["totals"]["outstanding_paisa"] == 30_000
    # The PO is cancelled: nothing more arrives, so nothing beyond what arrived is payable.
    await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/cancel",
        headers=_h(shop),
        json={"reason": "Supplier out of stock"},
    )
    too_much = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 40_000, "method": "CASH", "idempotency_key": uuid.uuid4().hex},
    )
    assert too_much.status_code == 409 and too_much.json()["details"]["code"] == "OVERPAYMENT"
    paid = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 30_000, "method": "CASH", "idempotency_key": uuid.uuid4().hex},
    )
    assert paid.json()["payable"]["status"] == "PAID"
    # Supplier money never touches the COD / financial ledger.
    async with system_session("test") as db:
        from app.ledger.models import LedgerEntry

        entries = await db.scalar(
            sa.select(sa.func.count(LedgerEntry.id)).where(
                LedgerEntry.tenant_id == uuid.UUID(shop["tenant_id"])
            )
        )
        assert entries == 0


async def test_payment_due_date_raises_the_supplier_alert(client, unique_phone):
    from app.notifications.smart import SmartAlerts

    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(client, shop, name="Borka", opening_stock=0)
    supplier = await _supplier(client, shop, payment_terms_days=7)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 2, "unit_cost_paisa": 5_000}],
        expected_at=(utc_now() - timedelta(days=1)).isoformat(),
    )
    po_id = created["purchase_order"]["id"]
    await _order(client, shop, po_id)
    await _receive(client, shop, po_id, [{"line_id": created["lines"][0]["id"], "accepted": 1}])
    async with system_session("test") as db:
        po = await db.get(PurchaseOrder, uuid.UUID(po_id))
        assert po.payment_due_at is not None  # first receipt + supplier terms
        po.payment_due_at = utc_now() - timedelta(days=1)
        po.first_received_at = utc_now() - timedelta(days=5)
    from app.core.context import use_context

    with use_context(tenant_id=uuid.UUID(shop["tenant_id"])):
        # As the worker runs it: tenant-scoped, so other shops' orders stay out.
        async with session_scope() as db:
            alerts = SmartAlerts(db)
            overdue = await alerts.supplier_payments_overdue()
            late = await alerts.purchase_orders_overdue()
            partial = await alerts.partial_receipts_pending()
    assert overdue[0].amount_paisa == 5_000
    assert late[0].params["numbers"] == ["PO-00001"]
    assert partial[0].params["count"] == 1


async def test_roles(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(client, shop, name="Salwar", opening_stock=0)
    supplier = await _supplier(client, shop)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 5, "unit_cost_paisa": 100}],
    )
    po_id, line_id = created["purchase_order"]["id"], created["lines"][0]["id"]
    await _order(client, shop, po_id)

    await _role(shop, "ORDER_OPERATOR")
    view = await client.get(f"/v1/procurement/purchase-orders/{po_id}", headers=_h(shop))
    assert view.status_code == 200 and view.json()["purchase_order"]["payable"] is None
    assert (
        await _receive(client, shop, po_id, [{"line_id": line_id, "accepted": 1}])
    ).status_code == 403
    assert (await client.get("/v1/procurement/payables", headers=_h(shop))).status_code == 403

    await _role(shop, "MANAGER")
    ok = await _receive(client, shop, po_id, [{"line_id": line_id, "accepted": 2}])
    assert ok.status_code == 201
    pay = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 100, "method": "CASH", "idempotency_key": uuid.uuid4().hex},
    )
    assert pay.status_code == 403  # managers see payables but finance pays

    await _role(shop, "FINANCE")
    assert (await client.get("/v1/procurement/payables", headers=_h(shop))).status_code == 200
    pay = await client.post(
        f"/v1/procurement/purchase-orders/{po_id}/payments",
        headers=_h(shop),
        json={"amount_paisa": 100, "method": "CASH", "idempotency_key": uuid.uuid4().hex},
    )
    assert pay.status_code == 201
    assert (
        await _receive(client, shop, po_id, [{"line_id": line_id, "accepted": 1}])
    ).status_code == 403
    assert (
        await client.post(
            f"/v1/procurement/purchase-orders/{po_id}/cancel",
            headers=_h(shop),
            json={"reason": "no"},
        )
    ).status_code == 403

    await _role(shop, "MANAGER")
    over = await _receive(
        client,
        shop,
        po_id,
        [{"line_id": line_id, "accepted": 9}],
        over_receipt_reason="Bonus stock",
    )
    assert over.status_code == 201


async def test_locations_and_transfers_keep_the_total(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(client, shop, name="Three-piece", opening_stock=10)
    warehouse = await client.post(
        "/v1/procurement/warehouses",
        headers=_h(shop),
        json={"name": "Mirpur godown", "code": "mir"},
    )
    assert warehouse.status_code == 201, warehouse.text
    godown = warehouse.json()
    listed = (await client.get("/v1/procurement/warehouses", headers=_h(shop))).json()["items"]
    assert [w["code"] for w in listed] == ["MAIN", "MIR"]

    line = [{"product_id": product["id"], "quantity": 4}]
    key = uuid.uuid4().hex
    moved = await client.post(
        "/v1/procurement/transfers",
        headers=_h(shop),
        json={"to_warehouse_id": godown["id"], "lines": line, "idempotency_key": key},
    )
    assert moved.status_code == 201, moved.text
    replay = await client.post(
        "/v1/procurement/transfers",
        headers=_h(shop),
        json={"to_warehouse_id": godown["id"], "lines": line, "idempotency_key": key},
    )
    assert replay.json()["replayed"] is True
    assert await _stock(product["id"]) == 10  # a transfer never changes the total
    too_many = await client.post(
        "/v1/procurement/transfers",
        headers=_h(shop),
        json={
            "from_warehouse_id": godown["id"],
            "lines": [{"product_id": product["id"], "quantity": 5}],
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert too_many.status_code == 409
    assert too_many.json()["details"]["code"] == "INSUFFICIENT_LOCATION_STOCK"
    rows = (await client.get("/v1/procurement/stock", headers=_h(shop))).json()["items"]
    row = next(r for r in rows if r["product_id"] == product["id"])
    by_name = {loc["name"]: loc["quantity"] for loc in row["locations"]}
    assert by_name == {"Main": 6, "Mirpur godown": 4}

    # Receiving into the godown adds to the total and to the godown.
    supplier = await _supplier(client, shop)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 3, "unit_cost_paisa": 1_000}],
        warehouse_id=godown["id"],
    )
    await _order(client, shop, created["purchase_order"]["id"])
    await _receive(
        client,
        shop,
        created["purchase_order"]["id"],
        [{"line_id": created["lines"][0]["id"], "accepted": 3}],
    )
    assert await _stock(product["id"]) == 13
    async with system_session("test") as db:
        held = await db.scalar(
            sa.select(WarehouseStock.quantity).where(
                WarehouseStock.warehouse_id == uuid.UUID(godown["id"])
            )
        )
        assert held == 7
        pair = (
            await db.execute(
                sa.select(StockMovement.reason, StockMovement.quantity_delta).where(
                    StockMovement.product_id == uuid.UUID(product["id"]),
                    StockMovement.source == "TRANSFER",
                )
            )
        ).all()
        assert sorted(pair) == [("TRANSFER_IN", 4), ("TRANSFER_OUT", -4)]
    closing = await client.patch(
        f"/v1/procurement/warehouses/{godown['id']}",
        headers=_h(shop),
        json={"name": "Mirpur godown", "code": "MIR", "is_active": False},
    )
    assert closing.status_code == 409 and closing.json()["details"]["code"] == "LOCATION_NOT_EMPTY"


async def test_variant_items_and_draft_po_from_automation(client, unique_phone):
    from app.automation.actions import perform
    from app.automation.models import AutomationExecution
    from app.core.context import use_context

    shop = await signed_in_shop(client, unique_phone)
    product = await _variant_product(client, shop, black_m=2)
    variant = _variant(product, "Black / M")
    supplier = await _supplier(client, shop)
    missing = await client.post(
        "/v1/procurement/purchase-orders",
        headers=_h(shop),
        json={
            "supplier_id": supplier["id"],
            "lines": [{"product_id": product["id"], "quantity": 1, "unit_cost_paisa": 1}],
        },
    )
    assert missing.status_code == 422
    link = await client.put(
        f"/v1/procurement/suppliers/{supplier['id']}/items",
        headers=_h(shop),
        json={
            "product_id": product["id"],
            "variant_id": variant["id"],
            "unit_cost_paisa": 30_000,
            "is_preferred": True,
        },
    )
    assert link.status_code == 200, link.text
    tenant = uuid.UUID(shop["tenant_id"])
    step = {
        "id": "draft",
        "type": "action",
        "action": "CREATE_DRAFT_PO",
        "config": {"quantity": 12},
    }
    execution = AutomationExecution(
        id=uuid.uuid4(),
        tenant_id=tenant,
        snapshot={"subject": {"product_id": product["id"], "variant_id": variant["id"]}},
        created_at=utc_now(),
    )

    # As the dispatcher runs it: in the shop's own scope.
    with use_context(tenant_id=tenant):
        async with session_scope() as db:
            status, po_id, _ = await perform(db, execution, step)
            again = await perform(db, execution, step)
    assert status == "SUCCEEDED"
    assert again == ("SKIPPED", None, "ALREADY_ON_ORDER")
    detail = (await client.get(f"/v1/procurement/purchase-orders/{po_id}", headers=_h(shop))).json()
    assert detail["purchase_order"]["status"] == "DRAFT"  # prepared, never ordered
    assert detail["purchase_order"]["source"] == "AUTOMATION"
    assert detail["lines"][0]["quantity_ordered"] == 12
    assert detail["lines"][0]["unit_cost_paisa"] == 30_000


@pytest.mark.postgres
async def test_concurrent_receipts_cannot_over_receive(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    async with system_session("test") as db:
        if db.bind.dialect.name != "postgresql":
            pytest.skip("PostgreSQL only")
    product = await create_product(client, shop, name="Lawn", opening_stock=0)
    supplier = await _supplier(client, shop)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 5, "unit_cost_paisa": 100}],
    )
    po_id, line_id = created["purchase_order"]["id"], created["lines"][0]["id"]
    await _order(client, shop, po_id)
    results = await asyncio.gather(
        *(_receive(client, shop, po_id, [{"line_id": line_id, "accepted": 5}]) for _ in range(3))
    )
    assert sorted(r.status_code for r in results) == [201, 409, 409]
    assert await _stock(product["id"]) == 5
