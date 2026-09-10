"""The immutable financial ledger.

Master spec section 80. The tests here defend one property above all: **an
entry is never edited.** A correction is a reversal plus a new row, and the
original stays exactly as it was written. Everything a seller is later shown
about their money is derived from these rows, so a mutable ledger would make
every balance unexplainable.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_date, utc_now
from app.core.context import RequestContext, set_context
from app.core.errors import ConflictError, ValidationError
from app.ledger.models import (
    EVENT_SHAPE,
    LedgerBucket,
    LedgerDirection,
    LedgerEntry,
    LedgerEventType,
    LedgerSource,
)
from app.ledger.service import LedgerService
from app.tenants.models import Tenant


@pytest.fixture
async def tenant_id(system_db: AsyncSession) -> uuid.UUID:
    tenant = Tenant(name="Ledger Shop")
    system_db.add(tenant)
    await system_db.commit()
    set_context(RequestContext(trace_id="test", tenant_id=tenant.id))
    return tenant.id


@pytest.fixture
def ledger(db: AsyncSession) -> LedgerService:
    return LedgerService(db)


class TestShape:
    def test_every_event_declares_a_direction_and_a_bucket(self) -> None:
        # A new event type cannot be added without deciding both, which is what
        # stops a delivery being filed under COD_SETTLED by accident.
        for event in LedgerEventType:
            assert event in EVENT_SHAPE, f"{event} has no declared shape"
            direction, bucket = EVENT_SHAPE[event]
            assert isinstance(direction, LedgerDirection)
            assert isinstance(bucket, LedgerBucket)

    def test_money_arriving_and_money_leaving_are_opposite(self) -> None:
        applied, _ = EVENT_SHAPE[LedgerEventType.PAYOUT_APPLIED]
        reversed_, _ = EVENT_SHAPE[LedgerEventType.PAYOUT_REVERSED]
        assert applied is LedgerDirection.CREDIT
        assert reversed_ is LedgerDirection.DEBIT


class TestRecording:
    async def test_an_entry_stores_its_business_date_in_dhaka_time(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        moment = utc_now()
        entry = await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=uuid.uuid4(),
            amount_paisa=140_500,
            source=LedgerSource.PROVIDER_EVENT,
            occurred_at=moment,
        )
        # Money is reported on the seller's calendar, not on UTC's: a delivery
        # at 01:30 Dhaka belongs to that day (section 69).
        assert entry.business_date == business_date(at=moment)
        assert entry.currency == "BDT"

    async def test_the_default_shape_is_applied(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        entry = await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=uuid.uuid4(),
            amount_paisa=140_500,
            source=LedgerSource.PROVIDER_EVENT,
        )
        assert entry.ledger_bucket is LedgerBucket.COD_RECEIVABLE
        assert entry.ledger_direction is LedgerDirection.CREDIT
        assert entry.signed_paisa == 140_500

    async def test_a_debit_signs_itself_negative(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        entry = await ledger.record(
            event_type=LedgerEventType.RETURN_FEE_APPLIED,
            entity_type="consignment",
            entity_id=uuid.uuid4(),
            amount_paisa=8_000,
            source=LedgerSource.PAYOUT,
        )
        assert entry.amount_paisa == 8_000
        assert entry.signed_paisa == -8_000

    async def test_a_negative_amount_is_refused(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        # The sign lives in `direction`. Allowing a negative amount as well
        # would give every figure two representations, free to disagree.
        with pytest.raises(ValidationError):
            await ledger.record(
                event_type=LedgerEventType.DELIVERY_CONFIRMED,
                entity_type="consignment",
                entity_id=uuid.uuid4(),
                amount_paisa=-100,
                source=LedgerSource.PROVIDER_EVENT,
            )

    async def test_a_zero_amount_is_refused(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        with pytest.raises(ValidationError):
            await ledger.record(
                event_type=LedgerEventType.DELIVERY_CONFIRMED,
                entity_type="consignment",
                entity_id=uuid.uuid4(),
                amount_paisa=0,
                source=LedgerSource.PROVIDER_EVENT,
            )

    async def test_the_actor_is_captured_from_the_request(
        self, ledger: LedgerService, system_db: AsyncSession
    ) -> None:
        tenant = Tenant(name="Actor Shop")
        system_db.add(tenant)
        await system_db.commit()
        actor = uuid.uuid4()
        set_context(RequestContext(trace_id="test", tenant_id=tenant.id, user_id=actor))

        entry = await ledger.record(
            event_type=LedgerEventType.MANUAL_ADJUSTMENT,
            entity_type="cod_receivable",
            entity_id=uuid.uuid4(),
            amount_paisa=5_000,
            source=LedgerSource.SELLER,
            reason="Courier confirmed the shortfall by phone",
        )
        # Section 80: every manual financial correction needs an actor and a
        # reason. Without them a balance change is untraceable.
        assert entry.actor_user_id == actor
        assert entry.reason == "Courier confirmed the shortfall by phone"


class TestReversal:
    async def test_a_reversal_leaves_the_original_untouched(
        self, ledger: LedgerService, db: AsyncSession, tenant_id: uuid.UUID
    ) -> None:
        original = await ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="cod_receivable",
            entity_id=uuid.uuid4(),
            amount_paisa=140_500,
            source=LedgerSource.PAYOUT,
            source_ref="STMT-1",
        )
        original_amount = original.amount_paisa
        original_direction = original.direction

        reversal = await ledger.reverse(
            original.id, reason="Matched the wrong parcel", source=LedgerSource.RECONCILIATION
        )

        await db.refresh(original)
        assert original.amount_paisa == original_amount
        assert original.direction == original_direction
        assert reversal.reversal_of == original.id
        assert reversal.ledger_direction is LedgerDirection.DEBIT
        assert reversal.bucket == original.bucket

    async def test_a_reversal_nets_the_bucket_back_to_zero(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        entry = await ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="cod_receivable",
            entity_id=uuid.uuid4(),
            amount_paisa=140_500,
            source=LedgerSource.PAYOUT,
        )
        await ledger.reverse(entry.id, reason="Wrong parcel", source=LedgerSource.RECONCILIATION)

        balances = await ledger.balances()
        assert balances[LedgerBucket.COD_SETTLED].net_paisa == 0

    async def test_reversing_twice_is_refused(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        entry = await ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="cod_receivable",
            entity_id=uuid.uuid4(),
            amount_paisa=140_500,
            source=LedgerSource.PAYOUT,
        )
        await ledger.reverse(entry.id, reason="First", source=LedgerSource.RECONCILIATION)

        # A second reversal would credit the money back twice.
        with pytest.raises(ConflictError):
            await ledger.reverse(entry.id, reason="Second", source=LedgerSource.RECONCILIATION)

    async def test_there_is_no_update_path(self) -> None:
        # The invariant stated as a test: nothing in the service can change a
        # row that already exists. If an `update` or `delete` ever appears
        # here, this fails and the reviewer has to justify it.
        api = {name for name in dir(LedgerService) if not name.startswith("_")}
        assert api == {"balances", "entries_for", "record", "reverse"}


class TestBalances:
    async def test_buckets_net_credits_against_debits(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        consignment = uuid.uuid4()
        await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=consignment,
            amount_paisa=140_500,
            source=LedgerSource.PROVIDER_EVENT,
        )
        await ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="cod_receivable",
            entity_id=consignment,
            amount_paisa=140_500,
            bucket=LedgerBucket.COD_RECEIVABLE,
            direction=LedgerDirection.DEBIT,
            source=LedgerSource.PAYOUT,
        )

        balances = await ledger.balances()
        # The receivable was created and then cleared: nothing outstanding.
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0
        assert balances[LedgerBucket.COD_RECEIVABLE].credit_paisa == 140_500
        assert balances[LedgerBucket.COD_RECEIVABLE].debit_paisa == 140_500

    async def test_every_bucket_is_present_even_when_empty(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        balances = await ledger.balances()
        assert set(balances) == set(LedgerBucket)
        assert all(balance.net_paisa == 0 for balance in balances.values())

    async def test_a_date_range_excludes_earlier_entries(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        from datetime import timedelta

        old = utc_now() - timedelta(days=40)
        await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=uuid.uuid4(),
            amount_paisa=100_000,
            source=LedgerSource.PROVIDER_EVENT,
            occurred_at=old,
        )
        await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=uuid.uuid4(),
            amount_paisa=40_500,
            source=LedgerSource.PROVIDER_EVENT,
        )

        recent = await ledger.balances(since=business_date(at=utc_now()))
        assert recent[LedgerBucket.COD_RECEIVABLE].net_paisa == 40_500

    async def test_entries_for_one_entity_read_oldest_first(
        self, ledger: LedgerService, tenant_id: uuid.UUID
    ) -> None:
        from datetime import timedelta

        consignment = uuid.uuid4()
        now = utc_now()
        await ledger.record(
            event_type=LedgerEventType.PAYOUT_APPLIED,
            entity_type="consignment",
            entity_id=consignment,
            amount_paisa=140_500,
            source=LedgerSource.PAYOUT,
            occurred_at=now,
        )
        await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=consignment,
            amount_paisa=140_500,
            source=LedgerSource.PROVIDER_EVENT,
            occurred_at=now - timedelta(hours=2),
        )

        # This is the "explain this number" query: the seller sees the delivery
        # before the payment, which is the order they happened in.
        entries = await ledger.entries_for("consignment", consignment)
        assert [entry.event_type for entry in entries] == [
            str(LedgerEventType.DELIVERY_CONFIRMED),
            str(LedgerEventType.PAYOUT_APPLIED),
        ]


class TestIsolation:
    async def test_another_shops_entries_are_invisible(
        self, ledger: LedgerService, db: AsyncSession, system_db: AsyncSession
    ) -> None:
        first = Tenant(name="Shop One")
        second = Tenant(name="Shop Two")
        system_db.add_all([first, second])
        await system_db.commit()

        set_context(RequestContext(trace_id="test", tenant_id=first.id))
        await ledger.record(
            event_type=LedgerEventType.DELIVERY_CONFIRMED,
            entity_type="consignment",
            entity_id=uuid.uuid4(),
            amount_paisa=140_500,
            source=LedgerSource.PROVIDER_EVENT,
        )
        await db.commit()

        set_context(RequestContext(trace_id="test", tenant_id=second.id))
        rows = (await db.execute(sa.select(LedgerEntry))).scalars().all()
        assert rows == []

        balances = await ledger.balances()
        assert balances[LedgerBucket.COD_RECEIVABLE].net_paisa == 0
