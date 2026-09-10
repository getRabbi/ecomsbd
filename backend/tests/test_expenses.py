"""Expenses and ad allocation.

Master spec section 86. The properties that matter:

* an expense changes nothing until it is allocated;
* the split loses no paisa, so the Insights total and the expense list agree;
* re-allocating writes new snapshot revisions with a reason rather than
  silently rewriting figures the seller has already read.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import dispatched_parcel, signed_in_shop

from app.consignments.models import ConsignmentStatus
from app.consignments.service import DeliveryOutcome
from app.core.clock import business_date, utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.expenses.models import (
    ALLOCATION_VERSION,
    AllocationMethod,
    Expense,
    ExpenseKind,
)
from app.expenses.service import ExpenseService
from app.profit.service import ProfitService


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Expense Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


async def delivered(
    client: AsyncClient, shop: dict, db: AsyncSession, *, cod: int, days_ago: int = 0
) -> dict:
    parcel = await dispatched_parcel(client, shop, db, cod_paisa=cod)
    await parcel["consignments"].record_outcome(
        parcel["consignment"].id,
        DeliveryOutcome(
            status=ConsignmentStatus.DELIVERED,
            occurred_at=utc_now() - timedelta(days=days_ago),
        ),
    )
    await db.commit()
    # No explicit snapshot: recording the outcome writes revision 1 itself.
    return parcel


class TestRecording:
    async def test_recording_changes_no_profit_figure(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered(client, shop, db, cod=140_500)
        profit = ProfitService(db)
        before = (await profit.current_snapshot(parcel["consignment"].id)).ad_cost_paisa

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=500_000,
            period_start=business_date(at=utc_now()),
            period_end=business_date(at=utc_now()),
            description="Facebook boost",
        )
        await db.commit()

        # Recording is not allocating. Nothing has reached an order yet.
        assert not expense.is_allocated
        assert expense.unallocated_paisa == 500_000
        after = (await profit.current_snapshot(parcel["consignment"].id)).ad_cost_paisa
        assert after == before == 0

    async def test_a_zero_expense_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        today = business_date(at=utc_now())
        with pytest.raises(ValidationError):
            await ExpenseService(db).record(
                kind=ExpenseKind.AD_SPEND,
                amount_paisa=0,
                period_start=today,
                period_end=today,
                description="Nothing",
            )

    async def test_a_backwards_period_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        today = business_date(at=utc_now())
        with pytest.raises(ValidationError):
            await ExpenseService(db).record(
                kind=ExpenseKind.AD_SPEND,
                amount_paisa=1000,
                period_start=today,
                period_end=today - timedelta(days=1),
                description="Time travel",
            )

    async def test_per_product_allocation_needs_a_product(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        today = business_date(at=utc_now())
        with pytest.raises(ValidationError):
            await ExpenseService(db).record(
                kind=ExpenseKind.AD_SPEND,
                amount_paisa=1000,
                period_start=today,
                period_end=today,
                description="Tagged spend with no tag",
                preferred_method=AllocationMethod.PRODUCT_TAGGED_PER_UNIT,
            )


class TestAllocation:
    async def test_equal_split_reaches_every_delivered_parcel(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await delivered(client, shop, db, cod=140_500)
        second = await delivered(client, shop, db, cod=250_000)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=10_000,
            period_start=today,
            period_end=today,
            description="Facebook boost",
        )
        report = await expenses.allocate(expense.id)
        await db.commit()

        assert report.parcel_count == 2
        assert report.allocated_paisa == 10_000
        assert report.unallocated_paisa == 0
        assert await expenses.snapshot_ad_cost(first["consignment"].id) == 5_000
        assert await expenses.snapshot_ad_cost(second["consignment"].id) == 5_000

    async def test_the_split_loses_no_paisa(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcels = [await delivered(client, shop, db, cod=100_000 + index) for index in range(3)]
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        # 1,000 paisa across three parcels does not divide evenly.
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=1_000,
            period_start=today,
            period_end=today,
            description="Awkward amount",
        )
        report = await expenses.allocate(expense.id)
        await db.commit()

        shares = [await expenses.snapshot_ad_cost(parcel["consignment"].id) for parcel in parcels]
        # Largest-remainder: the parts sum back to the exact expense, so the
        # Insights total and the expense list cannot drift apart.
        assert sum(shares) == 1_000
        assert report.allocated_paisa == 1_000
        assert sorted(shares) == [333, 333, 334]

    async def test_proportional_allocation_follows_revenue(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        small = await delivered(client, shop, db, cod=100_000)
        large = await delivered(client, shop, db, cod=300_000)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=8_000,
            period_start=today,
            period_end=today,
            description="Facebook boost",
            preferred_method=AllocationMethod.PROPORTIONAL_TO_REVENUE,
        )
        await expenses.allocate(expense.id)
        await db.commit()

        # 1:3 by collected amount.
        assert await expenses.snapshot_ad_cost(small["consignment"].id) == 2_000
        assert await expenses.snapshot_ad_cost(large["consignment"].id) == 6_000

    async def test_spend_in_a_period_with_no_deliveries_stays_unallocated(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await delivered(client, shop, db, cod=140_500)
        long_ago = business_date(at=utc_now() - timedelta(days=90))

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=500_000,
            period_start=long_ago,
            period_end=long_ago,
            description="Spend before this shop delivered anything",
        )
        report = await expenses.allocate(expense.id)
        await db.commit()

        # Smearing it onto unrelated parcels would flatter their margins. It is
        # a real cost that no order carries, and it says so.
        assert report.reached_nothing
        assert report.unallocated_paisa == 500_000
        await db.refresh(expense)
        assert expense.unallocated_paisa == 500_000

    async def test_only_parcels_inside_the_period_are_touched(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        recent = await delivered(client, shop, db, cod=140_500, days_ago=0)
        old = await delivered(client, shop, db, cod=140_500, days_ago=30)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=10_000,
            period_start=today,
            period_end=today,
            description="Today's ads",
        )
        await expenses.allocate(expense.id)
        await db.commit()

        assert await expenses.snapshot_ad_cost(recent["consignment"].id) == 10_000
        assert await expenses.snapshot_ad_cost(old["consignment"].id) == 0

    async def test_a_returned_parcel_carries_no_ad_cost(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        delivered_parcel = await delivered(client, shop, db, cod=140_500)
        returned = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await returned["consignments"].record_outcome(
            returned["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.RETURNED),
        )
        await db.commit()
        await ProfitService(db).snapshot(returned["consignment"].id)
        await db.commit()

        today = business_date(at=utc_now())
        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=10_000,
            period_start=today,
            period_end=today,
            description="Facebook boost",
        )
        report = await expenses.allocate(expense.id)
        await db.commit()

        # A return earned nothing. Loading it with ad cost would turn one loss
        # into a larger one for no reason the seller could act on.
        assert report.parcel_count == 1
        assert await expenses.snapshot_ad_cost(delivered_parcel["consignment"].id) == 10_000
        assert await expenses.snapshot_ad_cost(returned["consignment"].id) == 0

    async def test_a_fixed_cost_is_not_spread_across_parcels(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await delivered(client, shop, db, cod=140_500)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        rent = await expenses.record(
            kind=ExpenseKind.FIXED,
            amount_paisa=1_500_000,
            period_start=today,
            period_end=today,
            description="Shop rent",
        )
        await db.commit()

        # Section 18.1 keeps fixed costs below contribution profit, and section
        # 86 warns against pretending fixed-cost allocation is accounting-grade.
        with pytest.raises(ConflictError):
            await expenses.allocate(rent.id)


class TestReallocation:
    async def test_re_allocating_without_a_reason_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await delivered(client, shop, db, cod=140_500)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=10_000,
            period_start=today,
            period_end=today,
            description="Facebook boost",
        )
        await expenses.allocate(expense.id)
        await db.commit()

        # Section 86: do not rewrite historical profit silently.
        with pytest.raises(ValidationError):
            await expenses.allocate(expense.id)

    async def test_re_allocating_writes_a_revision_and_keeps_the_old_figure(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await delivered(client, shop, db, cod=100_000)
        second = await delivered(client, shop, db, cod=300_000)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        profit = ProfitService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=8_000,
            period_start=today,
            period_end=today,
            description="Facebook boost",
        )
        await expenses.allocate(expense.id)
        await db.commit()
        assert await expenses.snapshot_ad_cost(first["consignment"].id) == 4_000

        await expenses.allocate(
            expense.id,
            method=AllocationMethod.PROPORTIONAL_TO_REVENUE,
            reason="Equal split was misleading for these order sizes",
        )
        await db.commit()

        assert await expenses.snapshot_ad_cost(first["consignment"].id) == 2_000
        assert await expenses.snapshot_ad_cost(second["consignment"].id) == 6_000

        revisions = await profit.revisions(first["consignment"].id)
        # Snapshot at delivery, then one per allocation run.
        assert [row.revision for row in revisions] == [1, 2, 3]
        # The earlier figure survives exactly as it was read.
        assert revisions[1].ad_cost_paisa == 4_000
        assert revisions[2].allocation_method == str(AllocationMethod.PROPORTIONAL_TO_REVENUE)
        assert revisions[2].allocation_version == ALLOCATION_VERSION

    async def test_re_allocating_replaces_the_share_rather_than_adding_one(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered(client, shop, db, cod=140_500)
        today = business_date(at=utc_now())

        expenses = ExpenseService(db)
        expense = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=10_000,
            period_start=today,
            period_end=today,
            description="Facebook boost",
        )
        await expenses.allocate(expense.id)
        await expenses.allocate(expense.id, reason="Ran it again")
        await db.commit()

        allocations = await expenses.allocations_for(parcel["consignment"].id)
        assert len(allocations) == 1
        assert await expenses.snapshot_ad_cost(parcel["consignment"].id) == 10_000

    async def test_two_expenses_both_reach_the_same_parcel(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered(client, shop, db, cod=140_500)
        today = business_date(at=utc_now())
        expenses = ExpenseService(db)

        for description, amount in (("Facebook", 6_000), ("Instagram", 4_000)):
            expense = await expenses.record(
                kind=ExpenseKind.AD_SPEND,
                amount_paisa=amount,
                period_start=today,
                period_end=today,
                description=description,
            )
            await expenses.allocate(expense.id)
        await db.commit()

        # Clearing one expense's shares must not clear the other's.
        assert await expenses.snapshot_ad_cost(parcel["consignment"].id) == 10_000
        assert len(await expenses.allocations_for(parcel["consignment"].id)) == 2


class TestTotals:
    async def test_spend_is_split_by_kind_and_by_whether_it_landed(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await delivered(client, shop, db, cod=140_500)
        today = business_date(at=utc_now())
        expenses = ExpenseService(db)

        ads = await expenses.record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=10_000,
            period_start=today,
            period_end=today,
            description="Facebook",
        )
        await expenses.record(
            kind=ExpenseKind.FIXED,
            amount_paisa=1_500_000,
            period_start=today,
            period_end=today,
            description="Rent",
        )
        await expenses.allocate(ads.id)
        await db.commit()

        totals = await expenses.totals()
        assert totals["ad_spend_paisa"] == 10_000
        assert totals["fixed_paisa"] == 1_500_000
        assert totals["allocated_paisa"] == 10_000
        # Rent is recorded but never pushed onto a parcel.
        assert totals["unallocated_paisa"] == 1_500_000


class TestIsolation:
    async def test_another_shops_expenses_are_invisible(
        self, client: AsyncClient, db: AsyncSession, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Shop One")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
        today = business_date(at=utc_now())
        await ExpenseService(db).record(
            kind=ExpenseKind.AD_SPEND,
            amount_paisa=500_000,
            period_start=today,
            period_end=today,
            description="Facebook",
        )
        await db.commit()

        second = await signed_in_shop(client, "01977777777", shop_name="Shop Two")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(second["tenant_id"])))
        assert (await db.execute(sa.select(Expense))).scalars().all() == []
        assert (await ExpenseService(db).totals())["ad_spend_paisa"] == 0
