"""COD receivables and the delivery events that create them.

Master spec sections 15, 10.3 and 17. The property under test throughout is
section 1.4: **delivered is not paid.** A parcel reaching the customer creates
a receivable; only money arriving settles it, and the two are never allowed to
imply each other.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import ConsignmentStatus
from app.consignments.service import DeliveryOutcome, ItemOutcome, ReturnReceiptLine
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.ledger.models import LedgerBucket, LedgerEventType
from app.money.models import CodReceivable, ReceivableStatus, can_transition
from app.products.models import Product, StockMovement, StockMovementReason
from tests.conftest_commerce import dispatched_parcel, signed_in_shop


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str, db: AsyncSession) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Money Shop")
    # The money services run against the `db` session directly, so the tenant
    # the HTTP requests are scoped to has to be put in scope here too.
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


class TestStateMachine:
    def test_a_written_off_receivable_is_terminal(self) -> None:
        for target in ReceivableStatus:
            if target is ReceivableStatus.WRITTEN_OFF:
                continue
            assert not can_transition(ReceivableStatus.WRITTEN_OFF, target)

    def test_expected_money_is_not_counted_as_collectible(self) -> None:
        # A parcel in transit is not money the courier is holding. Counting it
        # would inflate a seller's outstanding balance by their whole pipeline.
        assert not ReceivableStatus.EXPECTED.is_collectible
        assert ReceivableStatus.EXPECTED.is_open
        assert ReceivableStatus.ELIGIBLE.is_collectible

    def test_settled_and_written_off_are_not_open(self) -> None:
        assert not ReceivableStatus.SETTLED.is_open
        assert not ReceivableStatus.WRITTEN_OFF.is_open

    def test_a_disputed_receivable_is_still_money_owed(self) -> None:
        # Raising a case does not make the money stop existing.
        assert ReceivableStatus.DISPUTED.is_collectible


class TestDispatch:
    async def test_dispatch_opens_an_expected_receivable_and_moves_stock(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=125_000)

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable is not None
        assert receivable.receivable_status is ReceivableStatus.EXPECTED
        # Nothing is owed yet: the parcel has not been delivered.
        assert receivable.outstanding_paisa == 0

        product = await db.get(Product, uuid.UUID(parcel["product"]["id"]))
        await db.refresh(product)
        assert product.stock_on_hand == 9

        movements = (
            (
                await db.execute(
                    sa.select(StockMovement).where(
                        StockMovement.product_id == product.id,
                        StockMovement.reason == str(StockMovementReason.BOOKED_DECREMENT),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(movements) == 1

    async def test_dispatch_writes_no_ledger_entry(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        balances = await parcel["ledger"].balances()
        # Section 80's examples start at DELIVERY_CONFIRMED. Crediting a
        # receivable at dispatch would show a seller money for every parcel in
        # transit, including the ones that come back.
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0

    async def test_one_order_cannot_have_two_live_parcels(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        with pytest.raises(ConflictError):
            await parcel["consignments"].dispatch_manual(parcel["consignment"].order_id)


class TestDelivery:
    async def test_a_full_delivery_makes_the_whole_cod_collectible(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)

        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.ELIGIBLE
        assert receivable.collectible_paisa == 140_500
        assert receivable.outstanding_paisa == 140_500
        assert receivable.eligible_at is not None

        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 140_500
        # Delivered is not paid.
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 0

    async def test_a_partial_delivery_collects_only_the_delivered_units(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(
            client, shop, db, cod_paisa=300_000, quantity=3, opening_stock=10
        )
        consignment = await parcel["consignments"].get(parcel["consignment"].id)
        line = consignment.items[0]

        await parcel["consignments"].record_outcome(
            consignment.id,
            DeliveryOutcome(
                status=ConsignmentStatus.PARTIAL_DELIVERED,
                items=[ItemOutcome(consignment_item_id=line.id, qty_delivered=2, qty_returned=1)],
            ),
        )
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(consignment.id)
        # Section 17.8: a partial delivery creates a partial collectible amount
        # and never assumes the original COD.
        assert receivable.collectible_paisa == 200_000
        assert receivable.outstanding_paisa == 200_000

    async def test_a_partial_delivery_restores_the_returned_units(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(
            client, shop, db, cod_paisa=300_000, quantity=3, opening_stock=10
        )
        consignment = await parcel["consignments"].get(parcel["consignment"].id)
        line = consignment.items[0]

        await parcel["consignments"].record_outcome(
            consignment.id,
            DeliveryOutcome(
                status=ConsignmentStatus.PARTIAL_DELIVERED,
                items=[ItemOutcome(consignment_item_id=line.id, qty_delivered=2, qty_returned=1)],
            ),
        )
        await db.commit()

        product = await db.get(Product, uuid.UUID(parcel["product"]["id"]))
        await db.refresh(product)
        # V2.2: the courier's word does not restock. 10 − 3 shipped, until the
        # seller receives the returned unit.
        assert product.stock_on_hand == 7

        await parcel["consignments"].receive_return(
            consignment.id,
            [ReturnReceiptLine(consignment_item_id=line.id, qty_restocked=1, qty_not_restocked=0)],
        )
        await db.commit()
        await db.refresh(product)
        # 10 − 3 shipped + 1 received back.
        assert product.stock_on_hand == 8

    async def test_a_partial_delivery_without_quantities_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, quantity=2, cod_paisa=200_000)
        # Guessing the whole COD for a partial is the section 17.8 error.
        with pytest.raises(ValidationError):
            await parcel["consignments"].record_outcome(
                parcel["consignment"].id,
                DeliveryOutcome(status=ConsignmentStatus.PARTIAL_DELIVERED),
            )

    async def test_more_units_than_were_shipped_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, quantity=2, cod_paisa=200_000)
        consignment = await parcel["consignments"].get(parcel["consignment"].id)
        line = consignment.items[0]

        with pytest.raises(ValidationError):
            await parcel["consignments"].record_outcome(
                consignment.id,
                DeliveryOutcome(
                    status=ConsignmentStatus.PARTIAL_DELIVERED,
                    items=[
                        ItemOutcome(
                            consignment_item_id=line.id,
                            qty_delivered=2,
                            qty_returned=1,
                        )
                    ],
                ),
            )

    async def test_a_return_collects_nothing_and_restores_stock(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)

        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.RETURNED, note="Customer refused at the door"),
        )
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.NOT_DUE
        assert receivable.outstanding_paisa == 0

        product = await db.get(Product, uuid.UUID(parcel["product"]["id"]))
        await db.refresh(product)
        # Returned by the courier is not yet back on the shelf (V2.2).
        assert product.stock_on_hand == 9

        consignment = await parcel["consignments"].get(parcel["consignment"].id)
        await parcel["consignments"].receive_return(
            consignment.id,
            [
                ReturnReceiptLine(
                    consignment_item_id=consignment.items[0].id,
                    qty_restocked=1,
                    qty_not_restocked=0,
                )
            ],
        )
        await db.commit()
        await db.refresh(product)
        assert product.stock_on_hand == 10

    async def test_a_return_after_a_delivery_reverses_the_receivable(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()

        # A mistaken delivery corrected later. The credit is reversed rather
        # than erased, because the delivery genuinely was reported.
        consignment = await parcel["consignments"].get(parcel["consignment"].id)
        consignment.status = str(ConsignmentStatus.IN_TRANSIT)
        await db.flush()
        await parcel["consignments"].record_outcome(
            consignment.id,
            DeliveryOutcome(status=ConsignmentStatus.RETURNED, note="Reported in error"),
        )
        await db.commit()

        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0

        entries = await parcel["ledger"].entries_for("consignment", parcel["consignment"].id)
        assert [entry.event_type for entry in entries] == [
            str(LedgerEventType.DELIVERY_CONFIRMED),
            str(LedgerEventType.RETURN_CONFIRMED),
        ]

    async def test_a_terminal_parcel_cannot_be_re_reported(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()

        with pytest.raises(ConflictError):
            await parcel["consignments"].record_outcome(
                parcel["consignment"].id,
                DeliveryOutcome(status=ConsignmentStatus.RETURNED),
            )


class TestSettlement:
    async def _delivered(self, client: AsyncClient, shop: dict, db: AsyncSession, cod: int):
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=cod)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()
        parcel["receivable"] = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        return parcel

    async def test_paying_in_full_settles_it(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)

        receivable = await parcel["receivables"].apply_settlement(
            parcel["receivable"].id, amount_paisa=140_500, source_ref="STMT-1"
        )
        await db.commit()

        assert receivable.receivable_status is ReceivableStatus.SETTLED
        assert receivable.outstanding_paisa == 0
        assert receivable.settled_at is not None

        balances = await parcel["ledger"].balances()
        # The money left the receivable pot and entered the settled pot.
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 140_500

    async def test_paying_part_of_it_leaves_the_rest_outstanding(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)

        receivable = await parcel["receivables"].apply_settlement(
            parcel["receivable"].id, amount_paisa=100_000, source_ref="STMT-1"
        )
        await db.commit()

        assert receivable.receivable_status is ReceivableStatus.PARTIALLY_SETTLED
        assert receivable.outstanding_paisa == 40_500

    async def test_paying_more_than_is_owed_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)

        # Section 17.2. An overpayment is a reconciliation case for a human,
        # not something to absorb silently — absorbing it is how a provider's
        # error quietly becomes the seller's loss.
        with pytest.raises(ConflictError):
            await parcel["receivables"].apply_settlement(
                parcel["receivable"].id, amount_paisa=200_000, source_ref="STMT-1"
            )

    async def test_two_lines_can_settle_one_parcel(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)

        await parcel["receivables"].apply_settlement(
            parcel["receivable"].id, amount_paisa=100_000, source_ref="STMT-1"
        )
        receivable = await parcel["receivables"].apply_settlement(
            parcel["receivable"].id, amount_paisa=40_500, source_ref="STMT-2"
        )
        await db.commit()

        # Section 17.2 allows many settlement lines per consignment; what it
        # forbids is exceeding the collectible total.
        assert receivable.receivable_status is ReceivableStatus.SETTLED
        assert receivable.settled_paisa == 140_500

    async def test_unmatching_is_a_reversal_not_a_subtraction(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)
        await parcel["receivables"].apply_settlement(
            parcel["receivable"].id, amount_paisa=140_500, source_ref="STMT-1"
        )
        await db.commit()

        receivable = await parcel["receivables"].reverse_settlement(
            parcel["receivable"].id,
            amount_paisa=140_500,
            reason="Matched the wrong parcel",
            source_ref="STMT-1",
        )
        await db.commit()

        assert receivable.receivable_status is ReceivableStatus.ELIGIBLE
        assert receivable.outstanding_paisa == 140_500

        # Section 81.8: the original entries stay and two more undo them, so
        # the history of the mistake survives alongside its fix.
        entries = await parcel["ledger"].entries_for("cod_receivable", parcel["receivable"].id)
        assert [entry.event_type for entry in entries].count(
            str(LedgerEventType.PAYOUT_APPLIED)
        ) == 2
        assert [entry.event_type for entry in entries].count(
            str(LedgerEventType.PAYOUT_REVERSED)
        ) == 2

        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 0
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 140_500

    async def test_a_deduction_is_visible_rather_than_netted_away(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)

        await parcel["receivables"].record_deduction(
            parcel["receivable"].id,
            amount_paisa=8_000,
            event_type=LedgerEventType.COD_FEE_APPLIED,
            source_ref="STMT-1",
            label="COD charge",
        )
        receivable = await parcel["receivables"].apply_settlement(
            parcel["receivable"].id, amount_paisa=132_500, source_ref="STMT-1"
        )
        await db.commit()

        assert receivable.deduction_paisa == 8_000
        assert receivable.outstanding_paisa == 0

        balances = await parcel["ledger"].balances()
        # The fee lands in its own bucket, so a seller can see what the courier
        # took rather than only that ৳8,000 went missing (section 84).
        assert balances[LedgerBucket.COD_FEE].net_paisa == -8_000

    async def test_writing_off_records_the_loss(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await self._delivered(client, shop, db, 140_500)

        receivable = await parcel["receivables"].write_off(
            parcel["receivable"].id, reason="Courier went out of business"
        )
        await db.commit()

        assert receivable.receivable_status is ReceivableStatus.WRITTEN_OFF
        assert receivable.outstanding_paisa == 0

        balances = await parcel["ledger"].balances()
        # The money is gone, and the ledger says so. It does not simply vanish
        # from the outstanding total with no trace.
        assert balances[LedgerBucket.WRITE_OFF].net_paisa == -140_500


class TestAging:
    async def test_outstanding_money_is_bucketed_by_how_long_it_has_waited(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        long_ago = utc_now() - timedelta(days=9)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED, occurred_at=long_ago),
        )
        await db.commit()

        aging = await parcel["receivables"].aging()
        by_label = {bucket.label: (count, amount) for bucket, count, amount in aging}
        assert by_label["8-14 days"] == (1, 140_500)
        assert by_label["0-3 days"] == (0, 0)

    async def test_settled_money_leaves_the_aging_report(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        await parcel["receivables"].apply_settlement(
            receivable.id, amount_paisa=140_500, source_ref="STMT-1"
        )
        await db.commit()

        assert await parcel["receivables"].outstanding_total() == 0

    async def test_parcels_in_transit_are_not_counted_as_outstanding(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        # Dispatched, not delivered. Nobody is holding this money yet.
        assert await parcel["receivables"].outstanding_total() == 0


class TestIsolation:
    async def test_another_shops_receivables_are_invisible(
        self, client: AsyncClient, db: AsyncSession, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Shop One")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
        parcel = await dispatched_parcel(client, first, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.DELIVERED),
        )
        await db.commit()

        second = await signed_in_shop(client, "01911111111", shop_name="Shop Two")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(second["tenant_id"])))
        rows = (await db.execute(sa.select(CodReceivable))).scalars().all()
        assert rows == []
