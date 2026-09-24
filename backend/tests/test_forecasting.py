"""V3.6 forecasting: demand, lead times, reorder suggestions, snapshots, accuracy, cash."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import sqlalchemy as sa

from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.clock import business_date, business_day_bounds, utc_now
from app.core.context import use_context
from app.db.session import session_scope, system_session
from app.forecasting.engine import (
    Confidence,
    History,
    LeadTimeSource,
    forecast,
    median_days,
    wape,
)
from app.forecasting.models import DemandForecast
from app.forecasting.service import ForecastService
from app.procurement.models import PurchaseOrder
from app.products.models import Product, StockMovementReason
from app.products.service import StockAdjustment, StockService
from tests.conftest_commerce import create_product, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_integrations import _role
from tests.test_procurement import _order, _po, _receive, _supplier

TODAY = date(2026, 9, 1)


def _h(shop: dict) -> dict[str, str]:
    return auth_header(shop)


def _history(units: list[int], in_stock: list[bool] | None = None) -> History:
    return History(tuple(units), tuple(in_stock if in_stock is not None else [True] * len(units)))


def _noon(days_ago: int) -> datetime:
    start, _ = business_day_bounds(business_date() - timedelta(days=days_ago))
    return start + timedelta(hours=6)


async def _sell(shop: dict, product_id: str, per_day: dict[int, int]) -> None:
    """Units booked out ``days_ago`` → units, oldest first, through the stock ledger."""
    with use_context(tenant_id=uuid.UUID(shop["tenant_id"])):
        async with session_scope() as db:
            stock = StockService(db)
            for days_ago in sorted(per_day, reverse=True):
                await stock.record_movement(
                    StockAdjustment(
                        product_id=uuid.UUID(product_id),
                        quantity_delta=-per_day[days_ago],
                        reason=StockMovementReason.BOOKED_DECREMENT,
                        occurred_at=_noon(days_ago),
                    )
                )


async def _created(product_id: str, days_ago: int) -> None:
    async with system_session("test: backdate product") as db:
        product = await db.get(Product, uuid.UUID(product_id))
        product.created_at = business_day_bounds(business_date() - timedelta(days=days_ago))[0]


async def _selling_item(client, shop, *, name="Borka", opening=70, per_day=2, days=30) -> dict:
    product = await create_product(client, shop, name=name, opening_stock=opening)
    await _created(product["id"], days)
    await _sell(shop, product["id"], dict.fromkeys(range(1, days + 1), per_day))
    return product


# --------------------------------------------------------------- engine ---


def test_too_little_history_is_an_answer_not_a_guess():
    result = forecast(_history([3] * 10), today=TODAY, on_hand=5, incoming=0, lead_time_days=7)
    assert result.confidence is Confidence.INSUFFICIENT
    assert result.rate is None and result.suggested_quantity is None and not result.at_risk


def test_steady_demand_gives_the_textbook_numbers():
    result = forecast(
        _history([2] * 56), today=TODAY, on_hand=10, incoming=0, lead_time_days=7, cover_days=14
    )
    assert result.confidence is Confidence.HIGH
    assert result.rate == 2 and result.safety_stock == 0
    assert result.reorder_point == 14  # 2/day × 7 days
    assert result.suggested_quantity == 32  # 2 × (7 + 14) − 10
    assert result.days_of_cover == 5
    assert result.stockout_on == TODAY + timedelta(days=5)
    assert result.at_risk
    assert result.predicted_units(28) == 56


def test_stock_on_order_counts_against_the_suggestion():
    result = forecast(_history([2] * 56), today=TODAY, on_hand=10, incoming=40, lead_time_days=7)
    assert result.suggested_quantity == 0 and not result.at_risk


def test_days_out_of_stock_are_not_read_as_zero_demand():
    units = [3] * 28 + [0] * 28
    result = forecast(
        _history(units, [True] * 28 + [False] * 28),
        today=TODAY,
        on_hand=0,
        incoming=0,
        lead_time_days=7,
    )
    # 3/day while it could be sold, not 1.5/day across the empty shelf.
    assert result.rate == 3
    assert result.stockout_on == TODAY and result.at_risk


def test_recent_demand_is_blended_with_the_long_average():
    result = forecast(
        _history([1] * 42 + [3] * 14), today=TODAY, on_hand=100, incoming=0, lead_time_days=7
    )
    assert result.long_rate == 1.5 and result.recent_rate == 3
    assert result.rate == 2.25
    assert result.safety_stock is not None and result.safety_stock > 0  # variable demand


def test_accuracy_and_lead_time_arithmetic():
    assert wape([(10, 8), (0, 2)]) == 0.4
    assert wape([(3, 0)]) is None
    assert median_days([5, 1, 3]) == 3
    assert median_days([1, 2, 3, 4]) == 3


# ------------------------------------------------------------------ API ---


async def test_demand_is_read_from_the_stock_ledger(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await _selling_item(client, shop)
    response = await client.get("/v1/forecasting/demand", headers=_h(shop))
    assert response.status_code == 200, response.text
    body = response.json()
    item = next(i for i in body["items"] if i["product_id"] == product["id"])
    assert item["confidence"] == "HIGH"
    assert item["in_stock_days"] == 30  # not the 26 days before the item existed
    assert item["rate_per_day"] == 2.0
    assert item["on_hand"] == 10
    assert item["lead_time_source"] == "DEFAULT" and item["lead_time_days"] == 7
    assert item["suggested_quantity"] == 32
    assert item["at_risk"] and body["counts"]["at_risk"] == 1
    assert body["can_draft"] is True

    detail = await client.get(
        "/v1/forecasting/demand/item", headers=_h(shop), params={"product_id": product["id"]}
    )
    history = detail.json()["history"]
    assert len(history) == 56
    assert history[-1]["date"] == (business_date() - timedelta(days=1)).isoformat()
    assert history[-1]["units"] == 2 and history[-1]["in_stock"]
    assert not history[0]["in_stock"]

    # Another shop sees none of it.
    other = await signed_in_shop(client, "018" + unique_phone[3:])
    assert (await client.get("/v1/forecasting/demand", headers=_h(other))).json()["items"] == []
    missing = await client.get(
        "/v1/forecasting/demand/item", headers=_h(other), params={"product_id": product["id"]}
    )
    assert missing.status_code == 404


async def test_lead_time_is_observed_then_entered_then_default(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await _selling_item(client, shop)
    supplier = await _supplier(client, shop, lead_time_days=10)
    await client.put(
        f"/v1/procurement/suppliers/{supplier['id']}/items",
        headers=_h(shop),
        json={"product_id": product["id"], "unit_cost_paisa": 100, "is_preferred": True},
    )

    async def lead() -> dict:
        body = (await client.get("/v1/forecasting/demand", headers=_h(shop))).json()
        return body["items"][0]

    entered = await lead()
    assert (entered["lead_time_source"], entered["lead_time_days"]) == ("SUPPLIER", 10)
    assert entered["supplier_name"] == "Karim Traders"

    for days in (3, 4, 6):
        created = await _po(
            client,
            shop,
            supplier,
            [{"product_id": product["id"], "quantity": 1, "unit_cost_paisa": 100}],
        )
        po_id = created["purchase_order"]["id"]
        await _order(client, shop, po_id)
        received = await _receive(
            client, shop, po_id, [{"line_id": created["lines"][0]["id"], "accepted": 1}]
        )
        assert received.status_code == 201, received.text
        async with system_session("test: backdate order") as db:
            po = await db.get(PurchaseOrder, uuid.UUID(po_id))
            po.ordered_at = po.first_received_at - timedelta(days=days)
    observed = await lead()
    assert observed["lead_time_source"] == str(LeadTimeSource.OBSERVED)
    assert observed["lead_time_days"] == 4 and observed["lead_time_samples"] == 3


async def test_snapshot_announces_a_new_risk_once_and_raises_the_alert(client, unique_phone):
    from app.notifications.smart import SmartAlerts

    shop = await signed_in_shop(client, unique_phone)
    product = await _selling_item(client, shop)
    await create_product(client, shop, name="New item", opening_stock=5)  # no history
    tenant = uuid.UUID(shop["tenant_id"])

    with use_context(tenant_id=tenant):
        async with session_scope() as db:
            first = await ForecastService(db).snapshot()
        async with session_scope() as db:
            again = await ForecastService(db).snapshot()
        async with session_scope() as db:
            tomorrow = await ForecastService(db).snapshot(business_date() + timedelta(days=1))
        async with session_scope() as db:
            alerts = await SmartAlerts(db).stockouts_predicted()

    assert first == {"stored": 2, "announced": 1}
    assert again == {"stored": 0, "announced": 0}  # once a day
    assert tomorrow["announced"] == 0  # still at risk: not news
    async with system_session("test") as db:
        events = (
            await db.scalars(
                sa.select(OutboxEvent).where(
                    OutboxEvent.tenant_id == tenant,
                    OutboxEvent.topic == str(OutboxTopic.STOCKOUT_PREDICTED),
                )
            )
        ).all()
        rows = (
            await db.scalars(sa.select(DemandForecast).where(DemandForecast.tenant_id == tenant))
        ).all()
    assert [e.payload["product_id"] for e in events] == [product["id"]]
    assert events[0].payload["suggested_quantity"] == 32
    insufficient = [r for r in rows if r.confidence == "INSUFFICIENT"]
    assert insufficient and insufficient[0].predicted_units is None
    assert len(alerts) == 1 and alerts[0].params["names"] == ["Borka"]


async def test_stockout_event_starts_the_workflow_and_drafts_the_suggestion(client, unique_phone):
    from app.automation.actions import perform
    from app.automation.models import AutomationExecution
    from app.automation.triggers import read_event

    shop = await signed_in_shop(client, unique_phone)
    product = await _selling_item(client, shop)
    supplier = await _supplier(client, shop)
    await client.put(
        f"/v1/procurement/suppliers/{supplier['id']}/items",
        headers=_h(shop),
        json={"product_id": product["id"], "unit_cost_paisa": 100, "is_preferred": True},
    )
    tenant = uuid.UUID(shop["tenant_id"])
    with use_context(tenant_id=tenant):
        async with session_scope() as db:
            await ForecastService(db).snapshot()
    async with system_session("test") as db:
        event = await db.scalar(
            sa.select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant,
                OutboxEvent.topic == str(OutboxTopic.STOCKOUT_PREDICTED),
            )
        )
        reading = await read_event(db, event)
    match = reading.matches[0]
    assert match.trigger == "inventory.stockout_predicted"
    assert match.subject["suggested_quantity"] == 32

    step = {
        "id": "draft",
        "type": "action",
        "action": "CREATE_DRAFT_PO",
        "config": {"quantity": 5, "use_suggested": True},
    }
    execution = AutomationExecution(
        id=uuid.uuid4(), tenant_id=tenant, snapshot={"subject": match.subject}, created_at=utc_now()
    )
    with use_context(tenant_id=tenant):
        async with session_scope() as db:
            status, po_id, _ = await perform(db, execution, step)
    assert status == "SUCCEEDED"
    detail = (await client.get(f"/v1/procurement/purchase-orders/{po_id}", headers=_h(shop))).json()
    assert detail["purchase_order"]["status"] == "DRAFT"
    assert detail["lines"][0]["quantity_ordered"] == 32


async def test_seller_drafts_a_reorder_and_roles_are_enforced(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await _selling_item(client, shop)
    quiet = await create_product(client, shop, name="Quiet", opening_stock=5)

    no_supplier = await client.post(
        "/v1/forecasting/draft-purchase-order",
        headers=_h(shop),
        json={"product_id": product["id"]},
    )
    assert no_supplier.status_code == 409
    assert no_supplier.json()["details"]["code"] == "NO_PREFERRED_SUPPLIER"

    supplier = await _supplier(client, shop)
    await client.put(
        f"/v1/procurement/suppliers/{supplier['id']}/items",
        headers=_h(shop),
        json={"product_id": product["id"], "unit_cost_paisa": 100, "is_preferred": True},
    )
    drafted = await client.post(
        "/v1/forecasting/draft-purchase-order",
        headers=_h(shop),
        json={"product_id": product["id"]},
    )
    assert drafted.status_code == 201, drafted.text
    assert drafted.json()["quantity"] == 32
    detail = (
        await client.get(
            f"/v1/procurement/purchase-orders/{drafted.json()['purchase_order_id']}",
            headers=_h(shop),
        )
    ).json()
    assert detail["purchase_order"]["status"] == "DRAFT"
    assert detail["purchase_order"]["source"] == "SELLER"
    nothing = await client.post(
        "/v1/forecasting/draft-purchase-order",
        headers=_h(shop),
        json={"product_id": quiet["id"]},
    )
    assert nothing.status_code == 409
    assert nothing.json()["details"]["code"] == "NO_SUGGESTION"

    await _role(shop, "VIEWER")
    viewer = await client.get("/v1/forecasting/demand", headers=_h(shop))
    assert viewer.status_code == 200 and viewer.json()["can_draft"] is False
    forbidden = await client.post(
        "/v1/forecasting/draft-purchase-order",
        headers=_h(shop),
        json={"product_id": product["id"], "quantity": 3},
    )
    assert forbidden.status_code == 403
    await _role(shop, "ORDER_OPERATOR")
    assert (await client.get("/v1/forecasting/cash", headers=_h(shop))).status_code == 403


async def test_accuracy_scores_only_stored_forecasts(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    tenant = uuid.UUID(shop["tenant_id"])
    empty = (await client.get("/v1/forecasting/accuracy", headers=_h(shop))).json()
    assert empty["status"] == "NOT_ENOUGH_HISTORY"

    as_of = business_date() - timedelta(days=30)
    products = [
        await create_product(client, shop, name=f"Item {i}", opening_stock=100) for i in range(10)
    ]
    for product in products:
        # 20 predicted, 10 sold inside the horizon, 50 more after it (not counted).
        await _sell(shop, product["id"], {29: 10, 1: 50})
    async with system_session("test: stored forecasts") as db:
        for product in products:
            db.add(
                DemandForecast(
                    tenant_id=tenant,
                    as_of=as_of,
                    product_id=uuid.UUID(product["id"]),
                    variant_id=None,
                    item_key=f"{product['id']}:-",
                    confidence="MEDIUM",
                    rate_milli=714,
                    horizon_days=28,
                    predicted_units=20,
                    on_hand=100,
                    incoming=0,
                    lead_time_days=7,
                    at_risk=False,
                )
            )
    scored = (await client.get("/v1/forecasting/accuracy", headers=_h(shop))).json()
    assert scored["status"] == "SCORED"
    assert scored["items"] == 10
    assert (scored["predicted_units"], scored["actual_units"]) == (200, 100)
    assert scored["error_bps"] == 10_000  # |200 − 100| / 100
    assert scored["bias_units"] == 100  # over-forecast


async def test_cash_outlook_sets_supplier_money_against_expected_cod(client, unique_phone):
    shop = await signed_in_shop(client, unique_phone)
    product = await create_product(client, shop, name="Lawn", opening_stock=0)
    supplier = await _supplier(client, shop, payment_terms_days=3)
    created = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 10, "unit_cost_paisa": 10_000}],
    )
    po_id = created["purchase_order"]["id"]
    await _order(client, shop, po_id)
    await _receive(client, shop, po_id, [{"line_id": created["lines"][0]["id"], "accepted": 4}])
    ordered = await _po(
        client,
        shop,
        supplier,
        [{"product_id": product["id"], "quantity": 2, "unit_cost_paisa": 5_000}],
    )
    await _order(client, shop, ordered["purchase_order"]["id"])

    body = (await client.get("/v1/forecasting/cash", headers=_h(shop))).json()
    assert body["outflow"]["next_7_days"] == 40_000  # 4 received × ৳100, due in 3 days
    assert body["outflow"]["overdue"] == 0
    # Not owed yet: the rest of the first order and all of the second.
    assert body["committed_on_open_orders_paisa"] == 60_000 + 10_000
    assert body["net_7_days_paisa"] == body["inflow"].get("next_7_days", 0) - 40_000

    other = await signed_in_shop(client, "018" + unique_phone[3:])
    theirs = (await client.get("/v1/forecasting/cash", headers=_h(other))).json()
    assert theirs["outflow"]["next_7_days"] == 0 and theirs["committed_on_open_orders_paisa"] == 0


async def test_snapshot_job_runs_each_shop_once(client, unique_phone, monkeypatch):
    import app.forecasting.jobs as jobs

    shop = await signed_in_shop(client, unique_phone)
    await _selling_item(client, shop)

    async def only_this_shop() -> list[tuple[uuid.UUID, str]]:
        return [(uuid.UUID(shop["tenant_id"]), "Asia/Dhaka")]

    monkeypatch.setattr(jobs, "_active_tenants", only_this_shop)
    first = await jobs.snapshot_demand_forecasts()
    second = await jobs.snapshot_demand_forecasts()
    assert first == {"stored": 1, "announced": 1, "failed": 0}
    assert second == {"stored": 0, "announced": 0, "failed": 0}
