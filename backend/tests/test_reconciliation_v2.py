"""Reconciliation V2: expected against actual, cases, idempotency.

The seller's three questions — what should the courier have paid, what did it
pay, what is different — answered per parcel by reconciliation items, with
every figure traceable to the ledger's own records. These tests pin the money
rules: what counts as a mismatch, what is only a charge to accept, and that no
retry, re-upload or re-export can settle a parcel twice.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_reconciliation import delivered_parcel, statement
from tests.test_team import _member_session

from app.consignments.models import ConsignmentStatus
from app.consignments.service import DeliveryOutcome
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.ledger.models import LedgerBucket
from app.money.models import ReceivableStatus
from app.payouts.models import PayoutLineStatus
from app.payouts.service import PayoutService
from app.profit.models import ChargeKind, ChargeSource
from app.profit.service import ProfitService
from app.reconciliation.evaluation import LineEvidence, evaluate_parcel, expected_charge_from
from app.reconciliation.models import CaseEventAction, CaseKind, CaseStatus, ItemStatus
from app.reconciliation.scoring import ScoringConfig
from app.reconciliation.service import ReconciliationService
from app.tenants.roles import TenantRole


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Recon V2 Shop", plan="pro")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


FEE_HEADER = "Invoice,Amount,Charge,Charge Type"


async def _import(db: AsyncSession, content: bytes, *, name: str = "s.csv"):
    payout = await PayoutService(db).import_statement(content, provider="manual", filename=name)
    await db.commit()
    return payout


async def _reconcile(db: AsyncSession, parcel: dict, payout_id: uuid.UUID):
    engine = ReconciliationService(db, receivables=parcel["receivables"])
    report = await engine.reconcile(payout_id)
    await db.commit()
    return engine, report


async def _item_for(engine: ReconciliationService, parcel: dict):
    receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
    items = await engine.list_items(limit=100)
    return next(item for item in items if item.receivable_id == receivable.id)


class TestEvaluation:
    """The pure rules, without a database."""

    def _line(self, amount: int, status: PayoutLineStatus = PayoutLineStatus.MATCHED):
        from app.payouts.models import PayoutLine

        return LineEvidence(
            line=PayoutLine(id=uuid.uuid4(), amount_paisa=amount, status=str(status))
        )

    def test_missing_expected_charge_is_not_zero(self) -> None:
        # No charge on record: the COD is still compared, the charge is not
        # guessed at, and the item is not a "charge mismatch".
        evaluation = evaluate_parcel(
            expected_cod_paisa=140_500,
            lines=[self._line(140_500)],
            expected_charge=None,
            config=ScoringConfig(),
        )
        assert evaluation.status is ItemStatus.MATCHED
        assert evaluation.expected_charge_paisa is None
        assert evaluation.detail["charge_verified"] is False
        assert evaluation.difference_paisa == 0

    def test_split_payments_are_judged_on_their_total(self) -> None:
        halves = [self._line(70_000), self._line(70_500)]
        evaluation = evaluate_parcel(
            expected_cod_paisa=140_500, lines=halves, expected_charge=None, config=ScoringConfig()
        )
        assert evaluation.status is ItemStatus.MATCHED
        short = evaluate_parcel(
            expected_cod_paisa=200_000, lines=halves, expected_charge=None, config=ScoringConfig()
        )
        assert short.status is ItemStatus.PARTIAL

    def test_the_best_expectation_wins_and_settled_is_never_expected(self) -> None:
        from app.profit.models import ConsignmentCharge

        now = utc_now()
        charges = [
            ConsignmentCharge(
                kind="DELIVERY", source="ESTIMATE", amount_paisa=9_000, occurred_at=now
            ),
            ConsignmentCharge(
                kind="DELIVERY", source="BOOKED", amount_paisa=6_000, occurred_at=now
            ),
            ConsignmentCharge(
                kind="DELIVERY", source="SETTLED", amount_paisa=12_000, occurred_at=now
            ),
            ConsignmentCharge(
                kind="PACKAGING", source="SELLER", amount_paisa=2_000, occurred_at=now
            ),
        ]
        expected = expected_charge_from(charges)
        assert expected is not None
        assert expected.amount_paisa == 6_000
        assert expected.source is ChargeSource.BOOKED


class TestMatchingAndExpectedVsActual:
    async def test_an_exact_match_is_a_matched_item(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(db, statement(f"{parcel['reference']},1405.00"))
        engine, report = await _reconcile(db, parcel, payout.id)

        assert report.exact_matches == 1
        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.MATCHED
        assert item.expected_cod_paisa == 140_500
        assert item.actual_cod_paisa == 140_500
        assert item.difference_paisa == 0

        summary = await engine.summary()
        assert summary.matched == 1
        assert summary.expected_paisa == 140_500
        assert summary.actual_paisa == 140_500
        assert summary.difference_paisa == 0

    async def test_an_itemised_fee_is_a_charge_not_an_underpayment(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(
            db, f"{FEE_HEADER}\n{parcel['reference']},1325.00,80,COD Fee\n".encode()
        )
        engine, _ = await _reconcile(db, parcel, payout.id)

        # Collected ৳1,405 (paid ৳1,325 + kept ৳80): the COD agrees.
        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.MATCHED
        assert item.actual_cod_paisa == 140_500
        assert item.actual_charge_paisa == 8_000
        assert item.actual_net_paisa == 132_500
        assert item.charges_pending_paisa == 8_000
        assert await engine.list_cases(kind=CaseKind.UNDERPAID) == []

        # Golden scenario 2 still holds until the seller accepts the fee.
        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.outstanding_paisa == 8_000

    async def test_a_short_payment_is_an_amount_mismatch(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(db, statement(f"{parcel['reference']},1000.00"))
        engine, _ = await _reconcile(db, parcel, payout.id)

        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.AMOUNT_MISMATCH
        assert item.difference_paisa == -40_500
        cases = await engine.list_cases(kind=CaseKind.UNDERPAID)
        assert len(cases) == 1
        assert item.case_id == cases[0].id
        summary = await engine.summary()
        assert summary.discrepancies == 1
        assert summary.difference_paisa == -40_500

    async def test_a_charge_above_the_booked_quote_is_a_charge_mismatch(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        await ProfitService(db).record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=6_000,
            source=ChargeSource.BOOKED,
        )
        await db.commit()
        payout = await _import(
            db, f"{FEE_HEADER}\n{parcel['reference']},1305.00,100,Delivery charge\n".encode()
        )
        engine, _ = await _reconcile(db, parcel, payout.id)

        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.CHARGE_MISMATCH
        assert item.expected_charge_paisa == 6_000
        assert item.expected_charge_source == "BOOKED"
        assert item.actual_charge_paisa == 10_000
        # Expected ৳1,345 net, got ৳1,305.
        assert item.expected_net_paisa == 134_500
        assert item.difference_paisa == -4_000
        cases = await engine.list_cases(kind=CaseKind.CHARGE_MISMATCH)
        assert len(cases) == 1
        assert cases[0].amount_paisa == 4_000

    async def test_charges_without_cod_are_missing_cod(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(
            db, f"{FEE_HEADER}\n{parcel['reference']},0,60,Delivery charge\n".encode()
        )
        engine, report = await _reconcile(db, parcel, payout.id)

        assert report.charge_only == 1
        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.MISSING_COD
        assert item.actual_net_paisa == -6_000
        assert len(await engine.list_cases(kind=CaseKind.MISSING_COD)) == 1
        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.settled_paisa == 0

    async def test_a_return_charge_row_is_a_return_adjustment(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        from tests.conftest_commerce import dispatched_parcel

        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id, DeliveryOutcome(status=ConsignmentStatus.RETURNED)
        )
        await ProfitService(db).record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.RETURN,
            amount_paisa=5_000,
            source=ChargeSource.BOOKED,
        )
        await db.commit()
        reference = parcel["consignment"].merchant_reference
        payout = await _import(db, f"{FEE_HEADER}\n{reference},0,90,Return charge\n".encode())
        engine, report = await _reconcile(db, parcel, payout.id)

        assert report.charge_only == 1
        items = await engine.list_items(statuses=[ItemStatus.RETURN_ADJUSTMENT])
        assert len(items) == 1
        assert items[0].actual_charge_paisa == 9_000
        assert items[0].difference_paisa == -4_000
        assert len(await engine.list_cases(kind=CaseKind.RETURN_CHARGE_MISMATCH)) == 1
        assert await engine.list_cases(kind=CaseKind.UNMAPPABLE_PAYOUT) == []

        # Accepting it books the cost once, against the parcel.
        result = await engine.accept_charges(items[0].id, reason="Courier confirmed")
        await db.commit()
        assert result.accepted_paisa == 9_000
        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.RETURN_CHARGE].net_paisa == -9_000
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0

    async def test_a_parcel_left_out_of_a_payout_is_missing_cod(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        paid = await delivered_parcel(client, shop, db, cod_paisa=100_000)
        forgotten = await delivered_parcel(client, shop, db, cod_paisa=90_000, delivered_days_ago=6)
        today = utc_now().date().isoformat()
        payout = await _import(
            db, f"Invoice,Amount,Delivered Date\n{paid['reference']},1000.00,{today}\n".encode()
        )
        engine, _ = await _reconcile(db, paid, payout.id)

        item = await _item_for(engine, forgotten)
        assert item.item_status is ItemStatus.MISSING_COD
        assert item.detail["reason"] == "not_in_payout"
        assert item.difference_paisa == -90_000
        assert len(await engine.list_cases(kind=CaseKind.MISSING_COD)) == 1

    async def test_two_parcels_sharing_a_reference_need_review(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await delivered_parcel(client, shop, db, cod_paisa=140_500, tracking="TRK-SAME")
        second = await delivered_parcel(client, shop, db, cod_paisa=140_500, tracking="TRK-SAME")
        payout = await _import(db, statement("TRK-SAME,1405.00", header="Tracking,Amount"))
        engine, report = await _reconcile(db, first, payout.id)

        assert report.exact_matches == 0
        items = await engine.list_items(statuses=[ItemStatus.UNMATCHED, ItemStatus.NEEDS_REVIEW])
        assert len(items) == 1
        for parcel in (first, second):
            receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
            assert receivable.settled_paisa == 0


class TestChargeAcceptance:
    async def test_accepting_charges_settles_the_parcel_once(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(
            db, f"{FEE_HEADER}\n{parcel['reference']},1325.00,80,COD Fee\n".encode()
        )
        engine, _ = await _reconcile(db, parcel, payout.id)
        item = await _item_for(engine, parcel)

        first = await engine.accept_payout_charges(payout.id)
        await db.commit()
        again = await engine.accept_charges(item.id)
        await db.commit()

        assert first.accepted_paisa == 8_000
        assert again.accepted_paisa == 0
        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.SETTLED
        assert receivable.outstanding_paisa == 0

        balances = await parcel["ledger"].balances()
        # Section 81.10: the ledger agrees with the dashboard after a deduction.
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0
        assert await parcel["receivables"].outstanding_total() == 0
        assert balances[LedgerBucket.COD_FEE].net_paisa == -8_000
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 132_500

        refreshed = await engine.get_item(item.id)
        assert refreshed.charges_pending_paisa == 0

    async def test_a_mismatched_charge_is_not_bulk_accepted(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        await ProfitService(db).record_charge(
            parcel["consignment"].id,
            kind=ChargeKind.DELIVERY,
            amount_paisa=6_000,
            source=ChargeSource.BOOKED,
        )
        await db.commit()
        payout = await _import(
            db, f"{FEE_HEADER}\n{parcel['reference']},1305.00,100,Delivery charge\n".encode()
        )
        engine, _ = await _reconcile(db, parcel, payout.id)

        bulk = await engine.accept_payout_charges(payout.id)
        assert bulk.accepted_paisa == 0

        item = await _item_for(engine, parcel)
        single = await engine.accept_charges(item.id, reason="Rate changed this month")
        await db.commit()
        assert single.accepted_paisa == 10_000
        case = (await engine.list_cases(kind=CaseKind.CHARGE_MISMATCH))[0]
        assert case.case_status is CaseStatus.RESOLVED
        events = await engine.case_events(case.id)
        assert events[-1].action == str(CaseEventAction.CHARGES_ACCEPTED)


class TestIdempotency:
    async def test_reconciling_twice_changes_nothing(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(
            db, statement(f"{parcel['reference']},1000.00", "UNKNOWN-REF,500.00")
        )
        engine, first = await _reconcile(db, parcel, payout.id)
        before = await parcel["ledger"].balances()
        cases_before = len(await engine.list_cases(limit=100))
        items_before = len(await engine.list_items(limit=100))

        _, second = await _reconcile(db, parcel, payout.id)

        assert first.applied_paisa == 100_000
        assert second.applied_paisa == 0
        assert second.cases_opened == 0
        assert len(await engine.list_cases(limit=100)) == cases_before
        assert len(await engine.list_items(limit=100)) == items_before
        after = await parcel["ledger"].balances()
        assert {k: v.net_paisa for k, v in after.items()} == {
            k: v.net_paisa for k, v in before.items()
        }

    async def test_the_same_file_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        content = statement(f"{parcel['reference']},1405.00")
        await _import(db, content)
        with pytest.raises(ConflictError):
            await _import(db, content, name="again.csv")

    async def test_a_re_exported_statement_is_a_duplicate_not_a_second_payment(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        original = await _import(db, statement(f"{parcel['reference']},1405.00"))
        await _reconcile(db, parcel, original.id)

        # Same row, different bytes around it: the file hash cannot see it.
        re_export = await _import(
            db, f"Invoice,Amount,Note\n{parcel['reference']},1405.00,re-sent\n".encode()
        )
        engine, report = await _reconcile(db, parcel, re_export.id)

        assert report.duplicates == 1
        assert report.applied_paisa == 0
        line = (await PayoutService(db).lines(re_export.id))[0]
        assert line.line_status is PayoutLineStatus.DUPLICATE
        cases = await engine.list_cases(kind=CaseKind.DUPLICATE_PAYOUT_LINE)
        assert len(cases) == 1
        assert cases[0].detail["duplicate_of_payout_id"] == str(original.id)
        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 140_500
        summary = await engine.summary()
        assert summary.duplicate_paisa == 140_500
        assert summary.actual_paisa == 140_500

    async def test_a_split_payment_completes_and_closes_its_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        first = await _import(db, statement(f"{parcel['reference']},700.00"))
        engine, _ = await _reconcile(db, parcel, first.id)
        assert (await _item_for(engine, parcel)).item_status is ItemStatus.AMOUNT_MISMATCH

        second = await _import(db, statement(f"{parcel['reference']},705.00"), name="b.csv")
        await _reconcile(db, parcel, second.id)

        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.MATCHED
        assert item.line_count == 2
        underpaid = (await engine.list_cases(kind=CaseKind.UNDERPAID))[0]
        assert underpaid.case_status is CaseStatus.RESOLVED
        assert underpaid.resolved_by is None
        events = await engine.case_events(underpaid.id)
        assert events[-1].action == str(CaseEventAction.AUTO_RESOLVED)


class TestManualWork:
    async def test_manual_match_resolves_the_unmatched_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        # Two parcels of the same value tie on amount alone: a person decides.
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(db, statement("NO-SUCH-REF,1405.00"))
        engine, _ = await _reconcile(db, parcel, payout.id)
        [case] = await engine.list_cases(kind=CaseKind.UNMAPPABLE_PAYOUT)
        line = (await PayoutService(db).lines(payout.id))[0]

        await engine.match_manually(
            line.id, parcel["receivable"].id, reason="Courier typo, confirmed on call"
        )
        await db.commit()

        item = await _item_for(engine, parcel)
        assert item.item_status is ItemStatus.MATCHED
        # The unplaced-row item is gone; the parcel item replaced it.
        assert await engine.list_items(statuses=[ItemStatus.UNMATCHED]) == []
        refreshed = await engine.get_case(case.id)
        assert refreshed.case_status is CaseStatus.RESOLVED
        events = await engine.case_events(case.id)
        assert [event.action for event in events] == [
            str(CaseEventAction.OPENED),
            str(CaseEventAction.MANUAL_MATCH),
        ]

    async def test_a_duplicate_row_cannot_be_matched_by_hand(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(
            db, statement(f"{parcel['reference']},1405.00", f"{parcel['reference']},1405.00")
        )
        engine, _ = await _reconcile(db, parcel, payout.id)
        duplicate = (await PayoutService(db).lines(payout.id))[1]
        assert duplicate.line_status is PayoutLineStatus.DUPLICATE
        with pytest.raises(ConflictError):
            await engine.match_manually(
                duplicate.id, parcel["receivable"].id, reason="Trying anyway"
            )

    async def test_unmatching_returns_the_row_to_the_queue(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(db, statement(f"{parcel['reference']},1405.00"))
        engine, _ = await _reconcile(db, parcel, payout.id)
        line = (await PayoutService(db).lines(payout.id))[0]

        await engine.unmatch(line.id, reason="Wrong parcel")
        await db.commit()

        items = await engine.list_items(limit=100)
        assert [item.item_status for item in items] == [ItemStatus.UNMATCHED]
        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 0

    async def test_case_notes_resolution_and_reopening_are_events(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(db, statement(f"{parcel['reference']},1000.00"))
        engine, _ = await _reconcile(db, parcel, payout.id)
        [case] = await engine.list_cases(kind=CaseKind.UNDERPAID)
        ledger_before = await parcel["ledger"].balances()

        await engine.add_note(case.id, note="Called the hub, they will check")
        with pytest.raises(ValidationError):
            await engine.update_case(case.id, status=CaseStatus.RESOLVED)
        await engine.update_case(case.id, status=CaseStatus.RESOLVED, resolution="Refund promised")
        reopened = await engine.update_case(case.id, status=CaseStatus.OPEN)
        await db.commit()

        assert reopened.case_status is CaseStatus.OPEN
        assert reopened.resolved_at is None
        actions = [event.action for event in await engine.case_events(case.id)]
        assert actions == ["OPENED", "NOTE", "STATUS_CHANGED", "REOPENED"]
        # A decision about money is recorded; money itself is not touched.
        ledger_after = await parcel["ledger"].balances()
        assert {k: v.net_paisa for k, v in ledger_after.items()} == {
            k: v.net_paisa for k, v in ledger_before.items()
        }


class TestApi:
    async def _seed(self, client: AsyncClient, shop: dict, db: AsyncSession) -> dict:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payout = await _import(db, statement(f"{parcel['reference']},1000.00"))
        await _reconcile(db, parcel, payout.id)
        return {"parcel": parcel, "payout": payout}

    async def test_items_summary_and_case_detail(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await self._seed(client, shop, db)

        summary = await client.get("/v1/reconciliation/summary", headers=auth_header(shop))
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["discrepancies"] == 1
        assert body["difference_paisa"] == -40_500

        listing = await client.get(
            "/v1/reconciliation/items",
            params={"discrepancies_only": "true", "limit": 10},
            headers=auth_header(shop),
        )
        assert listing.status_code == 200, listing.text
        [row] = listing.json()["items"]
        assert row["status"] == "AMOUNT_MISMATCH"
        assert row["case_status"] == "OPEN"

        searched = await client.get(
            "/v1/reconciliation/items",
            params={"q": "zz-nothing"},
            headers=auth_header(shop),
        )
        assert searched.json()["items"] == []

        detail = await client.get(
            f"/v1/reconciliation/items/{row['id']}", headers=auth_header(shop)
        )
        assert detail.status_code == 200
        assert len(detail.json()["lines"]) == 1

        case = await client.get(
            f"/v1/reconciliation/cases/{row['case_id']}", headers=auth_header(shop)
        )
        assert case.status_code == 200
        assert case.json()["events"][0]["action"] == "OPENED"
        assert case.json()["item"]["id"] == row["id"]

        note = await client.post(
            f"/v1/reconciliation/cases/{row['case_id']}/notes",
            json={"note": "Checking with courier"},
            headers=auth_header(shop),
        )
        assert note.status_code == 200, note.text

    async def test_another_shop_sees_none_of_it(
        self, client: AsyncClient, shop: dict, db: AsyncSession, unique_phone: str
    ) -> None:
        seeded = await self._seed(client, shop, db)
        mine = await client.get("/v1/reconciliation/items", headers=auth_header(shop))
        item_id = mine.json()["items"][0]["id"]

        other = await signed_in_shop(
            client, f"018{uuid.uuid4().int % 100_000_000:08d}", shop_name="Other", plan="pro"
        )
        listing = await client.get("/v1/reconciliation/items", headers=auth_header(other))
        assert listing.json()["items"] == []
        summary = await client.get("/v1/reconciliation/summary", headers=auth_header(other))
        assert summary.json()["expected_paisa"] == 0
        assert summary.json()["open_cases"] == 0
        detail = await client.get(f"/v1/reconciliation/items/{item_id}", headers=auth_header(other))
        assert detail.status_code == 404
        accept = await client.post(
            f"/v1/reconciliation/items/{item_id}/accept-charges",
            json={},
            headers=auth_header(other),
        )
        assert accept.status_code == 404
        reconcile = await client.post(
            f"/v1/reconciliation/payouts/{seeded['payout'].id}/reconcile",
            headers=auth_header(other),
        )
        assert reconcile.status_code == 404

    async def test_only_finance_and_owner_may_act(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await self._seed(client, shop, db)
        item_id = (await client.get("/v1/reconciliation/items", headers=auth_header(shop))).json()[
            "items"
        ][0]["id"]
        case_id = (await client.get("/v1/reconciliation/cases", headers=auth_header(shop))).json()[
            "items"
        ][0]["id"]

        manager = await _member_session(client, shop, "01744000101", TenantRole.MANAGER)
        finance = await _member_session(client, shop, "01744000102", TenantRole.FINANCE)
        viewer = await _member_session(client, shop, "01744000103", TenantRole.VIEWER)

        # Reading is money.view: the manager may look, the viewer may not.
        assert (
            await client.get("/v1/reconciliation/summary", headers=auth_header(manager))
        ).status_code == 200
        assert (
            await client.get("/v1/reconciliation/items", headers=auth_header(viewer))
        ).status_code == 403

        for session, allowed in ((manager, False), (viewer, False), (finance, True)):
            accept = await client.post(
                f"/v1/reconciliation/items/{item_id}/accept-charges",
                json={},
                headers=auth_header(session),
            )
            note = await client.post(
                f"/v1/reconciliation/cases/{case_id}/notes",
                json={"note": "looked"},
                headers=auth_header(session),
            )
            expected = 200 if allowed else 403
            assert accept.status_code == expected, accept.text
            assert note.status_code == expected, note.text

    async def test_statement_mapping_is_previewed_and_validated(
        self, client: AsyncClient, shop: dict
    ) -> None:
        content = b"Ref,Paid,Kept\nCP-1,1325.00,80\n"
        detected = await client.post(
            "/v1/payouts/preview",
            files={"file": ("s.csv", content, "text/csv")},
            headers=auth_header(shop),
        )
        assert detected.status_code == 200, detected.text
        assert "amount" in detected.json()["fields"]

        mapped = await client.post(
            "/v1/payouts/preview",
            files={"file": ("s.csv", content, "text/csv")},
            data={
                "mapping": json.dumps(
                    {"merchant_reference": "Ref", "amount": "Paid", "fee": "Kept"}
                )
            },
            headers=auth_header(shop),
        )
        assert mapped.status_code == 200, mapped.text
        row = mapped.json()["rows"][0]
        assert row["merchant_reference"] == "CP-1"
        assert row["amount_paisa"] == 132_500
        assert row["fee_paisa"] == 8_000

        wrong = await client.post(
            "/v1/payouts/preview",
            files={"file": ("s.csv", content, "text/csv")},
            data={"mapping": json.dumps({"amount": "Nope"})},
            headers=auth_header(shop),
        )
        assert wrong.status_code == 422, wrong.text

    async def test_an_xlsx_statement_is_read(self, client: AsyncClient, shop: dict) -> None:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Invoice", "Amount"])
        sheet.append(["CP-9", "1405.00"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        response = await client.post(
            "/v1/payouts/preview",
            files={
                "file": (
                    "s.xlsx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=auth_header(shop),
        )
        assert response.status_code == 200, response.text
        assert response.json()["rows"][0]["amount_paisa"] == 140_500


def test_omission_window_is_bounded() -> None:
    from app.reconciliation import service

    assert service.OMISSION_GRACE_DAYS < service.OMISSION_LOOKBACK_DAYS
    assert timedelta(days=service.OMISSION_LOOKBACK_DAYS) <= timedelta(days=31)
