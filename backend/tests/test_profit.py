"""The profit engine.

Master spec sections 18, 19, 85 and 135. Two properties carry the module:

* **the formula is arithmetic on integer paisa** and nothing else (section 1.5:
  AI is never the source of truth for profit);
* **a figure never changes underneath a seller.** Section 55: *"old order profit
  does not change after new rate rule."* Recalculating writes a revision; the
  old numbers stay readable.
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
from app.consignments.service import DeliveryOutcome, ItemOutcome
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.profit.engine import (
    ChargeInput,
    ProfitInput,
    calculate_profit,
    margin_basis_points,
)
from app.profit.models import (
    CALCULATION_VERSION,
    ChargeKind,
    ChargeSource,
    ConsignmentCharge,
    ProfitQuality,
    ProfitSnapshot,
    ReturnReason,
)
from app.profit.service import ProfitService


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Profit Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


class TestTruthHierarchy:
    def test_the_hierarchy_is_the_order_section_85_lists(self) -> None:
        # settled → booked → seller-entered → estimate → unknown.
        assert ChargeSource.SETTLED.rank < ChargeSource.BOOKED.rank
        assert ChargeSource.BOOKED.rank < ChargeSource.SELLER.rank
        assert ChargeSource.SELLER.rank < ChargeSource.ESTIMATE.rank
        assert ChargeSource.ESTIMATE.rank < ChargeSource.UNKNOWN.rank

    def test_quality_is_the_quality_of_the_worst_input(self) -> None:
        assert (
            ProfitQuality.from_sources(essential=[ChargeSource.SETTLED, ChargeSource.SETTLED])
            is ProfitQuality.ACTUAL
        )
        # One estimate is enough to stop the whole figure being called exact.
        assert (
            ProfitQuality.from_sources(
                essential=[ChargeSource.SETTLED],
                peripheral=[ChargeSource.ESTIMATE],
            )
            is ProfitQuality.ESTIMATED
        )

    def test_a_missing_essential_is_worse_than_a_missing_charge(self) -> None:
        # Section 135's own example calls a figure with a missing ad cost
        # "Estimated (ad cost missing)" — usable, with the gap named. Without
        # the product cost there is nothing to show at all.
        assert (
            ProfitQuality.from_sources(
                essential=[ChargeSource.SETTLED, ChargeSource.SETTLED],
                peripheral=[ChargeSource.UNKNOWN],
            )
            is ProfitQuality.ESTIMATED
        )
        assert (
            ProfitQuality.from_sources(essential=[ChargeSource.SETTLED, ChargeSource.UNKNOWN])
            is ProfitQuality.MISSING
        )

    def test_a_booking_quote_is_an_estimate_not_an_actual(self) -> None:
        # It is what the provider *said it would* charge, which is regularly
        # not what it charges.
        assert (
            ProfitQuality.from_sources(essential=[ChargeSource.BOOKED]) is ProfitQuality.ESTIMATED
        )
        assert not ChargeSource.BOOKED.is_actual


class TestFormula:
    def test_the_section_18_1_formula(self) -> None:
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=140_500,
                item_cost_paisa=65_000,
                charges=[
                    ChargeInput(ChargeKind.DELIVERY, 8_000, ChargeSource.SETTLED),
                    ChargeInput(ChargeKind.COD_FEE, 1_400, ChargeSource.SETTLED),
                    ChargeInput(ChargeKind.PACKAGING, 2_000, ChargeSource.SELLER),
                ],
                ad_cost_paisa=5_000,
                ad_source=ChargeSource.SELLER,
            )
        )
        # 140,500 − 65,000 − 8,000 − 1,400 − 2,000 − 5,000
        assert result.contribution_profit_paisa == 59_100
        assert result.total_cost_paisa == 81_400
        assert result.quality is ProfitQuality.ACTUAL

    def test_a_discount_is_not_subtracted_twice(self) -> None:
        # The discount is already inside the collected amount. Subtracting it
        # again would quietly understate every discounted order.
        with_discount = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=100_000,
                item_cost_paisa=40_000,
                discount_paisa=20_000,
            )
        )
        without = calculate_profit(
            ProfitInput(realized_revenue_paisa=100_000, item_cost_paisa=40_000)
        )
        assert with_discount.contribution_profit_paisa == 60_000
        assert with_discount.contribution_profit_paisa == without.contribution_profit_paisa
        # Kept as a memo so the seller can still see it.
        assert with_discount.discount_paisa == 20_000

    def test_a_return_can_lose_money_with_nothing_collected(self) -> None:
        # Section 17.9, verbatim: return charges can create negative profit even
        # with zero collected revenue.
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=0,
                item_cost_paisa=0,
                charges=[
                    ChargeInput(ChargeKind.DELIVERY, 8_000, ChargeSource.SETTLED),
                    ChargeInput(ChargeKind.RETURN, 6_000, ChargeSource.SETTLED),
                    ChargeInput(ChargeKind.PACKAGING, 2_000, ChargeSource.SELLER),
                ],
            )
        )
        assert result.contribution_profit_paisa == -16_000
        assert result.is_loss
        # No margin: there was no revenue to have a margin against, and 0%
        # would read as break-even.
        assert result.margin_basis_points is None

    def test_a_damaged_return_costs_the_goods_too(self) -> None:
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=0,
                item_cost_paisa=0,
                write_off_cost_paisa=65_000,
                charges=[ChargeInput(ChargeKind.RETURN, 6_000, ChargeSource.SETTLED)],
            )
        )
        assert result.contribution_profit_paisa == -71_000

    def test_a_missing_input_names_itself(self) -> None:
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=140_500,
                item_cost_paisa=65_000,
                charges=[
                    ChargeInput(ChargeKind.DELIVERY, 0, ChargeSource.UNKNOWN),
                ],
                ad_source=ChargeSource.UNKNOWN,
            )
        )
        # Section 135's "Estimated (ad cost missing)": usable, with the gaps
        # named. Neither of these is an essential term.
        assert result.quality is ProfitQuality.ESTIMATED
        assert "delivery charge" in result.missing_inputs
        assert "ad cost" in result.missing_inputs

    def test_an_unknown_product_cost_makes_the_figure_meaningless(self) -> None:
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=140_500,
                item_cost_paisa=0,
                item_cost_source=ChargeSource.UNKNOWN,
            )
        )
        # Profit is revenue minus cost, and one of those terms is absent.
        assert result.quality is ProfitQuality.MISSING
        assert "product cost" in result.missing_inputs

    def test_missing_inputs_are_listed_once_each(self) -> None:
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=100_000,
                item_cost_paisa=0,
                charges=[
                    ChargeInput(ChargeKind.OTHER, 0, ChargeSource.UNKNOWN),
                    ChargeInput(ChargeKind.OTHER, 0, ChargeSource.UNKNOWN),
                ],
            )
        )
        assert result.missing_inputs.count("other cost") == 1

    def test_margin_is_basis_points_not_a_float(self) -> None:
        # 59,100 of 140,500 is 42.06%.
        assert margin_basis_points(59_100, 140_500) == 4206
        assert margin_basis_points(0, 140_500) == 0
        assert margin_basis_points(-16_000, 0) is None

    def test_the_formula_holds_at_lakh_scale(self) -> None:
        result = calculate_profit(
            ProfitInput(
                realized_revenue_paisa=28_450_000,
                item_cost_paisa=12_000_000,
                charges=[
                    ChargeInput(ChargeKind.DELIVERY, 800_000, ChargeSource.SETTLED),
                ],
            )
        )
        assert result.contribution_profit_paisa == 15_650_000
        assert result.margin_basis_points == 5501


class TestChargeSnapshots:
    async def test_a_settled_figure_supersedes_a_booking_quote(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        profit = ProfitService(db)

        quoted = await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=6_000,
            source=ChargeSource.BOOKED,
        )
        settled = await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=8_000,
            source=ChargeSource.SETTLED,
            source_ref="STMT-1",
        )
        await db.commit()

        await db.refresh(quoted)
        # The quote survives exactly as written, pointing at what replaced it —
        # so the seller can see the courier charged ৳20 more than it said.
        assert quoted.amount_paisa == 6_000
        assert quoted.superseded_by == settled.id
        assert not quoted.is_current

        current = await profit.charges_for(parcel["consignment"].id)
        assert len(current) == 1
        assert current[0].amount_paisa == 8_000

    async def test_a_weaker_source_cannot_silently_overwrite_a_stronger_one(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        profit = ProfitService(db)

        await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=8_000,
            source=ChargeSource.SETTLED,
        )
        await db.commit()

        # A configured estimate arriving after the real figure would replace
        # the truth with a guess.
        with pytest.raises(ConflictError):
            await profit.record_charge(
                parcel["consignment"].id,
                kind=ChargeKind.DELIVERY,
                amount_paisa=6_000,
                source=ChargeSource.ESTIMATE,
            )

    async def test_a_person_may_override_with_a_reason(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        profit = ProfitService(db)

        await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=8_000,
            source=ChargeSource.SETTLED,
        )
        # Section 17.3's "audited correction": possible, but never silent.
        corrected = await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=6_000,
            source=ChargeSource.SELLER,
            reason="Courier refunded the difference in cash",
        )
        await db.commit()

        assert corrected.amount_paisa == 6_000
        assert corrected.reason.startswith("Courier refunded")
        history = await profit.charges_for(parcel["consignment"].id, include_superseded=True)
        assert len(history) == 2

    async def test_a_negative_charge_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        with pytest.raises(ValidationError):
            await ProfitService(db).record_charge(
                parcel["consignment"].id,
                kind=ChargeKind.DELIVERY,
                amount_paisa=-100,
                source=ChargeSource.SETTLED,
            )


class TestProfitSnapshots:
    async def _delivered(
        self, client: AsyncClient, shop: dict, db: AsyncSession, *, cod: int = 140_500
    ) -> dict:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=cod)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()
        parcel["profit"] = ProfitService(db)
        return parcel

    async def test_a_delivered_parcel_gets_a_snapshot(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, cod=140_500)

        # Recording the outcome wrote it. Nobody had to ask: a figure that
        # only existed once the Insights screen was opened would mean a parcel
        # nobody looked at never reached a total.
        first = await parcel["profit"].current_snapshot(parcel["consignment"].id)
        assert first is not None
        assert first.revision == 1
        assert first.is_current
        assert first.calculation_version == CALCULATION_VERSION
        assert first.realized_revenue_paisa == 140_500
        # The product cost came from the order line's snapshot, not from the
        # product's current cost.
        assert first.item_cost_paisa == 65_000
        assert first.contribution_profit_paisa == 75_500

        await parcel["profit"].record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=8_000,
            source=ChargeSource.SETTLED,
        )
        snapshot = await parcel["profit"].snapshot(parcel["consignment"].id)
        await db.commit()

        # The courier's charge arriving is a revision, not an edit.
        assert snapshot.revision == 2
        assert snapshot.delivery_charge_paisa == 8_000
        assert snapshot.contribution_profit_paisa == 67_500

    async def test_a_parcel_still_moving_has_no_profit(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        # A figure that changes under the seller is worse than no figure.
        with pytest.raises(ConflictError):
            await ProfitService(db).snapshot(parcel["consignment"].id)

    async def test_recalculating_writes_a_revision_and_keeps_the_old_one(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, cod=140_500)
        profit = parcel["profit"]

        await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=6_000,
            source=ChargeSource.BOOKED,
        )
        first = await profit.snapshot(parcel["consignment"].id)
        await db.commit()
        first_profit = first.contribution_profit_paisa

        await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=8_000,
            source=ChargeSource.SETTLED,
        )
        second = await profit.snapshot(parcel["consignment"].id, reason="Statement arrived")
        await db.commit()

        # Section 17.4: a new revision, never an edit. Revision 1 was written
        # by the delivery itself, so these are the second and third.
        assert first.revision == 2
        assert second.revision == 3
        assert second.contribution_profit_paisa == first_profit - 2_000

        await db.refresh(first)
        assert first.contribution_profit_paisa == first_profit
        assert not first.is_current
        assert first.superseded_by == second.id

        revisions = await profit.revisions(parcel["consignment"].id)
        assert [row.revision for row in revisions] == [1, 2, 3]

    async def test_an_estimated_charge_marks_the_whole_figure_estimated(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db)
        await parcel["profit"].record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=6_000,
            source=ChargeSource.ESTIMATE,
        )
        snapshot = await parcel["profit"].snapshot(parcel["consignment"].id)
        await db.commit()

        # Section 85: an estimated profit is never shown as exact.
        assert snapshot.profit_quality is ProfitQuality.ESTIMATED

    async def test_a_returned_parcel_snapshots_a_loss(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.RETURNED),
        )
        await db.commit()

        profit = ProfitService(db)
        await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.RETURN,
            amount_paisa=6_000,
            source=ChargeSource.SETTLED,
        )
        snapshot = await profit.snapshot(
            parcel["consignment"].id,
            return_reason=ReturnReason.CUSTOMER_REFUSED,
        )
        await db.commit()

        assert snapshot.realized_revenue_paisa == 0
        # The goods came back sellable, so they are stock rather than a loss.
        assert snapshot.item_cost_paisa == 0
        assert snapshot.write_off_cost_paisa == 0
        assert snapshot.contribution_profit_paisa == -6_000
        assert snapshot.return_reason == "CUSTOMER_REFUSED"
        assert snapshot.margin_basis_points is None

    async def test_a_partial_delivery_counts_only_the_delivered_units(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(
            client, shop, db, cod_paisa=300_000, quantity=3, opening_stock=10
        )
        consignment = await parcel["consignments"].get(parcel["consignment"].id)
        await parcel["consignments"].record_outcome(
            consignment.id,
            DeliveryOutcome(
                status=ConsignmentStatus.PARTIAL_DELIVERED,
                items=[
                    ItemOutcome(
                        consignment_item_id=consignment.items[0].id,
                        qty_delivered=2,
                        qty_returned=1,
                    )
                ],
            ),
        )
        await db.commit()

        snapshot = await ProfitService(db).snapshot(consignment.id)
        await db.commit()

        # Two of three: two units' revenue and two units' cost. The third is
        # back on the shelf and costs nothing.
        assert snapshot.realized_revenue_paisa == 200_000
        assert snapshot.item_cost_paisa == 130_000

    async def test_revenue_becomes_what_arrived_once_it_is_settled(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, cod=140_500)
        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        await parcel["receivables"].apply_settlement(
            receivable.id, amount_paisa=132_500, source_ref="STMT-1"
        )
        # Fully settled with a deduction recorded, so nothing is outstanding.
        await parcel["receivables"].record_deduction(
            receivable.id,
            amount_paisa=8_000,
            event_type=__import__(
                "app.ledger.models", fromlist=["LedgerEventType"]
            ).LedgerEventType.COD_FEE_APPLIED,
            source_ref="STMT-1",
        )
        await db.commit()

        snapshot = await parcel["profit"].snapshot(parcel["consignment"].id)
        await db.commit()

        # What arrived, not what was owed. They differ by the deduction.
        assert snapshot.realized_revenue_paisa == 132_500
        # Still estimated: this shop has recorded no ad spend, so its ad cost
        # is genuinely unknown. Section 135's "Estimated (ad cost missing)".
        assert snapshot.profit_quality is ProfitQuality.ESTIMATED
        assert "ad cost" in snapshot.missing_inputs

    async def test_delivered_but_unpaid_revenue_is_only_an_estimate(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, cod=140_500)
        snapshot = await parcel["profit"].snapshot(parcel["consignment"].id)
        await db.commit()

        # The courier collected it; whether it reaches the seller is not yet
        # known, so the figure is not called exact.
        assert snapshot.realized_revenue_paisa == 140_500
        assert snapshot.profit_quality is ProfitQuality.ESTIMATED


class TestTotals:
    async def test_only_current_revisions_are_counted(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()

        profit = ProfitService(db)
        await profit.snapshot(parcel["consignment"].id)
        await profit.snapshot(parcel["consignment"].id, reason="Recalculated")
        await db.commit()

        totals = await profit.totals()
        # Summing every revision would double-count exactly the orders that
        # were corrected.
        assert totals["parcel_count"] == 1
        assert totals["realized_revenue_paisa"] == 140_500

    async def test_the_quality_breakdown_counts_every_band(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()
        profit = ProfitService(db)
        await profit.snapshot(parcel["consignment"].id)
        await db.commit()

        breakdown = await profit.quality_breakdown()
        assert set(breakdown) == set(ProfitQuality)
        assert sum(breakdown.values()) == 1

    async def test_a_date_range_excludes_older_parcels(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(
                status=ConsignmentStatus.DELIVERED,
                occurred_at=utc_now() - timedelta(days=40),
            ),
        )
        await db.commit()
        profit = ProfitService(db)
        await profit.snapshot(parcel["consignment"].id)
        await db.commit()

        recent = await profit.totals(since=(utc_now() - timedelta(days=7)).date())
        assert recent["parcel_count"] == 0
        assert (await profit.totals())["parcel_count"] == 1


class TestIsolation:
    async def test_another_shops_profit_is_invisible(
        self, client: AsyncClient, db: AsyncSession, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Shop One")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
        parcel = await dispatched_parcel(client, first, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        profit = ProfitService(db)
        await profit.record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=8_000,
            source=ChargeSource.SETTLED,
        )
        await profit.snapshot(parcel["consignment"].id)
        await db.commit()

        second = await signed_in_shop(client, "01966666666", shop_name="Shop Two")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(second["tenant_id"])))
        assert (await db.execute(sa.select(ProfitSnapshot))).scalars().all() == []
        assert (await db.execute(sa.select(ConsignmentCharge))).scalars().all() == []
        assert (await ProfitService(db).totals())["parcel_count"] == 0
