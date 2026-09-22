"""Receivables and cashflow.

The facts — outstanding, delivered-but-unpaid, overdue, received — must agree
with the receivables and the ledger they are read from. The one estimate, the
forecast, must never create money: its windows always add up to what is
receivable today, and a courier with too little history is reported as
"no history" rather than guessed at.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_date, utc_now
from app.core.context import RequestContext, set_context
from app.ledger.models import LedgerBucket
from app.money.cashflow import MIN_DELAY_SAMPLES, CashflowService
from app.payouts.service import PayoutService
from app.reconciliation.service import ReconciliationService
from app.tenants.roles import TenantRole
from tests.conftest_commerce import dispatched_parcel, signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_reconciliation import delivered_parcel, statement
from tests.test_team import _member_session


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Cashflow Shop", plan="pro")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


async def _as_courier(db: AsyncSession, parcel: dict, provider: str) -> None:
    """Move a parcel to another courier, the way a booked parcel would be."""
    parcel["consignment"].provider = provider
    receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
    receivable.provider = provider
    await db.commit()


async def _pay(db: AsyncSession, parcel: dict, *, paid_days_ago: int, name: str) -> None:
    payout = await PayoutService(db).import_statement(
        statement(f"{parcel['reference']},{parcel['cod_paisa'] / 100:.2f}"),
        provider=parcel["consignment"].provider,
        filename=name,
        paid_on=business_date(at=utc_now() - timedelta(days=paid_days_ago)),
    )
    await db.commit()
    await ReconciliationService(db, receivables=parcel["receivables"]).reconcile(payout.id)
    await db.commit()


async def _delivered(client, shop, db, *, cod: int, days_ago: int = 0) -> dict:
    parcel = await delivered_parcel(client, shop, db, cod_paisa=cod, delivered_days_ago=days_ago)
    parcel["cod_paisa"] = cod
    return parcel


class TestCourierBalances:
    async def test_balances_split_by_courier_with_overdue_and_in_transit(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await _delivered(client, shop, db, cod=100_000, days_ago=1)
        late = await _delivered(client, shop, db, cod=50_000, days_ago=20)
        ancient = await _delivered(client, shop, db, cod=30_000, days_ago=45)
        await _as_courier(db, ancient, "steadfast")
        await dispatched_parcel(client, shop, db, cod_paisa=70_000)  # still on the road

        rows = {row.provider: row for row in await CashflowService(db).courier_balances()}

        manual = rows["manual"]
        assert manual.outstanding_paisa == 150_000
        assert manual.delivered_unpaid_count == 2
        assert manual.overdue_count == 1
        assert manual.overdue_paisa == 50_000
        assert manual.oldest_age_days == 20
        assert manual.in_transit_count == 1
        assert manual.in_transit_paisa == 70_000
        bands = {bucket.label: (count, amount) for bucket, count, amount in manual.aging}
        assert bands["0-3 days"] == (1, 100_000)
        assert bands["15-30 days"] == (1, 50_000)

        steadfast = rows["steadfast"]
        assert steadfast.outstanding_paisa == 30_000
        assert {bucket.label: amount for bucket, _, amount in steadfast.aging}["30+ days"] == 30_000

        # The per-courier facts agree with the shop-wide receivable total.
        total = await late["receivables"].outstanding_total()
        assert sum(row.outstanding_paisa for row in rows.values()) == total

    async def test_the_age_filter_lists_what_the_band_counted(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await _delivered(client, shop, db, cod=100_000, days_ago=1)
        late = await _delivered(client, shop, db, cod=50_000, days_ago=10)

        rows = await late["receivables"].list_receivables(min_age_days=8, max_age_days=14)
        assert [row.id for row in rows] == [late["receivable"].id]


class TestCashflow:
    async def test_received_is_ledger_truth_and_splits_by_courier(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await _delivered(client, shop, db, cod=100_000, days_ago=3)
        second = await _delivered(client, shop, db, cod=60_000, days_ago=3)
        await _as_courier(db, second, "steadfast")
        await _pay(db, first, paid_days_ago=0, name="a.csv")
        await _pay(db, second, paid_days_ago=0, name="b.csv")

        flow = await CashflowService(db).cashflow()
        balances = await first["ledger"].balances(since=flow.since, until=flow.until)

        assert flow.received_paisa == balances[LedgerBucket.COD_SETTLED].net_paisa == 160_000
        assert flow.received_by_courier == {"manual": 100_000, "steadfast": 60_000}
        assert flow.receivable_paisa == 0

    async def test_the_forecast_never_creates_money(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        # Enough paid history to learn a 3-day delay…
        for index in range(MIN_DELAY_SAMPLES):
            paid = await _delivered(client, shop, db, cod=10_000 + index, days_ago=6)
            await _pay(db, paid, paid_days_ago=3, name=f"h{index}.csv")
        # …then one parcel expected within the week, and one already late.
        await _delivered(client, shop, db, cod=40_000, days_ago=1)
        await _delivered(client, shop, db, cod=25_000, days_ago=9)

        flow = await CashflowService(db).cashflow()

        windows = {window.key: window for window in flow.forecast}
        assert sum(window.amount_paisa for window in flow.forecast) == flow.receivable_paisa
        assert flow.receivable_paisa == 65_000
        assert windows["next_7_days"].amount_paisa == 40_000
        # Its usual payment date has passed: reported as late, not moved
        # into next week.
        assert windows["past_expected"].amount_paisa == 25_000
        assert windows["no_history"].amount_paisa == 0
        [delay] = flow.delays
        assert delay.samples == MIN_DELAY_SAMPLES
        assert delay.median_days == 3
        assert flow.overdue_paisa == 25_000

    async def test_without_history_the_date_is_unknown(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        paid = await _delivered(client, shop, db, cod=10_000, days_ago=5)
        await _pay(db, paid, paid_days_ago=1, name="one.csv")
        await _delivered(client, shop, db, cod=40_000, days_ago=1)

        flow = await CashflowService(db).cashflow()

        windows = {window.key: window.amount_paisa for window in flow.forecast}
        assert windows["no_history"] == 40_000
        assert windows["next_7_days"] == 0
        # One paid parcel is measured, but not trusted for a forecast.
        assert flow.delays[0].samples == 1
        assert not flow.delays[0].is_reliable


class TestApi:
    async def test_couriers_and_cashflow_endpoints(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await _delivered(client, shop, db, cod=100_000, days_ago=10)

        couriers = await client.get("/v1/money/couriers", headers=auth_header(shop))
        assert couriers.status_code == 200, couriers.text
        [row] = couriers.json()
        assert row["provider"] == "manual"
        assert row["overdue_paisa"] == 100_000
        assert [band["label"] for band in row["aging"]][-1] == "30+ days"

        flow = await client.get("/v1/money/cashflow", headers=auth_header(shop))
        assert flow.status_code == 200, flow.text
        body = flow.json()
        assert body["forecast"]["quality"] == "ESTIMATE"
        assert body["receivable_paisa"] == 100_000
        assert sum(window["amount_paisa"] for window in body["forecast"]["windows"]) == 100_000
        assert body["overdue_after_days"] == 7

        aged = await client.get(
            "/v1/money/receivables",
            params={"min_age_days": 8, "max_age_days": 14},
            headers=auth_header(shop),
        )
        assert len(aged.json()["items"]) == 1

    async def test_another_shop_and_a_viewer_see_nothing(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await _delivered(client, shop, db, cod=100_000, days_ago=2)

        other = await signed_in_shop(
            client, f"018{uuid.uuid4().int % 100_000_000:08d}", shop_name="Other", plan="pro"
        )
        assert (await client.get("/v1/money/couriers", headers=auth_header(other))).json() == []
        flow = (await client.get("/v1/money/cashflow", headers=auth_header(other))).json()
        assert flow["receivable_paisa"] == 0
        assert flow["in_transit_paisa"] == 0

        viewer = await _member_session(client, shop, "01755000201", TenantRole.VIEWER)
        assert (
            await client.get("/v1/money/cashflow", headers=auth_header(viewer))
        ).status_code == 403
        finance = await _member_session(client, shop, "01755000202", TenantRole.FINANCE)
        assert (
            await client.get("/v1/money/couriers", headers=auth_header(finance))
        ).status_code == 200
