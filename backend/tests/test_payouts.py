"""Payouts and their source evidence.

Master spec sections 16, 81, 83 and 84. Two properties matter most here:
re-importing the same statement must not double a seller's money (81.2), and
the source file must survive so a reconciliation result can be explained later
(81.4, 83).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import signed_in_shop

from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.payouts.models import (
    AdjustmentType,
    Payout,
    PayoutLineStatus,
    PayoutSource,
    PayoutStatus,
)
from app.payouts.service import PayoutService
from app.payouts.statements import (
    autodetect_columns,
    classify_adjustment,
    parse_statement,
)

STATEMENT = b"""Consignment ID,Invoice,Collected Amount,Delivery Date,Charge,Charge Type
CN-1001,CP-20260901-0001,1405.00,2026-09-05,80,Delivery charge
CN-1002,CP-20260901-0002,2500,2026-09-05,80,Delivery charge
CN-1003,CP-20260901-0003,900,2026-09-06,80,Delivery charge
"""


@pytest.fixture
async def shop(client: AsyncClient, unique_phone: str) -> dict:
    session = await signed_in_shop(client, unique_phone, shop_name="Payout Shop")
    set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(session["tenant_id"])))
    return session


@pytest.fixture
def payouts(db: AsyncSession) -> PayoutService:
    return PayoutService(db)


class TestStatementParsing:
    def test_columns_are_detected_by_name(self) -> None:
        parsed = parse_statement(STATEMENT)
        assert parsed.mapping["consignment_id"] == "Consignment ID"
        assert parsed.mapping["merchant_reference"] == "Invoice"
        assert parsed.mapping["amount"] == "Collected Amount"
        assert parsed.mapping["delivered_on"] == "Delivery Date"

    def test_an_exact_column_name_wins_over_a_substring(self) -> None:
        # "Amount" should claim `amount`, not be stolen by "COD Amount".
        mapping = autodetect_columns(["COD Amount", "Amount", "Tracking"])
        assert mapping["amount"] == "Amount"

    def test_amounts_become_integer_paisa(self) -> None:
        parsed = parse_statement(STATEMENT)
        assert [row.amount_paisa for row in parsed.rows] == [140_500, 250_000, 90_000]
        assert parsed.total_paisa == 480_500

    def test_bangla_numerals_and_currency_marks_are_handled(self) -> None:
        # Quoted, because an unquoted thousands separator is a field
        # separator to any CSV reader.
        content = 'Invoice,Amount\nCP-1,"৳১,৪০৫.৫০"\n'.encode()
        parsed = parse_statement(content)
        assert parsed.rows[0].amount_paisa == 140_550

    def test_an_unreadable_amount_is_reported_not_coerced(self) -> None:
        content = b"Invoice,Amount\nCP-1,pending\n"
        parsed = parse_statement(content)
        row = parsed.rows[0]
        # Section 98: never silently coerce. A row imported as zero is a
        # settlement that silently never happened.
        assert not row.is_valid
        assert row.amount_paisa is None
        assert "pending" in row.errors[0]

    def test_a_negative_line_is_a_deduction_not_a_payment(self) -> None:
        content = b"Invoice,Amount\nCP-1,(150)\n"
        parsed = parse_statement(content)
        row = parsed.rows[0]
        assert row.amount_paisa == 0
        assert row.fee_paisa == 15_000

    def test_a_row_with_only_an_amount_has_no_reference(self) -> None:
        content = b"Amount\n1405\n"
        parsed = parse_statement(content)
        # Section 82: amount-only can never auto-match. The row is still
        # imported, because the money arrived.
        assert not parsed.rows[0].has_reference
        assert parsed.rows[0].amount_paisa == 140_500

    def test_a_semicolon_statement_is_read_too(self) -> None:
        content = b"Invoice;Amount\nCP-1;1405\n"
        parsed = parse_statement(content)
        assert parsed.rows[0].amount_paisa == 140_500

    def test_dates_in_several_formats_are_understood(self) -> None:
        content = b"Invoice,Amount,Delivery Date\nCP-1,100,05/09/2026\n"
        parsed = parse_statement(content)
        assert parsed.rows[0].delivered_on == date(2026, 9, 5)

    def test_an_unreadable_date_is_left_unset_rather_than_guessed(self) -> None:
        content = b"Invoice,Amount,Delivery Date\nCP-1,100,last tuesday\n"
        parsed = parse_statement(content)
        # The delivery date is a scoring input; a wrong one moves a match.
        assert parsed.rows[0].delivered_on is None


class TestAdjustmentClassification:
    def test_known_labels_are_recognised(self) -> None:
        assert classify_adjustment("Delivery charge")[0] is AdjustmentType.DELIVERY_FEE
        assert classify_adjustment("COD Fee")[0] is AdjustmentType.COD_FEE
        assert classify_adjustment("Return charge")[0] is AdjustmentType.RETURN_FEE
        assert classify_adjustment("VAT 5%")[0] is AdjustmentType.TAX

    def test_an_unknown_label_stays_unknown(self) -> None:
        # Section 84: unknown deductions must remain visible, not silently
        # forced into delivery fee. A deduction filed under a familiar name is
        # one the seller will never question.
        adjustment_type, rule = classify_adjustment("Adj. ref 9931")
        assert adjustment_type is AdjustmentType.UNKNOWN_DEDUCTION
        assert rule is None

    def test_no_label_at_all_is_also_unknown(self) -> None:
        assert classify_adjustment(None)[0] is AdjustmentType.UNKNOWN_DEDUCTION


class TestManualPayout:
    async def test_a_lump_sum_creates_one_unmatched_line(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        payout = await payouts.record_manual(
            provider="manual", total_paisa=480_500, paid_on=date(2026, 9, 7)
        )
        await db.commit()

        assert payout.payout_status is PayoutStatus.RECEIVED
        assert payout.total_paisa == 480_500
        assert payout.unexplained_paisa == 480_500

        lines = await payouts.lines(payout.id)
        assert len(lines) == 1
        # Section 82: a lump total may propose an allocation, never an
        # irreversible settlement. It starts unmatched.
        assert lines[0].line_status is PayoutLineStatus.UNMATCHED
        assert not lines[0].raw.get("consignment_id")

    async def test_a_zero_payout_is_refused(self, payouts: PayoutService, shop: dict) -> None:
        with pytest.raises(ValidationError):
            await payouts.record_manual(provider="manual", total_paisa=0)

    async def test_the_same_provider_reference_cannot_be_recorded_twice(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        await payouts.record_manual(provider="steadfast", total_paisa=100_000, reference="PAY-9931")
        await db.commit()

        # Section 81.1. Two rows for one provider reference would double the
        # seller's settled total.
        with pytest.raises(ConflictError):
            await payouts.record_manual(
                provider="steadfast", total_paisa=100_000, reference="PAY-9931"
            )


class TestStatementImport:
    async def test_a_statement_becomes_a_payout_with_a_line_per_row(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        payout = await payouts.import_statement(
            STATEMENT, provider="steadfast", filename="sept-05.csv"
        )
        await db.commit()

        assert payout.source == str(PayoutSource.STATEMENT)
        assert payout.total_paisa == 480_500

        lines = await payouts.lines(payout.id)
        assert len(lines) == 3
        assert lines[0].provider_consignment_id == "CN-1001"
        assert lines[0].merchant_reference == "CP-20260901-0001"
        assert lines[0].amount_paisa == 140_500
        assert lines[0].delivered_on == date(2026, 9, 5)

    async def test_the_source_file_is_kept_with_its_hash(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        payout = await payouts.import_statement(
            STATEMENT, provider="steadfast", filename="sept-05.csv"
        )
        await db.commit()

        source = await payouts.source_file(payout)
        assert source is not None
        assert source.original_filename == "sept-05.csv"
        assert len(source.sha256) == 64
        # Section 83: this is what lets support explain a reconciliation result
        # months later. R2 is not provisioned, so it lives in the row for now.
        assert source.raw_content is not None
        assert "CN-1001" in source.raw_content
        assert source.storage_key is None

    async def test_re_importing_the_same_file_is_refused(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        await payouts.import_statement(STATEMENT, provider="steadfast", filename="sept-05.csv")
        await db.commit()

        # Section 81.2. The second upload looks exactly as legitimate as the
        # first, which is precisely why the content hash has to catch it.
        with pytest.raises(ConflictError) as caught:
            await payouts.import_statement(
                STATEMENT, provider="steadfast", filename="sept-05-again.csv"
            )
        assert "already been imported" in str(caught.value)

    async def test_a_repeated_reference_is_marked_duplicate(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        content = b"Consignment ID,Amount\nCN-1001,1405\nCN-1001,1405\nCN-1002,900\n"
        payout = await payouts.import_statement(content, provider="steadfast", filename="dupe.csv")
        await db.commit()

        lines = await payouts.lines(payout.id)
        # Section 16 lists "duplicate payout line" as its own reconciliation
        # case; flagging it here means the second one is never applied.
        assert lines[0].line_status is PayoutLineStatus.UNMATCHED
        assert lines[1].line_status is PayoutLineStatus.DUPLICATE
        assert lines[2].line_status is PayoutLineStatus.UNMATCHED

    async def test_an_unreadable_row_is_imported_as_unmappable(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        content = b"Consignment ID,Amount\nCN-1001,1405\nCN-1002,pending\n"
        payout = await payouts.import_statement(
            content, provider="steadfast", filename="partial.csv"
        )
        await db.commit()

        lines = await payouts.lines(payout.id)
        assert lines[1].line_status is PayoutLineStatus.UNMAPPABLE
        # The text the file contained travels with the row, so the seller can
        # see what was actually there rather than a blank.
        assert lines[1].raw["errors"]
        # Section 81.3: one bad line does not block the rest.
        assert lines[0].line_status is PayoutLineStatus.UNMATCHED

    async def test_charges_become_adjustments(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        payout = await payouts.import_statement(
            STATEMENT, provider="steadfast", filename="sept-05.csv"
        )
        await db.commit()

        adjustments = await payouts.adjustments(payout.id)
        assert len(adjustments) == 3
        assert all(
            adjustment.adjustment_type is AdjustmentType.DELIVERY_FEE for adjustment in adjustments
        )
        assert adjustments[0].amount_paisa == 8_000
        assert adjustments[0].provider_label == "Delivery charge"

    async def test_an_unrecognised_deduction_stays_visible(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        content = b"Consignment ID,Amount,Charge,Charge Type\nCN-1001,1405,120,Adj. ref 9931\n"
        payout = await payouts.import_statement(content, provider="steadfast", filename="odd.csv")
        await db.commit()

        adjustment = (await payouts.adjustments(payout.id))[0]
        assert adjustment.adjustment_type is AdjustmentType.UNKNOWN_DEDUCTION
        assert not adjustment.is_understood
        # The provider's own words are kept so the seller can ask about them.
        assert adjustment.raw_text == "Adj. ref 9931"
        assert adjustment.recognized_rule is None

    async def test_an_empty_file_is_refused(self, payouts: PayoutService, shop: dict) -> None:
        with pytest.raises(ValidationError):
            await payouts.import_statement(
                b"Invoice,Amount\n", provider="steadfast", filename="empty.csv"
            )

    async def test_a_preview_saves_nothing(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        parsed = await payouts.preview_statement(STATEMENT)
        await db.commit()

        assert len(parsed.rows) == 3
        rows = (await db.execute(sa.select(Payout))).scalars().all()
        assert rows == []


class TestTotals:
    async def test_the_status_follows_the_lines(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        payout = await payouts.import_statement(
            STATEMENT, provider="steadfast", filename="sept-05.csv"
        )
        await db.commit()

        lines = await payouts.lines(payout.id)
        lines[0].status = str(PayoutLineStatus.MATCHED)
        lines[0].applied_paisa = lines[0].amount_paisa
        await db.flush()

        refreshed = await payouts.refresh_totals(payout.id)
        assert refreshed.payout_status is PayoutStatus.PARTIALLY_RECONCILED
        assert refreshed.applied_paisa == 140_500
        # The gap between what arrived and what has been explained.
        assert refreshed.unexplained_paisa == 340_000

    async def test_every_line_resolved_marks_it_reconciled(
        self, payouts: PayoutService, shop: dict, db: AsyncSession
    ) -> None:
        payout = await payouts.import_statement(
            STATEMENT, provider="steadfast", filename="sept-05.csv"
        )
        await db.commit()

        for line in await payouts.lines(payout.id):
            line.status = str(PayoutLineStatus.MATCHED)
            line.applied_paisa = line.amount_paisa
        await db.flush()

        refreshed = await payouts.refresh_totals(payout.id)
        assert refreshed.payout_status is PayoutStatus.RECONCILED
        assert refreshed.unexplained_paisa == 0


class TestIsolation:
    async def test_another_shops_payouts_are_invisible(
        self, client: AsyncClient, db: AsyncSession, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Shop One")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
        service = PayoutService(db)
        await service.record_manual(provider="manual", total_paisa=480_500)
        await db.commit()

        second = await signed_in_shop(client, "01922222222", shop_name="Shop Two")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(second["tenant_id"])))
        rows = (await db.execute(sa.select(Payout))).scalars().all()
        assert rows == []

    async def test_two_shops_can_import_the_same_statement(
        self, client: AsyncClient, db: AsyncSession, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Shop One")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(first["tenant_id"])))
        service = PayoutService(db)
        await service.import_statement(STATEMENT, provider="steadfast", filename="sept-05.csv")
        await db.commit()

        second = await signed_in_shop(client, "01933333333", shop_name="Shop Two")
        set_context(RequestContext(trace_id="test", tenant_id=uuid.UUID(second["tenant_id"])))
        # The dedupe is per shop. Two sellers using the same courier can
        # legitimately hold files with identical bytes.
        payout = await service.import_statement(
            STATEMENT, provider="steadfast", filename="sept-05.csv"
        )
        await db.commit()
        assert payout.total_paisa == 480_500
