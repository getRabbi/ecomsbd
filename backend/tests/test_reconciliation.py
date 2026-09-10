"""The reconciliation engine.

Master spec sections 16, 81, 82 and 112. The governing rule is section 112's:
**precision before recall.** Most of these tests are therefore about the engine
*refusing* to match — a missed auto-match costs a click, and a wrong settlement
costs the seller their belief that the numbers are true.

Section 54's golden financial scenarios have their own class at the bottom.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import dispatched_parcel, signed_in_shop

from app.consignments.models import ConsignmentStatus
from app.consignments.service import DeliveryOutcome, ItemOutcome
from app.core.clock import utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.ledger.models import LedgerBucket
from app.money.models import ReceivableStatus
from app.payouts.models import MatchConfidence, PayoutLineStatus
from app.payouts.service import PayoutService
from app.reconciliation.models import CaseKind, CaseStatus
from app.reconciliation.scoring import (
    SCORE_WEIGHTS,
    Candidate,
    ScoringConfig,
    decide,
)
from app.reconciliation.service import ReconciliationService


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Recon Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


async def delivered_parcel(
    client: AsyncClient,
    shop: dict,
    db: AsyncSession,
    *,
    cod_paisa: int = 140_500,
    tracking: str | None = None,
    delivered_days_ago: int = 0,
) -> dict:
    """A parcel that reached the customer, with money owed on it."""
    parcel = await dispatched_parcel(client, shop, db, cod_paisa=cod_paisa)
    if tracking is not None:
        parcel["consignment"].tracking_code = tracking
        await db.flush()
    await parcel["consignments"].record_outcome(
        parcel["consignment"].id,
        DeliveryOutcome(
            status=ConsignmentStatus.DELIVERED,
            occurred_at=utc_now() - timedelta(days=delivered_days_ago),
        ),
    )
    await db.commit()
    parcel["receivable"] = await parcel["receivables"].for_consignment(parcel["consignment"].id)
    parcel["reference"] = parcel["consignment"].merchant_reference
    return parcel


def statement(*rows: str, header: str = "Invoice,Amount") -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode()


class TestScoringWeights:
    def test_the_weights_are_the_ones_the_spec_lists(self) -> None:
        # Section 82's table, verbatim. A change to any of these is a change to
        # how money is matched, so it should be a visible diff here.
        assert SCORE_WEIGHTS["exact_consignment_id"] == 100
        assert SCORE_WEIGHTS["exact_tracking_code"] == 100
        assert SCORE_WEIGHTS["exact_merchant_reference"] == 100
        assert SCORE_WEIGHTS["exact_amount"] == 35
        assert SCORE_WEIGHTS["amount_within_tolerance"] == 20
        assert SCORE_WEIGHTS["delivery_date_in_window"] == 15
        assert SCORE_WEIGHTS["same_phone"] == 10
        assert SCORE_WEIGHTS["order_number_fragment"] == 20

    def test_no_combination_of_soft_signals_reaches_the_threshold(self) -> None:
        # Section 82: "amount-only match → never auto-match". Enforced by
        # arithmetic rather than by a special case — every non-reference signal
        # added together still falls short of one exact reference.
        config = ScoringConfig()
        soft = (
            SCORE_WEIGHTS["exact_amount"]
            + SCORE_WEIGHTS["delivery_date_in_window"]
            + SCORE_WEIGHTS["same_phone"]
            + SCORE_WEIGHTS["order_number_fragment"]
        )
        assert soft < config.auto_match_threshold

    def test_tolerance_scales_with_the_amount(self) -> None:
        config = ScoringConfig()
        # A ৳10 parcel gets the ৳1 floor; a ৳10,000 one gets 1%.
        assert config.tolerance_for(1_000) == 100
        assert config.tolerance_for(1_000_000) == 10_000


class TestDecisionRules:
    def _candidate(self, score: int, signals: list[str], **kwargs) -> Candidate:
        return Candidate(
            receivable_id=uuid.uuid4(),
            consignment_id=uuid.uuid4(),
            merchant_reference=kwargs.get("reference", "CP-1"),
            outstanding_paisa=140_500,
            score=score,
            signals=signals,
            rejected_because=kwargs.get("rejected"),
        )

    def test_one_exact_reference_matches_automatically(self) -> None:
        decision = decide([self._candidate(100, ["exact_consignment_id"])], config=ScoringConfig())
        assert decision.confidence is MatchConfidence.EXACT
        assert decision.is_automatic

    def test_two_parcels_sharing_a_reference_go_to_a_human(self) -> None:
        # A data problem, not a matching problem. Picking one would bury it.
        decision = decide(
            [
                self._candidate(100, ["exact_merchant_reference"]),
                self._candidate(100, ["exact_merchant_reference"]),
            ],
            config=ScoringConfig(),
        )
        assert decision.confidence is MatchConfidence.MANUAL_REQUIRED
        assert decision.chosen is None
        assert "share that reference" in decision.reason

    def test_an_amount_only_candidate_is_a_suggestion_not_a_match(self) -> None:
        decision = decide([self._candidate(35, ["exact_amount"])], config=ScoringConfig())
        assert not decision.is_automatic
        assert decision.chosen is not None

    def test_two_close_candidates_are_a_tie(self) -> None:
        # Section 82: "tie/near-tie → manual review".
        decision = decide(
            [
                self._candidate(55, ["exact_amount", "delivery_date_in_window"]),
                self._candidate(45, ["exact_amount", "same_phone"]),
            ],
            config=ScoringConfig(),
        )
        assert decision.confidence is MatchConfidence.MANUAL_REQUIRED
        assert "equally well" in decision.reason

    def test_a_rejected_candidate_is_kept_but_not_chosen(self) -> None:
        decision = decide(
            [self._candidate(100, ["exact_consignment_id"], rejected="Already settled")],
            config=ScoringConfig(),
        )
        assert decision.chosen is None
        # Kept in `considered` so the seller can see it was looked at.
        assert len(decision.considered) == 1

    def test_nothing_at_all_is_manual_required(self) -> None:
        decision = decide([], config=ScoringConfig())
        assert decision.confidence is MatchConfidence.MANUAL_REQUIRED
        assert decision.chosen is None


class TestReconcile:
    async def test_an_exact_reference_settles_the_parcel(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        report = await engine.reconcile(payout.id)
        await db.commit()

        assert report.exact_matches == 1
        assert report.applied_paisa == 140_500

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.SETTLED
        assert receivable.outstanding_paisa == 0

        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 140_500
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0

    async def test_shadow_mode_changes_nothing(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        report = await engine.reconcile(payout.id, shadow=True)
        await db.commit()

        # Section 112: run it, see what it would do, change nothing.
        assert report.shadow
        assert report.exact_matches == 1
        assert report.applied_paisa == 0

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.ELIGIBLE
        assert receivable.outstanding_paisa == 140_500
        assert (await payouts.lines(payout.id))[0].line_status is (PayoutLineStatus.UNMATCHED)
        assert await engine.list_cases() == []

    async def test_an_amount_only_line_is_suggested_never_applied(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement("1405.00", header="Amount"), provider="manual", filename="s.csv"
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        report = await engine.reconcile(payout.id)
        await db.commit()

        assert report.exact_matches == 0
        assert report.suggested == 1
        assert report.applied_paisa == 0

        line = (await payouts.lines(payout.id))[0]
        assert line.line_status is PayoutLineStatus.SUGGESTED
        # The candidate is kept so the seller can accept it with one tap.
        assert line.candidates

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.outstanding_paisa == 140_500

    async def test_a_settled_parcel_is_not_matched_twice(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        engine = ReconciliationService(db, receivables=parcel["receivables"])

        first = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="a.csv",
        )
        await db.commit()
        await engine.reconcile(first.id)
        await db.commit()

        # A second, genuinely different statement that repeats the parcel —
        # the cross-file duplicate a courier really does send.
        second = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00", "OTHER-REF,100.00"),
            provider="manual",
            filename="b.csv",
        )
        await db.commit()
        report = await engine.reconcile(second.id)
        await db.commit()

        # Section 82 rejects an already-settled candidate outright. Paying the
        # same parcel twice would show the seller money that does not exist.
        assert report.applied_paisa == 0
        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.settled_paisa == 140_500

    async def test_a_returned_parcel_is_never_matched(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.RETURNED),
        )
        await db.commit()

        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['consignment'].merchant_reference},1405.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        report = await engine.reconcile(payout.id)
        await db.commit()

        # Money for a parcel that came back is a question for the provider, not
        # a settlement.
        assert report.applied_paisa == 0

    async def test_lines_are_independent_of_each_other(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        second = await delivered_parcel(client, shop, db, cod_paisa=250_000)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(
                f"{first['reference']},1405.00",
                "UNKNOWN-REF,900.00",
                f"{second['reference']},2500.00",
            ),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=first["receivables"])
        report = await engine.reconcile(payout.id)
        await db.commit()

        # Section 81.3: an unmatched line does not block the others.
        assert report.exact_matches == 2
        assert report.unresolved == 1
        assert report.applied_paisa == 390_500


class TestManualMatching:
    async def test_a_manual_match_records_who_and_why(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement("1405.00", header="Amount"), provider="manual", filename="s.csv"
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        line = (await payouts.lines(payout.id))[0]
        matched = await engine.match_manually(
            line.id,
            parcel["receivable"].id,
            reason="Courier confirmed this is for CP-0001 by phone",
        )
        await db.commit()

        # Section 81.7.
        assert matched.line_status is PayoutLineStatus.MANUAL_MATCHED
        assert matched.match_reason.startswith("Courier confirmed")
        assert matched.matched_at is not None

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.SETTLED

    async def test_a_manual_match_without_a_reason_is_refused(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement("1405.00", header="Amount"), provider="manual", filename="s.csv"
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        line = (await payouts.lines(payout.id))[0]
        with pytest.raises(ValidationError):
            await engine.match_manually(line.id, parcel["receivable"].id, reason="  ")

    async def test_unmatching_reverses_rather_than_deletes(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        line = (await payouts.lines(payout.id))[0]
        reversed_line = await engine.unmatch(line.id, reason="Wrong parcel")
        await db.commit()

        # Section 81.8.
        assert reversed_line.line_status is PayoutLineStatus.REVERSED
        assert reversed_line.applied_paisa == 0

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.ELIGIBLE
        assert receivable.outstanding_paisa == 140_500

        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 0
        # The entries that recorded the settlement are still there; two more
        # undo them.
        entries = await parcel["ledger"].entries_for("cod_receivable", parcel["receivable"].id)
        assert len(entries) == 4

    async def test_an_unmatched_line_cannot_be_unmatched(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement("1405.00", header="Amount"), provider="manual", filename="s.csv"
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        line = (await payouts.lines(payout.id))[0]
        with pytest.raises(ConflictError):
            await engine.unmatch(line.id, reason="nothing to undo")


class TestCases:
    async def test_an_underpayment_opens_a_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1300.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        cases = await engine.list_cases(kind=CaseKind.UNDERPAID)
        assert len(cases) == 1
        assert cases[0].amount_paisa == 10_500
        assert "short" in cases[0].summary

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.MISMATCHED
        # The money that did arrive is recorded; only the gap is a case.
        assert receivable.settled_paisa == 130_000

    async def test_a_small_difference_is_inside_tolerance(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        # ৳1 short — providers round, and a case for every parcel is noise.
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1404.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        assert await engine.list_cases(kind=CaseKind.UNDERPAID) == []

    async def test_an_unmappable_line_opens_a_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        await delivered_parcel(client, shop, db)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement("SOMEONE-ELSES-PARCEL,999.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db)
        await engine.reconcile(payout.id)
        await db.commit()

        cases = await engine.list_cases(kind=CaseKind.UNMAPPABLE_PAYOUT)
        assert len(cases) == 1

    async def test_an_unknown_deduction_opens_a_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            (
                "Invoice,Amount,Charge,Charge Type\n"
                f"{parcel['reference']},1405.00,120,Adj ref 9931\n"
            ).encode(),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        cases = await engine.list_cases(kind=CaseKind.UNKNOWN_DEDUCTION)
        assert len(cases) == 1
        # Section 84: it stays visible after the import screen closes.
        assert cases[0].detail["raw_text"] == "Adj ref 9931"

    async def test_a_duplicate_line_opens_a_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00", f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="stmt.csv",
        )
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        report = await engine.reconcile(payout.id)
        await db.commit()

        assert len(await engine.list_cases(kind=CaseKind.DUPLICATE_PAYOUT_LINE)) == 1
        # The second line never settles anything.
        assert report.applied_paisa == 140_500

    async def test_delivered_but_unpaid_is_found_by_the_scan(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500, delivered_days_ago=12)
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        opened = await engine.scan_for_cases()
        await db.commit()

        assert opened >= 1
        cases = await engine.list_cases(kind=CaseKind.DELIVERED_BUT_UNPAID)
        assert len(cases) == 1
        assert cases[0].amount_paisa == 140_500
        # The most valuable thing this product finds, stated plainly.
        assert "has not arrived" in cases[0].summary

    async def test_a_recent_delivery_is_not_a_case_yet(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500, delivered_days_ago=2)
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.scan_for_cases()
        await db.commit()

        assert await engine.list_cases(kind=CaseKind.DELIVERED_BUT_UNPAID) == []

    async def test_a_stale_parcel_is_found(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        parcel["consignment"].booked_at = utc_now() - timedelta(days=20)
        await db.commit()

        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.scan_for_cases()
        await db.commit()

        cases = await engine.list_cases(kind=CaseKind.STALE_IN_TRANSIT)
        assert len(cases) == 1
        assert "in transit for 20 days" in cases[0].summary

    async def test_scanning_twice_does_not_duplicate_a_case(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500, delivered_days_ago=12)
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.scan_for_cases()
        await db.commit()
        second = await engine.scan_for_cases()
        await db.commit()

        # A list that doubles on every scan is one the seller stops reading.
        assert second == 0
        assert len(await engine.list_cases(kind=CaseKind.DELIVERED_BUT_UNPAID)) == 1

    async def test_closing_a_case_needs_a_note(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500, delivered_days_ago=12)
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.scan_for_cases()
        await db.commit()
        case = (await engine.list_cases())[0]

        with pytest.raises(ValidationError):
            await engine.update_case(case.id, status=CaseStatus.RESOLVED)

        resolved = await engine.update_case(
            case.id,
            status=CaseStatus.RESOLVED,
            resolution="Courier paid it on the 12th",
        )
        await db.commit()
        assert resolved.case_status is CaseStatus.RESOLVED
        assert resolved.resolved_at is not None


class TestGoldenScenarios:
    """Master spec section 54's golden financial scenarios.

    *"A release must not change expected totals without explicit
    migration/versioning."* Each scenario asserts the exact figures a seller
    would see, so a change to any money rule shows up here as a failing number
    rather than as a quiet drift.
    """

    async def test_1_normal_delivered_cod(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        await ReconciliationService(db, receivables=parcel["receivables"]).reconcile(payout.id)
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.settled_paisa == 140_500
        assert receivable.outstanding_paisa == 0
        assert await parcel["receivables"].outstanding_total() == 0

    async def test_2_delivery_with_a_cod_fee(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            (
                f"Invoice,Amount,Charge,Charge Type\n{parcel['reference']},1325.00,80,COD Fee\n"
            ).encode(),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        await ReconciliationService(db, receivables=parcel["receivables"]).reconcile(payout.id)
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        # ৳1,325 arrived against ৳1,405 owed. The ৳80 gap is the fee, and it is
        # a case rather than a silent write-off — the seller decides whether
        # that charge was right.
        assert receivable.settled_paisa == 132_500
        assert receivable.outstanding_paisa == 8_000
        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 132_500

    async def test_3_return(self, client: AsyncClient, shop: dict, db: AsyncSession) -> None:
        parcel = await dispatched_parcel(client, shop, db, cod_paisa=140_500)
        await parcel["consignments"].record_outcome(
            parcel["consignment"].id,
            DeliveryOutcome(status=ConsignmentStatus.RETURNED),
        )
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.receivable_status is ReceivableStatus.NOT_DUE
        assert receivable.outstanding_paisa == 0
        assert await parcel["receivables"].outstanding_total() == 0

    async def test_4_partial_delivery(
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

        receivable = await parcel["receivables"].for_consignment(consignment.id)
        # Two of three units. Never the original ৳3,000 (section 17.8).
        assert receivable.collectible_paisa == 200_000
        assert await parcel["receivables"].outstanding_total() == 200_000

    async def test_5_underpayment(self, client: AsyncClient, shop: dict, db: AsyncSession) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1000.00"),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        assert receivable.settled_paisa == 100_000
        assert receivable.outstanding_paisa == 40_500
        assert receivable.receivable_status is ReceivableStatus.MISMATCHED
        assert len(await engine.list_cases(kind=CaseKind.UNDERPAID)) == 1

    async def test_6_overpayment(self, client: AsyncClient, shop: dict, db: AsyncSession) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1600.00"),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        # Only what was owed is settled. The excess is somebody else's money
        # until a person says otherwise.
        assert receivable.settled_paisa == 140_500
        assert receivable.outstanding_paisa == 0
        cases = await engine.list_cases(kind=CaseKind.OVERPAID)
        assert len(cases) == 1
        assert cases[0].amount_paisa == 19_500

        line = (await payouts.lines(payout.id))[0]
        assert line.applied_paisa == 140_500
        assert line.unapplied_paisa == 19_500

    async def test_7_split_payout(self, client: AsyncClient, shop: dict, db: AsyncSession) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        engine = ReconciliationService(db, receivables=parcel["receivables"])

        first = await payouts.import_statement(
            statement(f"{parcel['reference']},1000.00"),
            provider="manual",
            filename="a.csv",
        )
        await db.commit()
        await engine.reconcile(first.id)
        await db.commit()

        second = await payouts.import_statement(
            statement(f"{parcel['reference']},405.00"),
            provider="manual",
            filename="b.csv",
        )
        await db.commit()
        await engine.reconcile(second.id)
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        # Section 17.2 allows many settlement lines per parcel.
        assert receivable.settled_paisa == 140_500
        assert receivable.receivable_status is ReceivableStatus.SETTLED

    async def test_8_one_payout_covering_many_parcels(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        second = await delivered_parcel(client, shop, db, cod_paisa=250_000)
        third = await delivered_parcel(client, shop, db, cod_paisa=90_000)

        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(
                f"{first['reference']},1405.00",
                f"{second['reference']},2500.00",
                f"{third['reference']},900.00",
            ),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        report = await ReconciliationService(db, receivables=first["receivables"]).reconcile(
            payout.id
        )
        await db.commit()

        assert report.exact_matches == 3
        assert report.applied_paisa == 480_500
        assert await first["receivables"].outstanding_total() == 0

        refreshed = await payouts.get(payout.id)
        assert refreshed.applied_paisa == 480_500
        assert refreshed.unexplained_paisa == 0

    async def test_9_duplicate_payout_line(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        parcel = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{parcel['reference']},1405.00", f"{parcel['reference']},1405.00"),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        engine = ReconciliationService(db, receivables=parcel["receivables"])
        await engine.reconcile(payout.id)
        await db.commit()

        receivable = await parcel["receivables"].for_consignment(parcel["consignment"].id)
        # The parcel is paid once, and the second line is a case.
        assert receivable.settled_paisa == 140_500
        assert len(await engine.list_cases(kind=CaseKind.DUPLICATE_PAYOUT_LINE)) == 1

        balances = await parcel["ledger"].balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 140_500

    async def test_10_the_ledger_reconciles_to_the_dashboard(
        self, client: AsyncClient, shop: dict, db: AsyncSession
    ) -> None:
        first = await delivered_parcel(client, shop, db, cod_paisa=140_500)
        # A second delivered parcel, deliberately left unpaid.
        await delivered_parcel(client, shop, db, cod_paisa=250_000)
        payouts = PayoutService(db)
        payout = await payouts.import_statement(
            statement(f"{first['reference']},1405.00"),
            provider="manual",
            filename="s.csv",
        )
        await db.commit()
        await ReconciliationService(db, receivables=first["receivables"]).reconcile(payout.id)
        await db.commit()

        outstanding = await first["receivables"].outstanding_total()
        balances = await first["ledger"].balances()

        # Section 81.10: the dashboard total must reconcile to the ledger. The
        # receivable bucket's net is what is still owed, and it is derived from
        # a different place than `outstanding_total`, so agreement is evidence.
        assert outstanding == 250_000
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == outstanding
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 140_500
