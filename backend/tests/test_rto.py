"""Return / RTO intelligence.

The properties under test:

*   one classification: RETURNED and courier CANCELLED are RTO, open parcels
    are not, a booking that never happened never counts;
*   the denominator is completed parcels, and orders cancelled before dispatch
    are reported separately, never as RTO;
*   a parcel is counted once however many times its outcome is reported;
*   products, couriers and areas aggregate without double counting, and thin
    samples are labelled rather than ranked;
*   the V1 Risk Check bands on real parcel outcomes, and stays silent on a
    thin history;
*   one shop cannot see, or infer, another shop's customers or counts;
*   the phone number never reaches the logs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.analytics import rto
from app.analytics.rto import ObservationCode, OutcomeCounts, ParcelOutcome
from app.consignments.models import ConsignmentStatus
from app.core.clock import utc_now
from app.tenants.roles import TenantRole
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_team import _member_session

D, R = ParcelOutcome.DELIVERED, ParcelOutcome.RTO


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #


class TestClassification:
    def test_every_status_has_exactly_one_outcome(self) -> None:
        for status in ConsignmentStatus:
            assert isinstance(rto.classify(status), ParcelOutcome)

    def test_rto_is_returned_or_cancelled_at_the_courier(self) -> None:
        assert rto.classify("RETURNED") is ParcelOutcome.RTO
        # Steadfast's settled return-to-origin outcome.
        assert rto.classify("CANCELLED") is ParcelOutcome.RTO
        assert rto.statuses_for(ParcelOutcome.RTO) == ["CANCELLED", "RETURNED"]

    def test_a_return_on_its_way_is_not_yet_a_return(self) -> None:
        for status in ("RETURNING", "RETURN_REQUESTED", "BOOKED", "OUT_FOR_DELIVERY"):
            assert rto.classify(status) is ParcelOutcome.IN_TRANSIT

    def test_a_booking_that_never_happened_never_counts(self) -> None:
        for status in ("NOT_BOOKED", "BOOKING", "BOOKING_UNKNOWN", "FAILED"):
            assert rto.classify(status) is ParcelOutcome.NOT_DISPATCHED

    def test_the_denominator_is_completed_parcels_only(self) -> None:
        counts = OutcomeCounts()
        for status in (
            "DELIVERED",
            "DELIVERED",
            "DELIVERED",
            "PARTIAL_DELIVERED",
            "RETURNED",
            "CANCELLED",
            "LOST",
            "IN_TRANSIT",
            "FAILED",
        ):
            counts.add(status)
        assert counts.completed == 6
        assert (counts.returned, counts.courier_cancelled, counts.rto) == (1, 1, 2)
        assert counts.lost == 1
        assert counts.in_transit == 1
        assert counts.rto_rate_bps == 3333
        assert counts.success_rate_bps == 6667
        assert counts.sufficient is False

    def test_no_completed_parcels_is_no_rate_not_zero(self) -> None:
        assert OutcomeCounts(in_transit=4).rto_rate_bps is None


class TestPatterns:
    def test_one_return_is_not_a_pattern(self) -> None:
        assert rto.observe([(D, None), (R, None)]) == []

    def test_repeat_rto_needs_two(self) -> None:
        [obs] = rto.observe([(R, None), (D, None), (R, None)])
        assert obs.code is ObservationCode.REPEAT_RTO
        assert (obs.rto_count, obs.parcel_count) == (2, 3)

    def test_return_after_earlier_deliveries(self) -> None:
        [obs] = rto.observe([(D, None), (D, None), (R, None)])
        assert obs.code is ObservationCode.RTO_AFTER_DELIVERIES
        assert (obs.rto_count, obs.earlier_parcel_count, obs.earlier_rto_count) == (1, 2, 0)

    def test_worsening_and_improving_compare_counts(self) -> None:
        worse = {o.code for o in rto.observe([(D, None)] * 3 + [(R, None)] * 3)}
        assert ObservationCode.WORSENING in worse
        better = {o.code for o in rto.observe([(R, None)] * 3 + [(D, None)] * 3)}
        assert ObservationCode.IMPROVING in better
        # Too short to compare: no trend claimed.
        short = {o.code for o in rto.observe([(D, None), (R, None), (R, None)])}
        assert not short & {ObservationCode.WORSENING, ObservationCode.IMPROVING}

    def test_product_repeat_is_per_product(self) -> None:
        found = rto.observe([(R, "Abaya"), (R, "Abaya"), (R, "Hijab")])
        products = [o for o in found if o.code is ObservationCode.PRODUCT_REPEAT_RTO]
        assert [(o.product_name, o.rto_count) for o in products] == [("Abaya", 2)]

    def test_trend_needs_both_windows_sufficient(self) -> None:
        thin = OutcomeCounts(delivered=2, returned=2)
        full = OutcomeCounts(delivered=8, returned=2)
        assert rto.trend_of(thin, full) == "INSUFFICIENT_DATA"
        assert rto.trend_of(OutcomeCounts(delivered=5, returned=5), full) == "UP"
        assert rto.trend_of(full, OutcomeCounts(delivered=5, returned=5)) == "DOWN"
        assert rto.trend_of(full, full) == "FLAT"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class _Shop:
    """A signed-in shop that can put parcels through to an outcome."""

    def __init__(self, client: AsyncClient, session: dict[str, Any]) -> None:
        self.client = client
        self.session = session
        self.products: dict[str, dict[str, Any]] = {}

    @property
    def headers(self) -> dict[str, str]:
        return auth_header(self.session)

    async def product(self, name: str) -> dict[str, Any]:
        if name not in self.products:
            self.products[name] = await create_product(
                self.client, self.session, name=name, opening_stock=500
            )
        return self.products[name]

    async def order(
        self,
        *,
        phone: str = "01712345678",
        products: tuple[str, ...] = ("Cotton Abaya",),
        district: str | None = None,
    ) -> dict[str, Any]:
        items = [
            {
                "product_id": (await self.product(name))["id"],
                "quantity": 1,
                "unit_price_paisa": 100_000,
            }
            for name in products
        ]
        extra: dict[str, Any] = {"cod_amount_paisa": 100_000 * len(items)}
        if district is not None:
            extra["district"] = district
        return (await create_order(self.client, self.session, phone=phone, items=items, **extra))[
            "order"
        ]

    async def parcel(
        self,
        outcome: str | None,
        *,
        provider: str = "manual",
        days_ago: int = 0,
        **order: Any,
    ) -> dict[str, Any]:
        created = await self.order(**order)
        dispatch = await self.client.post(
            f"/v1/consignments/orders/{created['id']}/dispatch",
            json={"provider": provider},
            headers=self.headers,
        )
        assert dispatch.status_code == 201, dispatch.text
        consignment = dispatch.json()
        if outcome is not None:
            body: dict[str, Any] = {"status": outcome}
            if days_ago:
                body["occurred_at"] = (utc_now() - timedelta(days=days_ago)).isoformat()
            response = await self.client.post(
                f"/v1/consignments/{consignment['id']}/outcome", json=body, headers=self.headers
            )
            assert response.status_code == 200, response.text
        return {"order": created, "consignment": consignment}

    async def cancel_before_dispatch(self, **order: Any) -> None:
        created = await self.order(**order)
        response = await self.client.patch(
            f"/v1/orders/{created['id']}",
            json={"status": "CANCELLED", "cancellation_reason": "customer changed mind"},
            headers=self.headers,
        )
        assert response.status_code == 200, response.text

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(path, params=params, headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> _Shop:
    return _Shop(client, await signed_in_shop(client, unique_phone, shop_name="RTO Shop"))


def _other_phone() -> str:
    return f"017{uuid.uuid4().int % 100_000_000:08d}"


class TestSummary:
    async def test_denominator_and_cancelled_are_separate(self, shop: _Shop) -> None:
        await shop.parcel("DELIVERED")
        await shop.parcel("DELIVERED")
        await shop.parcel("RETURNED")
        await shop.parcel("CANCELLED", provider="steadfast")
        await shop.parcel("LOST")
        await shop.parcel(None)  # still with the courier
        await shop.cancel_before_dispatch()

        body = await shop.get("/v1/analytics/rto/summary", days=30)

        counts = body["counts"]
        assert counts["completed"] == 4
        assert counts["delivered"] == 2
        assert (counts["returned"], counts["courier_cancelled"], counts["rto"]) == (1, 1, 2)
        assert counts["rto_rate_basis_points"] == 5000
        assert counts["lost"] == 1
        assert counts["sufficient"] is False
        assert body["open_now"] == 1
        # Cancelled before dispatch: reported, never RTO, never in the denominator.
        assert body["cancelled_before_dispatch"] == 1
        assert body["definition"]["rto_statuses"] == ["CANCELLED", "RETURNED"]

    async def test_a_repeated_outcome_counts_once(self, shop: _Shop) -> None:
        parcel = await shop.parcel("RETURNED")
        again = await shop.client.post(
            f"/v1/consignments/{parcel['consignment']['id']}/outcome",
            json={"status": "RETURNED"},
            headers=shop.headers,
        )
        assert again.status_code == 409

        body = await shop.get("/v1/analytics/rto/summary")
        assert body["counts"]["rto"] == 1
        assert body["counts"]["completed"] == 1

    async def test_windows_use_the_outcome_date(self, shop: _Shop) -> None:
        await shop.parcel("RETURNED", days_ago=2)
        await shop.parcel("DELIVERED", days_ago=20)
        await shop.parcel("RETURNED", days_ago=60)

        body = await shop.get("/v1/analytics/rto/summary", days=30)
        windows = {w["days"]: w["counts"] for w in body["windows"]}
        assert (windows[7]["completed"], windows[7]["rto"]) == (1, 1)
        assert (windows[30]["completed"], windows[30]["rto"]) == (2, 1)
        assert (windows[90]["completed"], windows[90]["rto"]) == (3, 2)

        trend = await shop.get("/v1/analytics/rto/trend", weeks=12)
        assert len(trend["points"]) == 12
        assert sum(p["counts"]["completed"] for p in trend["points"]) == 3
        assert trend["points"][-1]["counts"]["rto"] == 1


class TestBreakdowns:
    async def test_products_count_each_parcel_once_per_product(self, shop: _Shop) -> None:
        await shop.parcel("RETURNED", products=("Abaya", "Hijab"))
        await shop.parcel("DELIVERED", products=("Abaya",))

        body = await shop.get("/v1/analytics/rto/products", sort="rto_count")
        rows = {row["product_name"]: row for row in body["items"]}
        assert rows["Abaya"]["counts"]["completed"] == 2
        assert rows["Abaya"]["counts"]["rto"] == 1
        assert rows["Hijab"]["counts"]["completed"] == 1
        assert rows["Hijab"]["counts"]["rto_rate_basis_points"] == 10_000
        # Line value inside returned parcels, per product, not the whole COD.
        assert rows["Abaya"]["rto_value_paisa"] == 100_000
        # Two parcels is a limited sample, and is labelled as one.
        assert rows["Hijab"]["counts"]["sufficient"] is False

    async def test_products_paginate_and_filter_on_the_server(self, shop: _Shop) -> None:
        for name in ("Abaya", "Hijab", "Scarf"):
            await shop.parcel("DELIVERED", products=(name,))

        first = await shop.get("/v1/analytics/rto/products", limit=2, offset=0)
        second = await shop.get("/v1/analytics/rto/products", limit=2, offset=2)
        assert first["total"] == 3 and first["has_more"] is True
        assert second["has_more"] is False
        names = [r["product_name"] for r in first["items"] + second["items"]]
        assert sorted(names) == ["Abaya", "Hijab", "Scarf"]

        found = await shop.get("/v1/analytics/rto/products", search="hij")
        assert [r["product_name"] for r in found["items"]] == ["Hijab"]

    async def test_couriers_split_by_provider_and_skip_not_live(self, shop: _Shop) -> None:
        await shop.parcel("CANCELLED", provider="steadfast")
        await shop.parcel("DELIVERED", provider="steadfast")
        await shop.parcel("DELIVERED", provider="pathao")
        await shop.parcel("DELIVERED", provider="redx")

        body = await shop.get("/v1/analytics/rto/couriers")
        rows = {row["provider"]: row for row in body["items"]}
        assert set(rows) == {"steadfast", "pathao"}
        assert rows["steadfast"]["counts"]["rto_rate_basis_points"] == 5000
        assert rows["steadfast"]["trend"] == "INSUFFICIENT_DATA"
        assert body["excluded_providers"] == ["redx"]

    async def test_areas_are_not_reported_on_thin_or_patchy_data(self, shop: _Shop) -> None:
        await shop.parcel("RETURNED", district="Dhaka")
        await shop.parcel("DELIVERED")

        body = await shop.get("/v1/analytics/rto/areas")
        assert body["status"] == "DATA_NOT_RELIABLE"
        assert body["items"] == []
        assert body["coverage_basis_points"] == 5000


class TestCustomerHistoryAndRisk:
    async def test_risk_check_bands_on_real_parcels(self, shop: _Shop) -> None:
        phone = "01712000111"
        for _ in range(4):
            await shop.parcel("DELIVERED", phone=phone)
        await shop.parcel("RETURNED", phone=phone)
        await shop.cancel_before_dispatch(phone=phone)

        body = await shop.get("/v1/customers/risk-check", phone=phone)
        assert body["found"] is True
        # V1 rule, unchanged: 4 delivered / (4 + 1 RTO + 1 cancelled).
        assert (body["delivered_count"], body["returned_count"], body["cancelled_count"]) == (
            4,
            1,
            1,
        )
        assert body["terminal_count"] == 6
        assert body["success_rate_basis_points"] == 6667
        assert body["state"] == "MEDIUM"
        # The RTO view of the same history excludes the pre-dispatch cancel.
        assert body["parcels"]["completed"] == 5
        assert body["parcels"]["rto_rate_basis_points"] == 2000
        assert body["recent"][0]["outcome"] in {"DELIVERED", "RTO"}
        codes = {obs["code"] for obs in body["observations"]}
        assert "RTO_AFTER_DELIVERIES" in codes

    async def test_one_return_is_insufficient_not_high(self, shop: _Shop) -> None:
        await shop.parcel("RETURNED", phone="01712000222")
        body = await shop.get("/v1/customers/risk-check", phone="01712000222")
        assert body["state"] == "INSUFFICIENT_DATA"
        assert body["returned_count"] == 1
        assert body["observations"] == []

    async def test_customer_history_and_list_share_the_numbers(self, shop: _Shop) -> None:
        phone = "01712000333"
        await shop.parcel("RETURNED", phone=phone, products=("Abaya",))
        await shop.parcel("CANCELLED", provider="steadfast", phone=phone, products=("Abaya",))
        created = await shop.parcel("DELIVERED", phone=phone)
        customer_id = created["order"]["customer_id"]

        history = await shop.get(f"/v1/analytics/rto/customers/{customer_id}")
        assert history["counts"]["rto"] == 2
        assert history["counts"]["completed"] == 3
        assert history["risk_state"] in {"LOW", "MEDIUM", "HIGH"}
        codes = {(o["code"], o["product_name"]) for o in history["observations"]}
        assert ("REPEAT_RTO", None) in codes
        assert ("PRODUCT_REPEAT_RTO", "Abaya") in codes

        listed = await shop.get("/v1/customers", search=phone)
        [row] = listed["items"]
        assert (row["delivered_count"], row["returned_count"]) == (1, 2)

        patterns = await shop.get("/v1/analytics/rto/patterns")
        [pattern] = patterns["items"]
        assert pattern["customer_id"] == customer_id
        assert pattern["phone_masked"] == "01712****33"
        assert phone not in str(patterns)


class TestIsolationAndPrivacy:
    async def test_another_shop_sees_nothing(self, client: AsyncClient, shop: _Shop) -> None:
        phone = "01712000444"
        for _ in range(3):
            created = await shop.parcel("RETURNED", phone=phone)
        customer_id = created["order"]["customer_id"]

        other = _Shop(client, await signed_in_shop(client, _other_phone(), shop_name="Other"))

        risk = await other.get("/v1/customers/risk-check", phone=phone)
        assert risk["found"] is False
        assert risk["returned_count"] == 0
        assert risk["parcels"] is None

        # Not the history, not even whether the id exists.
        response = await client.get(
            f"/v1/analytics/rto/customers/{customer_id}", headers=other.headers
        )
        assert response.status_code == 404

        # And no aggregate that would reveal shop A's counts.
        summary = await other.get("/v1/analytics/rto/summary", days=90)
        assert summary["counts"]["completed"] == 0
        assert (await other.get("/v1/analytics/rto/patterns"))["items"] == []
        assert (await other.get("/v1/analytics/rto/products"))["total"] == 0
        assert (await other.get("/v1/analytics/rto/couriers"))["items"] == []

    async def test_customer_views_need_risk_permission(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # A plan with room for a team.
        shop = _Shop(client, await signed_in_shop(client, unique_phone, plan="pro"))
        created = await shop.parcel("RETURNED")
        customer_id = created["order"]["customer_id"]

        viewer = await _member_session(client, shop.session, _other_phone(), TenantRole.VIEWER)
        finance = await _member_session(client, shop.session, _other_phone(), TenantRole.FINANCE)

        # Shop-level facts carry no customer identity.
        ok = await client.get("/v1/analytics/rto/summary", headers=auth_header(viewer))
        assert ok.status_code == 200
        for member in (viewer, finance):
            for path in (
                "/v1/analytics/rto/patterns",
                f"/v1/analytics/rto/customers/{customer_id}",
            ):
                response = await client.get(path, headers=auth_header(member))
                assert response.status_code == 403, (path, response.text)

    async def test_the_phone_never_reaches_the_logs(
        self, shop: _Shop, caplog: pytest.LogCaptureFixture
    ) -> None:
        phone = "01712000555"
        await shop.parcel("RETURNED", phone=phone)

        with caplog.at_level(logging.DEBUG):
            await shop.get("/v1/customers/risk-check", phone=phone)
            await shop.get("/v1/analytics/rto/patterns")

        logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
        for form in (phone, phone[1:], "+88" + phone):
            assert form not in logged


class TestReturnReportAgrees:
    async def test_courier_cancelled_counts_as_a_return_in_insights(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        shop = _Shop(client, await signed_in_shop(client, unique_phone, plan="pro"))
        await shop.parcel("CANCELLED", provider="steadfast")
        await shop.parcel("DELIVERED", provider="steadfast")
        await shop.parcel("LOST", provider="steadfast")

        report = await shop.get("/v1/analytics/returns")
        rto_summary = await shop.get("/v1/analytics/rto/summary")
        assert report["return_count"] == rto_summary["counts"]["rto"] == 1
        assert report["parcel_count"] == rto_summary["counts"]["completed"] == 2
        assert report["return_rate_basis_points"] == 5000
        [courier] = report["by_courier"]
        assert courier["return_count"] == 1
