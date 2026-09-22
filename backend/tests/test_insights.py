"""Advanced Insights.

The properties under test:

*   windows: 7/30/90 presets and bounded custom ranges; the previous period is
    the equivalent one before; a percentage is only stated over a real base;
*   every figure is the owning module's figure — RTO from the RTO classifier,
    revenue/profit from profit snapshots, receivables from cashflow,
    discrepancies from reconciliation, stock from the stock summary;
*   products, couriers, customers and inventory aggregate without inventing
    anything: thin samples are not ranked, unknown profit is null, slow-moving
    is "stock and no sale in N days", couriers are listed, never ranked;
*   one shop never sees another's figures, and money follows role and plan.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.insights import Window, compare, resolve_window
from app.core.clock import business_date, utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ValidationError
from app.orders.models import Order
from app.products.models import Product
from app.tenants.roles import TenantRole
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_cashflow import _pay
from tests.test_reconciliation import delivered_parcel
from tests.test_rto import _Shop
from tests.test_team import _member_session

BASE = "/v1/analytics/insights"


def _phone() -> str:
    return f"017{uuid.uuid4().int % 100_000_000:08d}"


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> _Shop:
    session = await signed_in_shop(client, unique_phone, shop_name="Insight Shop", plan="pro")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return _Shop(client, session)


async def _backdate(db: AsyncSession, model: Any, column: str, ids: list[str], when: Any) -> None:
    """Move rows into the past, the one thing the HTTP API rightly cannot do."""
    await db.execute(
        sa.update(model)
        .where(model.id.in_([uuid.UUID(value) for value in ids]))
        .values({column: when})
        .execution_options(synchronize_session=False)
    )
    await db.commit()


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #


class TestWindows:
    def test_presets_and_the_previous_period(self) -> None:
        today = date(2026, 9, 19)
        window = resolve_window(days=30, today=today)
        assert (window.since, window.until, window.days) == (date(2026, 8, 21), today, 30)
        previous = window.previous()
        assert (previous.since, previous.until) == (date(2026, 7, 22), date(2026, 8, 20))
        assert resolve_window(today=today).days == 30
        assert resolve_window(days=7, today=today).since == date(2026, 9, 13)

    def test_custom_ranges_are_bounded(self) -> None:
        today = date(2026, 9, 19)
        custom = resolve_window(since=date(2026, 1, 1), until=date(2026, 1, 10), today=today)
        assert custom == Window(date(2026, 1, 1), date(2026, 1, 10))
        for kwargs in (
            {"days": 14},
            {"since": date(2026, 1, 1)},
            {"since": date(2026, 2, 1), "until": date(2026, 1, 1)},
            {"since": date(2025, 1, 1), "until": date(2026, 9, 1)},
            {"since": date(2026, 9, 1), "until": date(2026, 9, 20)},
        ):
            with pytest.raises(ValidationError):
                resolve_window(today=today, **kwargs)  # type: ignore[arg-type]

    def test_a_percentage_needs_a_real_base(self) -> None:
        assert compare(12, 10, floor=5).change_bps == 2000
        assert compare(3, 0, floor=5).change_bps is None
        assert compare(9, 2, floor=5).change_bps is None  # "+350%" on two orders is noise
        assert compare(-50_000, 200_000, floor=100_000).change_bps == -12500
        assert compare(100_000, -20_000, floor=100_000).change_bps is None


# --------------------------------------------------------------------------- #
# Overview and trend
# --------------------------------------------------------------------------- #


class TestOverview:
    async def test_many_products_in_one_return_are_still_a_thin_sample(self, shop: _Shop) -> None:
        await shop.parcel("RETURNED", products=tuple(f"Scarf {n}" for n in range(10)))
        body = await shop.get(f"{BASE}/overview", days=30)
        assert body["rto"]["completed"] == 1
        assert all(e["code"] != "RETURN_VALUE_CONCENTRATED" for e in body["explanations"])

    async def test_an_empty_shop_is_zero_with_no_percentages(self, shop: _Shop) -> None:
        body = await shop.get(f"{BASE}/overview", days=30)
        assert body["window"]["days"] == 30
        assert body["orders"] == {"current": 0, "previous": 0, "change_basis_points": None}
        assert body["rto"]["rto_rate_basis_points"] is None
        assert body["rto_trend"] == "INSUFFICIENT_DATA"
        assert body["money_locked"] is None
        assert body["money"]["revenue"]["change_basis_points"] is None
        assert body["money"]["receivable_paisa"] == 0
        assert body["explanations"] == []

    async def test_every_figure_is_the_owning_modules_figure(self, shop: _Shop) -> None:
        for outcome in ("DELIVERED", "DELIVERED", "RETURNED"):
            await shop.parcel(outcome)
        await shop.parcel("CANCELLED", provider="steadfast")
        await shop.parcel(None)

        body = await shop.get(f"{BASE}/overview", days=30)
        rto = await shop.get("/v1/analytics/rto/summary", days=30)
        profit = await shop.get("/v1/analytics/profit")
        flow = await shop.get("/v1/money/cashflow")
        stock = await shop.get("/v1/products/stock-summary")

        # RTO: the canonical classifier, same counts as the RTO screen.
        assert body["rto"] == rto["counts"]
        assert body["delivered"]["current"] == 2
        assert body["orders"]["current"] == 5
        # Money: profit snapshots and Module 2 cashflow, not a second sum.
        assert body["money"]["revenue"]["current"] == profit["realized_revenue_paisa"]
        assert body["money"]["profit"]["current"] == profit["contribution_profit_paisa"]
        assert sum(body["money"]["profit_quality"].values()) == profit["parcel_count"]
        assert body["money"]["receivable_paisa"] == flow["receivable_paisa"]
        assert body["money"]["in_transit_paisa"] == flow["in_transit_paisa"]
        # Stock: the Module 5 summary.
        assert body["stock"]["low_stock_items"] == stock["low_stock_items"]
        assert body["stock"]["out_of_stock_items"] == stock["out_of_stock_items"]

    async def test_a_change_is_stated_only_over_a_meaningful_base(
        self, shop: _Shop, db: AsyncSession
    ) -> None:
        earlier = [(await shop.order())["id"] for _ in range(5)]
        await _backdate(db, Order, "business_date", earlier, business_date() - timedelta(days=10))
        for _ in range(8):
            await shop.order()

        body = await shop.get(f"{BASE}/overview", days=7)
        assert body["orders"] == {"current": 8, "previous": 5, "change_basis_points": 6000}
        [said] = [e for e in body["explanations"] if e["code"] == "ORDERS_CHANGED"]
        assert said["params"] == {"change_bps": 6000, "days": 7}


class TestTrend:
    async def test_daily_buckets_keep_empty_days_and_add_up(self, shop: _Shop) -> None:
        await shop.parcel("DELIVERED")
        body = await shop.get(f"{BASE}/trend", days=7)
        profit = await shop.get("/v1/analytics/profit")
        assert body["granularity"] == "day"
        assert len(body["buckets"]) == 7
        assert sum(b["revenue_paisa"] for b in body["buckets"]) == profit["realized_revenue_paisa"]
        assert sum(b["orders"] for b in body["buckets"]) == 1
        assert body["buckets"][-1]["parcels"] == 1

    async def test_a_long_range_folds_into_weeks(self, shop: _Shop) -> None:
        today = business_date()
        body = await shop.get(
            f"{BASE}/trend",
            since=(today - timedelta(days=119)).isoformat(),
            until=today.isoformat(),
        )
        assert body["granularity"] == "week"
        assert len(body["buckets"]) == 18
        assert body["buckets"][-1]["end"] == today.isoformat()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


class TestProducts:
    async def test_multiple_lines_of_one_product_count_one_parcel(self, shop: _Shop) -> None:
        await shop.parcel("DELIVERED", products=("Scarf", "Scarf"))
        body = await shop.get(f"{BASE}/products", days=30)
        [row] = body["items"]
        profit = await shop.get("/v1/analytics/profit")
        assert row["parcels"] == row["rto"]["completed"] == 1
        assert row["units_delivered"] == 2
        assert row["revenue_paisa"] == profit["realized_revenue_paisa"]
        assert row["profit_paisa"] == profit["contribution_profit_paisa"]

    async def test_custom_range_excludes_later_outcomes(self, shop: _Shop) -> None:
        await shop.parcel("RETURNED", provider="steadfast")
        yesterday = business_date() - timedelta(days=1)
        params = {"since": yesterday.isoformat(), "until": yesterday.isoformat()}
        products = await shop.get(f"{BASE}/products", **params)
        assert all(row["parcels"] == row["rto"]["completed"] == 0 for row in products["items"])
        couriers = await shop.get(f"{BASE}/couriers", **params)
        assert all(row["counts"]["completed"] == 0 for row in couriers["items"])

    async def test_a_row_joins_sales_rto_and_stock(self, shop: _Shop) -> None:
        for outcome in ("DELIVERED", "DELIVERED", "RETURNED"):
            await shop.parcel(outcome, products=("Silk Hijab",))

        body = await shop.get(f"{BASE}/products", days=30)
        [row] = [r for r in body["items"] if r["name"] == "Silk Hijab"]
        assert row["parcels"] == 3
        assert row["units_delivered"] == 2
        assert row["revenue_paisa"] > 0
        assert row["rto"]["completed"] == 3
        assert row["rto"]["rto"] == 1
        assert row["rto_value_paisa"] == 100_000
        assert row["units_booked"] == 3
        assert row["stock_on_hand"] == 497
        assert row["stock_status"] == "OK"
        # Three parcels is a thin sample: never ranked as high-RTO.
        assert row["rto"]["sufficient"] is False
        high = await shop.get(f"{BASE}/products", days=30, category="high_rto")
        assert high["items"] == []
        assert body["counts"]["high_rto"] == 0

    async def test_unknown_cost_is_not_a_profit(self, shop: _Shop) -> None:
        created = await create_order(
            shop.client,
            shop.session,
            items=[{"name": "Loose Scarf", "quantity": 1, "unit_price_paisa": 50_000}],
            cod_amount_paisa=50_000,
        )
        dispatch = await shop.client.post(
            f"/v1/consignments/orders/{created['order']['id']}/dispatch",
            json={"provider": "manual"},
            headers=shop.headers,
        )
        await shop.client.post(
            f"/v1/consignments/{dispatch.json()['id']}/outcome",
            json={"status": "DELIVERED"},
            headers=shop.headers,
        )
        body = await shop.get(f"{BASE}/products", days=30)
        [row] = [r for r in body["items"] if r["name"] == "Loose Scarf"]
        assert row["product_id"] is None
        assert row["profit_quality"] == "MISSING"
        assert row["profit_paisa"] is None
        assert row["margin_basis_points"] is None
        top = await shop.get(f"{BASE}/products", days=30, category="top_profit")
        assert all(r["name"] != "Loose Scarf" for r in top["items"])

    async def test_slow_moving_is_stock_with_no_recent_sale(
        self, shop: _Shop, db: AsyncSession
    ) -> None:
        old = await create_product(shop.client, shop.session, name="Old Stock", opening_stock=24)
        sold = await shop.product("Sold Stock")
        await shop.parcel("DELIVERED", products=("Sold Stock",))
        await create_product(shop.client, shop.session, name="New Stock", opening_stock=5)
        long_ago = utc_now() - timedelta(days=45)
        await _backdate(db, Product, "created_at", [old["id"], sold["id"]], long_ago)

        body = await shop.get(f"{BASE}/products", days=30, category="slow_moving")
        assert [r["name"] for r in body["items"]] == ["Old Stock"]
        assert body["items"][0]["stock_on_hand"] == 24
        assert body["slow_moving_days"] == 30

        inventory = await shop.get(f"{BASE}/inventory", days=30, filter="slow_moving")
        assert [i["name"] for i in inventory["items"]] == ["Old Stock"]
        overview = await shop.get(f"{BASE}/overview", days=30)
        assert overview["stock"]["slow_moving_items"] == 1
        [said] = [e for e in overview["explanations"] if e["code"] == "SLOW_MOVING"]
        assert said["params"] == {"count": 1, "days": 30}

    async def test_the_table_is_paged_on_the_server(self, shop: _Shop) -> None:
        for name in ("Alpha", "Beta", "Gamma"):
            await create_product(shop.client, shop.session, name=name)
        first = await shop.get(f"{BASE}/products", days=30, limit=2)
        assert (len(first["items"]), first["total"], first["has_more"]) == (2, 3, True)
        rest = await shop.get(f"{BASE}/products", days=30, limit=2, offset=2)
        assert (len(rest["items"]), rest["has_more"]) == (1, False)
        names = [r["name"] for r in first["items"] + rest["items"]]
        assert sorted(names) == ["Alpha", "Beta", "Gamma"]


# --------------------------------------------------------------------------- #
# Couriers, cash, reconciliation
# --------------------------------------------------------------------------- #


class TestCouriers:
    async def test_scorecards_are_facts_in_alphabetical_order(self, shop: _Shop) -> None:
        await shop.parcel("DELIVERED", provider="steadfast")
        await shop.parcel("CANCELLED", provider="steadfast")
        await shop.parcel("DELIVERED")
        await shop.parcel(None, provider="pathao")

        body = await shop.get(f"{BASE}/couriers", days=30)
        providers = [card["provider"] for card in body["items"]]
        assert providers == sorted(providers)
        assert "redx" not in providers
        assert body["excluded_providers"] == ["redx"]
        by = {card["provider"]: card for card in body["items"]}
        assert by["steadfast"]["counts"]["rto"] == 1
        assert by["steadfast"]["counts"]["sufficient"] is False
        assert by["pathao"]["in_transit_now"] == 1
        assert by["manual"]["outstanding_paisa"] is not None
        assert "best" not in str(body).lower()


class TestCashAndReconciliation:
    async def test_cash_is_module_two_cashflow(
        self, client: AsyncClient, shop: _Shop, db: AsyncSession
    ) -> None:
        await delivered_parcel(client, shop.session, db, cod_paisa=100_000, delivered_days_ago=10)
        await delivered_parcel(client, shop.session, db, cod_paisa=40_000, delivered_days_ago=1)

        body = await shop.get(f"{BASE}/cash", days=30)
        flow = await shop.get("/v1/money/cashflow")
        assert body["receivable_paisa"] == flow["receivable_paisa"] == 140_000
        assert body["overdue_paisa"] == flow["overdue_paisa"] == 100_000
        assert sum(row["amount_paisa"] for row in body["aging"]) == 140_000
        assert sum(w["amount_paisa"] for w in body["forecast"]) == body["receivable_paisa"]
        codes = {e["code"]: e["params"] for e in body["explanations"]}
        assert codes["OVERDUE_CONCENTRATED"]["provider"] == "manual"
        assert codes["OVERDUE_CONCENTRATED"]["share_bps"] == 10_000
        assert codes["RECEIVABLE_AGE_SHARE"]["bucket"] == "8-14 days"

    async def test_reconciliation_is_module_one(
        self, client: AsyncClient, shop: _Shop, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop.session, db, cod_paisa=100_000)
        parcel["cod_paisa"] = 60_000  # the courier paid short
        await _pay(db, parcel, paid_days_ago=0, name="short.csv")

        body = await shop.get(f"{BASE}/reconciliation", days=30)
        summary = await shop.get(
            "/v1/reconciliation/summary",
            date_from=(business_date() - timedelta(days=29)).isoformat(),
            date_to=business_date().isoformat(),
        )
        assert body["discrepancy_count"] >= 1
        assert body["open_case_count"] == summary["open_cases"]
        [courier] = body["by_courier"]
        assert courier["provider"] == "manual"
        overview = await shop.get(f"{BASE}/overview", days=30)
        assert overview["money"]["discrepancy_count"] == courier["count"]
        assert overview["money"]["discrepancy_paisa"] == courier["amount_paisa"]


# --------------------------------------------------------------------------- #
# Customers and inventory
# --------------------------------------------------------------------------- #


class TestCustomers:
    async def test_new_returning_and_repeat(self, shop: _Shop, db: AsyncSession) -> None:
        regular, first_timer = _phone(), _phone()
        earlier = await shop.order(phone=regular)
        await _backdate(
            db, Order, "business_date", [earlier["id"]], business_date() - timedelta(days=40)
        )
        await shop.order(phone=regular)
        await shop.order(phone=regular)
        await shop.order(phone=first_timer)

        body = await shop.get(f"{BASE}/customers", days=30)
        assert body["active"]["current"] == 2
        assert body["new"]["current"] == 1
        assert body["returning"] == 1
        assert body["orders_with_customer"] == 3
        assert body["repeat_orders"] == 2
        assert body["repeat_order_rate_basis_points"] == 6667
        assert body["repeat_customers"] == 1
        # Three orders is too few to state a repeat-order share as a pattern.
        assert body["sufficient"] is False
        assert all(e["code"] != "REPEAT_ORDER_SHARE" for e in body["explanations"])
        text = str(body).lower()
        assert "phone" not in text and regular not in text


class TestInventory:
    async def test_items_are_variants_and_counts_match_the_summary(self, shop: _Shop) -> None:
        product = await create_product(shop.client, shop.session, name="T-Shirt", opening_stock=0)
        for name, stock in (("Black / M", 0), ("Black / L", 5)):
            response = await shop.client.post(
                f"/v1/products/{product['id']}/variants",
                json={"name": name, "opening_stock": stock, "low_stock_threshold": 2},
                headers=shop.headers,
            )
            assert response.status_code == 201, response.text
        await create_product(
            shop.client, shop.session, name="Cap", opening_stock=1, low_stock_threshold=2
        )

        body = await shop.get(f"{BASE}/inventory", days=30)
        summary = await shop.get("/v1/products/stock-summary")
        assert body["counts"]["out_of_stock"] == summary["out_of_stock_items"] == 1
        assert body["counts"]["low_stock"] == summary["low_stock_items"] == 2
        assert body["counts"]["all"] == 3
        out = await shop.get(f"{BASE}/inventory", days=30, filter="out_of_stock")
        assert [(i["name"], i["variant_name"]) for i in out["items"]] == [("T-Shirt", "Black / M")]
        codes = {e["code"] for e in body["explanations"]}
        assert {"OUT_OF_STOCK", "LOW_STOCK"} <= codes


# --------------------------------------------------------------------------- #
# Isolation, roles, plans, validation
# --------------------------------------------------------------------------- #


class TestAccess:
    async def test_another_shop_sees_none_of_it(self, client: AsyncClient, shop: _Shop) -> None:
        await shop.parcel("DELIVERED")
        other = _Shop(client, await signed_in_shop(client, _phone(), shop_name="Other", plan="pro"))
        overview = await other.get(f"{BASE}/overview", days=30)
        assert overview["orders"]["current"] == 0
        assert overview["money"]["revenue"]["current"] == 0
        assert overview["rto"]["completed"] == 0
        assert (await other.get(f"{BASE}/products", days=30))["total"] == 0
        assert (await other.get(f"{BASE}/couriers", days=30))["items"] == []
        assert (await other.get(f"{BASE}/customers", days=30))["total_customers"] == 0
        assert (await other.get(f"{BASE}/inventory", days=30))["total_units"] == 0
        assert (await other.get(f"{BASE}/cash", days=30))["receivable_paisa"] == 0
        assert (await other.get(f"{BASE}/reconciliation", days=30))["open_case_count"] == 0

    async def test_money_follows_the_role(self, client: AsyncClient, shop: _Shop) -> None:
        await shop.parcel("DELIVERED")
        viewer = await _member_session(client, shop.session, _phone(), TenantRole.VIEWER)
        finance = await _member_session(client, shop.session, _phone(), TenantRole.FINANCE)

        def get(session: dict[str, Any], path: str, **params: Any) -> Any:
            return client.get(f"{BASE}/{path}", params=params, headers=auth_header(session))

        seen = (await get(viewer, "overview", days=30)).json()
        assert seen["money"] is None and seen["money_locked"] == "PERMISSION"
        assert seen["orders"]["current"] == 1
        assert seen["stock"] is not None
        assert all(e["code"] not in ("PROFIT_CHANGED", "OPEN_CASES") for e in seen["explanations"])
        trend = (await get(viewer, "trend", days=7)).json()
        assert all(b["revenue_paisa"] is None for b in trend["buckets"])
        rows = (await get(viewer, "products", days=30)).json()
        assert all(r["revenue_paisa"] is None for r in rows["items"])
        assert "top_revenue" not in rows["counts"]
        assert (await get(viewer, "products", days=30, category="top_revenue")).status_code == 403
        cards = (await get(viewer, "couriers", days=30)).json()
        assert all(c["outstanding_paisa"] is None for c in cards["items"])
        assert (await get(viewer, "cash", days=30)).status_code == 403
        assert (await get(viewer, "reconciliation", days=30)).status_code == 403
        assert (await get(viewer, "customers", days=30)).status_code == 200

        assert (await get(finance, "cash", days=30)).status_code == 200
        assert (await get(finance, "customers", days=30)).status_code == 403
        assert (await get(finance, "overview", days=30)).json()["money"] is not None

    async def test_money_follows_the_plan(self, client: AsyncClient) -> None:
        free = _Shop(client, await signed_in_shop(client, _phone(), shop_name="Free"))
        body = await free.get(f"{BASE}/overview", days=7)
        assert body["money"] is None and body["money_locked"] == "PLAN"
        assert body["orders"]["current"] == 0

        starter = _Shop(
            client, await signed_in_shop(client, _phone(), shop_name="Starter", plan="starter")
        )
        assert (await starter.get(f"{BASE}/overview", days=30))["money_locked"] is None
        rows = await starter.get(f"{BASE}/products", days=30)
        assert rows["money_locked"] == "PLAN"
        refused = await client.get(
            f"{BASE}/products",
            params={"days": 30, "category": "top_profit"},
            headers=starter.headers,
        )
        assert refused.status_code in (402, 403)

    async def test_bad_ranges_are_refused(self, shop: _Shop) -> None:
        today = business_date()
        for params in (
            {"days": 14},
            {"since": today.isoformat()},
            {"since": (today - timedelta(days=400)).isoformat(), "until": today.isoformat()},
        ):
            response = await shop.client.get(
                f"{BASE}/overview", params=params, headers=shop.headers
            )
            assert response.status_code in (400, 422), (params, response.text)
