"""Analytics, expenses and the notification centre, over HTTP.

Master spec sections 1.1, 19, 39, 86, 94 and 135. These drive the whole Phase
E surface the way the Flutter app will: create an order, dispatch it, record
an outcome, then read the screens. A parcel that reaches a terminal outcome
must appear in Home and Insights *without anybody asking for a snapshot* —
that is the property most of these tests exist to hold.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header

from app.core.clock import business_date, utc_now
from app.core.context import RequestContext, set_context
from app.notifications.models import NotificationKind, Severity
from app.notifications.service import NotificationService


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict[str, Any]:
    return await signed_in_shop(client, unique_phone, shop_name="Insights Shop", plan="pro")


async def _parcel(
    client: AsyncClient,
    shop: dict[str, Any],
    *,
    cod_paisa: int = 140_500,
    outcome: str = "DELIVERED",
    product_name: str = "Cotton Abaya",
    cost_paisa: int = 65_000,
    provider: str = "manual",
    return_reason: str | None = None,
) -> dict[str, Any]:
    """An order taken to a terminal outcome, entirely over HTTP."""
    product = await create_product(
        client,
        shop,
        name=product_name,
        sku=f"sku-{product_name.replace(' ', '-').lower()}",
        cost_paisa=cost_paisa,
        opening_stock=100,
    )
    created = await create_order(
        client,
        shop,
        items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": cod_paisa}],
        cod_amount_paisa=cod_paisa,
    )
    order = created["order"]

    dispatch = await client.post(
        f"/v1/consignments/orders/{order['id']}/dispatch",
        json={"provider": provider},
        headers=auth_header(shop),
    )
    assert dispatch.status_code == 201, dispatch.text

    body: dict[str, Any] = {"status": outcome}
    if return_reason is not None:
        body["return_reason"] = return_reason
    response = await client.post(
        f"/v1/consignments/{dispatch.json()['id']}/outcome",
        json=body,
        headers=auth_header(shop),
    )
    assert response.status_code == 200, response.text
    return {"order": order, "product": product, "consignment": response.json()}


async def _home(client: AsyncClient, shop: dict[str, Any]) -> dict[str, Any]:
    response = await client.get("/v1/analytics/home", headers=auth_header(shop))
    assert response.status_code == 200, response.text
    return response.json()


class TestHome:
    async def test_an_empty_shop_gets_zeros_not_an_error(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        body = await _home(client, shop)
        assert body["as_of"] == business_date(at=utc_now()).isoformat()
        assert body["orders_today"] == 0
        assert body["cod_outstanding_paisa"] == 0
        # Section 23: an alert with nothing in it is not shown at all.
        assert body["alerts"] == []

    async def test_a_delivery_shows_up_without_anyone_asking_for_a_snapshot(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        body = await _home(client, shop)

        assert body["orders_today"] == 1
        assert body["delivered_today"] == 1
        assert body["returned_today"] == 0
        assert body["realized_revenue_paisa"] == 140_500
        assert body["contribution_profit_paisa"] == 75_500
        # Delivered and unpaid, so the courier is still holding the money.
        assert body["cod_outstanding_paisa"] == 140_500

    async def test_gross_sales_and_realized_revenue_are_not_the_same_number(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=100_000)
        await _parcel(client, shop, cod_paisa=60_000, outcome="RETURNED", product_name="Silk Hijab")
        body = await _home(client, shop)

        # Both orders were placed; only one collected anything. Section 1.1
        # lists them separately because a seller who conflates them thinks a
        # busy day was a profitable one.
        assert body["gross_sales_paisa"] == 160_000
        assert body["realized_revenue_paisa"] == 100_000
        assert body["returned_today"] == 1
        # Nothing was charged for the failed delivery yet and the goods came
        # back sellable, so the return has genuinely cost nothing so far. The
        # engine refuses to invent a loss out of returned stock — that would
        # make every return look twice as expensive as it was.
        assert body["return_loss_paisa"] == 0

    async def test_unforecast_cod_is_reported_separately_from_expected(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        body = await _home(client, shop)

        # No settlement history for this provider yet, so no honest arrival
        # date exists. Section 140: nothing about courier payout timing is
        # invented, so the money is shown as unforecast rather than "due".
        assert body["cod_expected_today_paisa"] == 0
        assert body["cod_unforecast_paisa"] == 140_500

    async def test_the_quality_of_the_figure_travels_with_it(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        body = await _home(client, shop)

        # Delivered but not settled and no courier charge known yet, so the
        # profit is an estimate and says so (section 135).
        assert body["estimated_parcels"] == 1

    async def test_it_needs_a_shop(self, client: AsyncClient) -> None:
        assert (await client.get("/v1/analytics/home")).status_code == 401


class TestProfit:
    async def test_the_period_defaults_to_the_last_thirty_days(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/v1/analytics/profit", headers=auth_header(shop))
        assert response.status_code == 200
        body = response.json()
        assert body["since"] == (business_date(at=utc_now()) - timedelta(days=29)).isoformat()
        assert body["until"] == business_date(at=utc_now()).isoformat()

    async def test_margin_is_null_rather_than_zero_when_nothing_sold(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/v1/analytics/profit", headers=auth_header(shop))
        # A zero margin would read as "sold at cost"; nothing was sold at all.
        assert response.json()["margin_basis_points"] is None

    async def test_the_parts_add_up_to_the_profit(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500, cost_paisa=65_000)
        body = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()

        assert body["parcel_count"] == 1
        assert (
            body["contribution_profit_paisa"]
            == body["realized_revenue_paisa"]
            - body["item_cost_paisa"]
            - body["delivery_charge_paisa"]
            - body["cod_fee_paisa"]
            - body["return_charge_paisa"]
            - body["packaging_paisa"]
            - body["ad_cost_paisa"]
            - body["write_off_cost_paisa"]
        )
        assert body["margin_basis_points"] == 5374

    async def test_unallocated_ad_spend_is_visible_and_below_the_line(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        today = business_date(at=utc_now()).isoformat()
        created = await client.post(
            "/v1/expenses",
            json={
                "kind": "AD_SPEND",
                "amount_paisa": 500_000,
                "period_start": today,
                "period_end": today,
                "description": "Facebook boost",
            },
            headers=auth_header(shop),
        )
        assert created.status_code == 201, created.text

        body = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()
        # Recorded but not allocated: it has reached no parcel, so it changes
        # no contribution profit — and it is still shown, because the seller
        # spent it (section 86).
        assert body["ad_cost_paisa"] == 0
        assert body["unallocated_ad_spend_paisa"] == 500_000
        assert body["operating_profit_paisa"] == body["contribution_profit_paisa"] - 500_000


class TestReturns:
    async def test_a_shop_with_no_returns_reports_nothing_rather_than_failing(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop)
        body = (await client.get("/v1/analytics/returns", headers=auth_header(shop))).json()

        assert body["return_count"] == 0
        assert body["return_rate_basis_points"] == 0
        assert body["by_reason"] == {}

    async def test_the_return_reason_reaches_the_report(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(
            client,
            shop,
            cod_paisa=60_000,
            outcome="RETURNED",
            return_reason="CUSTOMER_REFUSED",
        )
        body = (await client.get("/v1/analytics/returns", headers=auth_header(shop))).json()

        assert body["return_count"] == 1
        assert body["by_reason"] == {"CUSTOMER_REFUSED": 1}
        assert body["unknown_reason_count"] == 0

    async def test_a_return_with_no_reason_is_counted_not_hidden(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=60_000, outcome="RETURNED")
        body = (await client.get("/v1/analytics/returns", headers=auth_header(shop))).json()

        # Section 19 asks for the reason. How much of the picture is missing
        # is itself information, so it is reported rather than dropped.
        assert body["unknown_reason_count"] == 1
        assert body["by_reason"] == {}

    async def test_the_breakdowns_carry_the_sample_flag(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=60_000, outcome="RETURNED")
        body = (await client.get("/v1/analytics/returns", headers=auth_header(shop))).json()

        [product] = body["by_product"]
        assert product["return_count"] == 1
        assert product["return_rate_basis_points"] == 10_000
        # One parcel is not evidence about a product (section 24).
        assert product["has_enough_sample"] is False

    async def test_a_sellable_return_is_stock_not_a_loss(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=60_000, outcome="RETURNED", cost_paisa=20_000)
        body = (await client.get("/v1/analytics/returns", headers=auth_header(shop))).json()

        # The units went back on the shelf and no courier charge is known, so
        # there is nothing to book as a loss yet.
        assert body["direct_loss_paisa"] == 0
        assert body["return_rate_basis_points"] == 10_000

    async def test_the_courier_charge_on_a_return_is_the_loss(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _parcel(
            client, shop, cod_paisa=60_000, outcome="RETURNED", cost_paisa=20_000
        )
        charge = await client.post(
            f"/v1/consignments/{parcel['consignment']['id']}/charges",
            json={
                "kind": "RETURN",
                "amount_paisa": 6_000,
                "source": "SELLER",
                "provider_label": "Return fee off the receipt",
            },
            headers=auth_header(shop),
        )
        assert charge.status_code == 201, charge.text

        body = (await client.get("/v1/analytics/returns", headers=auth_header(shop))).json()
        # This is what a return actually costs a Bangladeshi seller: the
        # courier's fee on a parcel that collected nothing.
        assert body["return_delivery_cost_paisa"] == 6_000
        assert body["direct_loss_paisa"] == 6_000


class TestProducts:
    async def test_a_multi_item_order_is_not_counted_twice(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        first = await create_product(
            client, shop, name="Cotton Abaya", sku="abaya", cost_paisa=30_000, opening_stock=10
        )
        second = await create_product(
            client, shop, name="Silk Hijab", sku="hijab", cost_paisa=10_000, opening_stock=10
        )
        created = await create_order(
            client,
            shop,
            items=[
                {"product_id": first["id"], "quantity": 1, "unit_price_paisa": 100_000},
                {"product_id": second["id"], "quantity": 1, "unit_price_paisa": 100_000},
            ],
            cod_amount_paisa=200_000,
        )
        dispatch = await client.post(
            f"/v1/consignments/orders/{created['order']['id']}/dispatch",
            json={},
            headers=auth_header(shop),
        )
        await client.post(
            f"/v1/consignments/{dispatch.json()['id']}/outcome",
            json={"status": "DELIVERED"},
            headers=auth_header(shop),
        )

        rows = (await client.get("/v1/analytics/products", headers=auth_header(shop))).json()
        profit = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()

        # The parcel's profit is split between its two lines, not credited to
        # both. Crediting both would double the shop's profit the moment a
        # seller started bundling.
        assert len(rows) == 2
        assert sum(row["profit_paisa"] for row in rows) == profit["contribution_profit_paisa"]
        assert sum(row["revenue_paisa"] for row in rows) == profit["realized_revenue_paisa"]

    async def test_the_best_product_comes_first(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=200_000, cost_paisa=20_000, product_name="Abaya")
        await _parcel(client, shop, cod_paisa=100_000, cost_paisa=90_000, product_name="Hijab")

        rows = (await client.get("/v1/analytics/products", headers=auth_header(shop))).json()
        assert [row["product_name"] for row in rows] == ["Abaya", "Hijab"]
        assert rows[0]["margin_basis_points"] > rows[1]["margin_basis_points"]

    async def test_a_thin_seller_still_sees_their_products(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, product_name="Abaya")
        [row] = (await client.get("/v1/analytics/products", headers=auth_header(shop))).json()

        # Shown, flagged — not hidden. A seller who sold one of something
        # should not have to wonder where it went.
        assert row["product_name"] == "Abaya"
        assert row["has_enough_sample"] is False


class TestExpenses:
    async def test_recording_then_allocating_moves_the_profit(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        today = business_date(at=utc_now()).isoformat()

        expense = (
            await client.post(
                "/v1/expenses",
                json={
                    "kind": "AD_SPEND",
                    "amount_paisa": 10_000,
                    "period_start": today,
                    "period_end": today,
                    "description": "Facebook boost",
                },
                headers=auth_header(shop),
            )
        ).json()
        assert expense["allocated_paisa"] == 0
        assert expense["unallocated_paisa"] == 10_000

        before = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()[
            "contribution_profit_paisa"
        ]

        allocation = await client.post(
            f"/v1/expenses/{expense['id']}/allocate",
            json={},
            headers=auth_header(shop),
        )
        assert allocation.status_code == 200, allocation.text
        assert allocation.json()["allocated_paisa"] == 10_000
        assert allocation.json()["reached_nothing"] is False

        after = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()
        assert after["ad_cost_paisa"] == 10_000
        assert after["contribution_profit_paisa"] == before - 10_000

    async def test_spend_that_reached_no_parcel_says_so(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        today = business_date(at=utc_now()).isoformat()
        expense = (
            await client.post(
                "/v1/expenses",
                json={
                    "kind": "AD_SPEND",
                    "amount_paisa": 10_000,
                    "period_start": today,
                    "period_end": today,
                    "description": "Boost with no deliveries",
                },
                headers=auth_header(shop),
            )
        ).json()

        allocation = (
            await client.post(
                f"/v1/expenses/{expense['id']}/allocate",
                json={},
                headers=auth_header(shop),
            )
        ).json()

        # Ad money spent in a week with no deliveries is a real cost that no
        # order carries. Smearing it over unrelated parcels would flatter them.
        assert allocation["reached_nothing"] is True
        assert allocation["unallocated_paisa"] == 10_000

    async def test_re_allocating_without_a_reason_is_refused(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        today = business_date(at=utc_now()).isoformat()
        expense = (
            await client.post(
                "/v1/expenses",
                json={
                    "kind": "AD_SPEND",
                    "amount_paisa": 10_000,
                    "period_start": today,
                    "period_end": today,
                    "description": "Facebook boost",
                },
                headers=auth_header(shop),
            )
        ).json()
        await client.post(
            f"/v1/expenses/{expense['id']}/allocate", json={}, headers=auth_header(shop)
        )

        # Section 86: a profit figure the seller has already read does not
        # change silently.
        again = await client.post(
            f"/v1/expenses/{expense['id']}/allocate", json={}, headers=auth_header(shop)
        )
        assert again.status_code == 422

    async def test_rent_is_not_allocated_to_parcels(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        today = business_date(at=utc_now()).isoformat()
        expense = (
            await client.post(
                "/v1/expenses",
                json={
                    "kind": "FIXED",
                    "amount_paisa": 1_500_000,
                    "period_start": today,
                    "period_end": today,
                    "description": "Shop rent",
                },
                headers=auth_header(shop),
            )
        ).json()

        response = await client.post(
            f"/v1/expenses/{expense['id']}/allocate", json={}, headers=auth_header(shop)
        )
        # Section 86 warns against pretending fixed-cost allocation is
        # accounting-grade. Rent stays below contribution profit.
        assert response.status_code == 409

    async def test_the_list_is_paged(self, client: AsyncClient, shop: dict[str, Any]) -> None:
        today = business_date(at=utc_now()).isoformat()
        for index in range(3):
            await client.post(
                "/v1/expenses",
                json={
                    "kind": "OTHER",
                    "amount_paisa": 1_000 + index,
                    "period_start": today,
                    "period_end": today,
                    "description": f"Packaging batch {index}",
                },
                headers=auth_header(shop),
            )

        page = (await client.get("/v1/expenses?limit=2", headers=auth_header(shop))).json()
        assert len(page["items"]) == 2
        assert page["next_cursor"] is not None


class TestCharges:
    async def test_recording_a_charge_moves_the_profit(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _parcel(client, shop, cod_paisa=140_500, cost_paisa=65_000)
        before = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()[
            "contribution_profit_paisa"
        ]

        response = await client.post(
            f"/v1/consignments/{parcel['consignment']['id']}/charges",
            json={"kind": "DELIVERY", "amount_paisa": 8_000, "source": "SELLER"},
            headers=auth_header(shop),
        )
        assert response.status_code == 201, response.text
        assert response.json()["source"] == "SELLER"

        after = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()
        # Without this endpoint every parcel's profit is revenue minus goods,
        # which flatters every margin in the shop by whatever the courier
        # actually charged.
        assert after["delivery_charge_paisa"] == 8_000
        assert after["contribution_profit_paisa"] == before - 8_000

    async def test_a_weaker_source_cannot_quietly_overwrite_a_better_one(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _parcel(client, shop, cod_paisa=140_500)
        url = f"/v1/consignments/{parcel['consignment']['id']}/charges"

        settled = await client.post(
            url,
            json={"kind": "DELIVERY", "amount_paisa": 8_000, "source": "SETTLED"},
            headers=auth_header(shop),
        )
        assert settled.status_code == 201

        # Section 85's hierarchy: a guess arriving after the statement does
        # not get to replace it silently.
        guess = await client.post(
            url,
            json={"kind": "DELIVERY", "amount_paisa": 6_000, "source": "ESTIMATE"},
            headers=auth_header(shop),
        )
        assert guess.status_code == 409

        # With a reason it is an audited correction, which is allowed.
        corrected = await client.post(
            url,
            json={
                "kind": "DELIVERY",
                "amount_paisa": 6_000,
                "source": "ESTIMATE",
                "reason": "Statement line belonged to a different parcel",
            },
            headers=auth_header(shop),
        )
        assert corrected.status_code == 201

    async def test_listing_shows_only_the_charges_still_in_force(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _parcel(client, shop, cod_paisa=140_500)
        url = f"/v1/consignments/{parcel['consignment']['id']}/charges"

        await client.post(
            url,
            json={"kind": "DELIVERY", "amount_paisa": 6_000, "source": "BOOKED"},
            headers=auth_header(shop),
        )
        await client.post(
            url,
            json={"kind": "DELIVERY", "amount_paisa": 8_000, "source": "SETTLED"},
            headers=auth_header(shop),
        )
        await client.post(
            url,
            json={"kind": "COD_FEE", "amount_paisa": 1_405, "source": "SETTLED"},
            headers=auth_header(shop),
        )

        charges = (await client.get(url, headers=auth_header(shop))).json()
        assert {(c["kind"], c["amount_paisa"]) for c in charges} == {
            ("DELIVERY", 8_000),
            ("COD_FEE", 1_405),
        }

    async def test_a_negative_charge_is_refused(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        parcel = await _parcel(client, shop)
        response = await client.post(
            f"/v1/consignments/{parcel['consignment']['id']}/charges",
            json={"kind": "DELIVERY", "amount_paisa": -1, "source": "SELLER"},
            headers=auth_header(shop),
        )
        assert response.status_code == 422


class TestNotificationCentre:
    async def test_the_badge_starts_empty(self, client: AsyncClient, shop: dict[str, Any]) -> None:
        response = await client.get("/v1/notifications/unread-count", headers=auth_header(shop))
        assert response.status_code == 200
        assert response.json() == {"unread": 0}

    async def test_the_weekly_summary_can_be_read_before_friday(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        response = await client.get("/v1/analytics/weekly-summary", headers=auth_header(shop))
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["delivered_count"] == 1
        assert body["cod_outstanding_paisa"] == 140_500
        # Section 24: no ranking on one parcel, and the reason is visible.
        assert body["best_product"] is None
        assert body["ranking_note"] is not None

    async def test_reading_one_and_then_all(
        self, client: AsyncClient, shop: dict[str, Any], db: AsyncSession
    ) -> None:
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(shop["tenant_id"])))
        await NotificationService(db).notify(
            kind=NotificationKind.DELIVERED_BUT_UNPAID,
            severity=Severity.CRITICAL,
            title="Delivered but not paid: ৳1,405",
            body="1 parcel reached the customer and the money has not arrived.",
        )
        await db.commit()

        listing = (await client.get("/v1/notifications", headers=auth_header(shop))).json()
        assert len(listing["items"]) == 1
        notification = listing["items"][0]
        assert notification["severity"] == "CRITICAL"
        assert notification["read_at"] is None

        read = await client.post(
            f"/v1/notifications/{notification['id']}/read", headers=auth_header(shop)
        )
        assert read.status_code == 200
        assert read.json()["read_at"] is not None

        cleared = await client.post("/v1/notifications/read-all", headers=auth_header(shop))
        assert cleared.json() == {"unread": 0}

    async def test_an_unknown_notification_is_a_404(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.post(
            f"/v1/notifications/{uuid.uuid4()}/read", headers=auth_header(shop)
        )
        assert response.status_code == 404


class TestTrendAndFunnel:
    async def test_the_series_keeps_the_empty_days(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=140_500)
        body = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()

        # Thirty days requested, thirty points returned. Skipping quiet days
        # would compress the chart's x-axis and make the week look busier
        # than it was.
        assert len(body["series"]) == 30
        assert body["series"][0]["business_date"] == body["since"]
        assert body["series"][-1]["business_date"] == body["until"]
        assert body["series"][-1]["contribution_profit_paisa"] == 75_500
        assert body["series"][0]["parcel_count"] == 0

    async def test_the_funnel_counts_parcels_that_have_not_finished(
        self, client: AsyncClient, shop: dict[str, Any]
    ) -> None:
        await _parcel(client, shop, cod_paisa=100_000)
        await _parcel(client, shop, cod_paisa=60_000, outcome="RETURNED", product_name="Silk Hijab")

        # One more dispatched and left in transit.
        product = await create_product(client, shop, name="Kurti", sku="kurti", opening_stock=5)
        created = await create_order(
            client,
            shop,
            items=[{"product_id": product["id"], "quantity": 1, "unit_price_paisa": 50_000}],
            cod_amount_paisa=50_000,
        )
        dispatch = await client.post(
            f"/v1/consignments/orders/{created['order']['id']}/dispatch",
            json={},
            headers=auth_header(shop),
        )
        assert dispatch.status_code == 201

        body = (await client.get("/v1/analytics/profit", headers=auth_header(shop))).json()
        stages = {stage["label"]: stage["count"] for stage in body["funnel"]}

        # Counted from consignments, not snapshots: a funnel built only from
        # finished parcels would always claim a 100% success rate.
        assert stages["Dispatched"] == 3
        assert stages["In transit"] == 1
        assert stages["Delivered"] == 1
        assert stages["Returned"] == 1
        assert stages["Lost or damaged"] == 0
