"""Courier receivables and cashflow.

Three questions a seller asks about money a courier is holding:

* **Who owes me, and how much?** Courier by courier: outstanding COD, parcels
  delivered but not yet paid at all, what is overdue, and how old the oldest is.
* **What arrived?** Money actually received over a period, from the ledger.
* **What is coming, and when?** An estimate — and only ever labelled one.

The first two are facts. They are read from the receivables and the ledger and
nothing here changes either. The third is the only forecast in the money core,
and it is built so that it cannot invent money: it takes the outstanding COD
that already exists and spreads it across time windows using each courier's
own recorded payout delay. Its windows always add up to exactly what is
receivable today. A courier with too little payment history to go on is
reported as "no history — date unknown" rather than guessed at, and money whose
usual payment date has already passed is reported as late rather than moved
into next week.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import business_date, utc_now
from app.ledger.models import LedgerBucket, LedgerDirection, LedgerEntry
from app.ledger.service import LedgerService
from app.money.models import CodReceivable, ReceivableStatus
from app.money.service import AGING_BUCKETS, OVERDUE_AFTER_DAYS, AgingBucket
from app.payouts.models import Payout, PayoutLine, PayoutLineStatus

__all__ = [
    "DELAY_LOOKBACK_DAYS",
    "FORECAST_WINDOWS",
    "MIN_DELAY_SAMPLES",
    "Cashflow",
    "CashflowService",
    "CourierBalance",
    "ForecastWindow",
    "PayoutDelay",
]

#: How far back payment history is read to learn a courier's usual delay.
#: Long enough to cover a slow month; short enough that a courier which has
#: sped up (or slowed down) is judged on how it behaves now.
DELAY_LOOKBACK_DAYS = 90

#: Fewer paid parcels than this and a courier's "usual delay" is noise. Below
#: it, the forecast says "no history" rather than extrapolating from two
#: parcels.
MIN_DELAY_SAMPLES = 5

#: The forecast's windows, in days from today. ``past_expected`` and
#: ``no_history`` are not windows: they are the honest answer when the date
#: cannot be estimated.
FORECAST_WINDOWS: tuple[tuple[str, int, int | None], ...] = (
    ("next_7_days", 0, 7),
    ("days_8_to_14", 8, 14),
    ("later", 15, None),
)

_IN_TRANSIT = (
    ConsignmentStatus.BOOKED,
    ConsignmentStatus.PICKED_UP,
    ConsignmentStatus.IN_TRANSIT,
    ConsignmentStatus.OUT_FOR_DELIVERY,
)


@dataclass(frozen=True, slots=True)
class PayoutDelay:
    """How long a courier has taken to pay, from delivery to money recorded."""

    provider: str
    samples: int
    average_days: float
    median_days: int

    @property
    def is_reliable(self) -> bool:
        return self.samples >= MIN_DELAY_SAMPLES


@dataclass(slots=True)
class CourierBalance:
    provider: str
    outstanding_paisa: int = 0
    parcel_count: int = 0
    #: Delivered, and not one paisa has arrived for them yet.
    delivered_unpaid_count: int = 0
    delivered_unpaid_paisa: int = 0
    #: Outstanding for longer than ``OVERDUE_AFTER_DAYS``.
    overdue_count: int = 0
    overdue_paisa: int = 0
    oldest_age_days: int | None = None
    #: COD on this courier's parcels still on the road. Not owed until
    #: delivered, so never counted as receivable.
    in_transit_count: int = 0
    in_transit_paisa: int = 0
    last_payment_on: date | None = None
    delay: PayoutDelay | None = None
    aging: list[tuple[AgingBucket, int, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ForecastWindow:
    key: str
    parcel_count: int
    amount_paisa: int


@dataclass(slots=True)
class Cashflow:
    since: date
    until: date
    #: Ledger truth: COD that arrived in the period.
    received_paisa: int
    received_by_courier: dict[str, int]
    #: Receivable truth: what couriers hold right now.
    receivable_paisa: int
    overdue_paisa: int
    delivered_unpaid_paisa: int
    in_transit_paisa: int
    in_transit_count: int
    #: ESTIMATE. Always sums to ``receivable_paisa``.
    forecast: list[ForecastWindow]
    delays: list[PayoutDelay]
    overall_delay: PayoutDelay | None


class CashflowService:
    """Reads receivables, payouts and the ledger. Writes nothing."""

    def __init__(self, session: AsyncSession, *, ledger: LedgerService | None = None) -> None:
        self._db = session
        self._ledger = ledger or LedgerService(session)

    # ------------------------------------------------------------ couriers --

    async def courier_balances(self, *, as_of: datetime | None = None) -> list[CourierBalance]:
        """Every courier that holds money, is carrying COD, or has paid lately.

        Ordered by what is outstanding, largest first: the courier holding the
        most is the one worth calling.
        """
        moment = as_of or utc_now()
        balances: dict[str, CourierBalance] = {}

        def balance(provider: str) -> CourierBalance:
            if provider not in balances:
                balances[provider] = CourierBalance(
                    provider=provider,
                    aging=[(bucket, 0, 0) for bucket in AGING_BUCKETS],
                )
            return balances[provider]

        for receivable in await self._open_receivables():
            outstanding = receivable.outstanding_paisa
            if outstanding <= 0:
                continue
            row = balance(receivable.provider)
            row.outstanding_paisa += outstanding
            row.parcel_count += 1
            if receivable.receivable_status is ReceivableStatus.ELIGIBLE:
                row.delivered_unpaid_count += 1
                row.delivered_unpaid_paisa += outstanding
            days = receivable.age_in_days(as_of=moment)
            if days is None:
                continue
            if days > OVERDUE_AFTER_DAYS:
                row.overdue_count += 1
                row.overdue_paisa += outstanding
            row.oldest_age_days = max(row.oldest_age_days or 0, days)
            row.aging = [
                (bucket, count + 1, amount + outstanding)
                if bucket.contains(days)
                else (bucket, count, amount)
                for bucket, count, amount in row.aging
            ]

        for provider, count, amount in await self._in_transit():
            row = balance(provider)
            row.in_transit_count = count
            row.in_transit_paisa = amount

        for provider, paid_on in (await self._last_payments()).items():
            balance(provider).last_payment_on = paid_on

        for delay in (await self.payout_delays(as_of=moment)).values():
            balance(delay.provider).delay = delay

        return sorted(
            balances.values(),
            key=lambda row: (-row.outstanding_paisa, -row.in_transit_paisa, row.provider),
        )

    async def payout_delays(self, *, as_of: datetime | None = None) -> dict[str, PayoutDelay]:
        """Each courier's recorded delay from delivery to payment.

        Measured per parcel from its delivery date to the first payout that
        paid for it — the payout's own paid-on date when the statement gave
        one, otherwise the day it was recorded. Only payouts from the last
        ``DELAY_LOOKBACK_DAYS``.
        """
        samples = await self._delay_samples(as_of=as_of)
        return {provider: _delay(provider, days) for provider, days in samples.items() if days}

    # ------------------------------------------------------------ cashflow --

    async def cashflow(
        self,
        *,
        since: date | None = None,
        until: date | None = None,
        as_of: datetime | None = None,
    ) -> Cashflow:
        moment = as_of or utc_now()
        today = business_date(at=moment)
        until = until or today
        since = since or (until - timedelta(days=29))

        balances = await self._ledger.balances(since=since, until=until)
        received = balances[LedgerBucket.COD_SETTLED].net_paisa
        by_courier = await self._received_by_courier(since=since, until=until)

        samples = await self._delay_samples(as_of=moment)
        delays = {provider: _delay(provider, days) for provider, days in samples.items() if days}
        every_sample = [day for days in samples.values() for day in days]
        overall = _delay("all", every_sample) if every_sample else None

        windows: dict[str, list[int]] = {
            key: [0, 0]
            for key in (*(name for name, _, _ in FORECAST_WINDOWS), "past_expected", "no_history")
        }
        receivable = overdue = delivered_unpaid = 0
        for row in await self._open_receivables():
            outstanding = row.outstanding_paisa
            if outstanding <= 0:
                continue
            receivable += outstanding
            if row.receivable_status is ReceivableStatus.ELIGIBLE:
                delivered_unpaid += outstanding
            days = row.age_in_days(as_of=moment)
            if days is not None and days > OVERDUE_AFTER_DAYS:
                overdue += outstanding

            key = _window_for(row, delays.get(row.provider), today)
            windows[key][0] += 1
            windows[key][1] += outstanding

        in_transit = await self._in_transit()
        return Cashflow(
            since=since,
            until=until,
            received_paisa=received,
            received_by_courier=by_courier,
            receivable_paisa=receivable,
            overdue_paisa=overdue,
            delivered_unpaid_paisa=delivered_unpaid,
            in_transit_paisa=sum(amount for _, _, amount in in_transit),
            in_transit_count=sum(count for _, count, _ in in_transit),
            forecast=[
                ForecastWindow(key=key, parcel_count=count, amount_paisa=amount)
                for key, (count, amount) in windows.items()
            ],
            delays=sorted(delays.values(), key=lambda delay: delay.provider),
            overall_delay=overall,
        )

    # ------------------------------------------------------------ internals --

    async def _open_receivables(self) -> list[CodReceivable]:
        """Receivables a courier is holding money for right now.

        Whole rows, so ``outstanding_paisa`` and ``age_in_days`` come from the
        model's own definitions rather than a second copy of the arithmetic.
        """
        rows = await self._db.execute(
            sa.select(CodReceivable).where(
                CodReceivable.status.in_(
                    [str(value) for value in ReceivableStatus if value.is_collectible]
                )
            )
        )
        return list(rows.scalars().all())

    async def _in_transit(self) -> list[tuple[str, int, int]]:
        """COD on parcels still on the road, by courier: ``(provider, count, paisa)``."""
        rows = await self._db.execute(
            sa.select(
                Consignment.provider,
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(Consignment.cod_amount_paisa), 0),
            )
            .join(CodReceivable, CodReceivable.consignment_id == Consignment.id)
            .where(
                CodReceivable.status == str(ReceivableStatus.EXPECTED),
                Consignment.status.in_([str(status) for status in _IN_TRANSIT]),
            )
            .group_by(Consignment.provider)
        )
        return [(provider, int(count), int(amount)) for provider, count, amount in rows.all()]

    async def _last_payments(self) -> dict[str, date]:
        rows = await self._db.execute(
            sa.select(Payout.provider, sa.func.max(Payout.received_at)).group_by(Payout.provider)
        )
        return {
            provider: business_date(at=received_at)
            for provider, received_at in rows.all()
            if received_at is not None
        }

    async def _delay_samples(self, *, as_of: datetime | None = None) -> dict[str, list[int]]:
        moment = as_of or utc_now()
        since = moment - timedelta(days=DELAY_LOOKBACK_DAYS)
        rows = await self._db.execute(
            sa.select(
                PayoutLine.receivable_id,
                CodReceivable.provider,
                CodReceivable.eligible_business_date,
                Payout.paid_on,
                Payout.received_at,
            )
            .join(Payout, Payout.id == PayoutLine.payout_id)
            .join(CodReceivable, CodReceivable.id == PayoutLine.receivable_id)
            .where(
                PayoutLine.status.in_(
                    [str(PayoutLineStatus.MATCHED), str(PayoutLineStatus.MANUAL_MATCHED)]
                ),
                Payout.received_at >= since,
                CodReceivable.eligible_business_date.is_not(None),
            )
        )
        first_paid: dict = {}
        for receivable_id, provider, delivered_on, paid_on, received_at in rows.all():
            paid = paid_on or business_date(at=received_at)
            current = first_paid.get(receivable_id)
            if current is None or paid < current[2]:
                first_paid[receivable_id] = (provider, delivered_on, paid)

        samples: dict[str, list[int]] = defaultdict(list)
        for provider, delivered_on, paid in first_paid.values():
            samples[provider].append(max(0, (paid - delivered_on).days))
        return dict(samples)

    async def _received_by_courier(self, *, since: date, until: date) -> dict[str, int]:
        """COD that arrived in the period, per courier — from the ledger.

        The settled bucket's entries are joined back to their receivable for
        the courier. The same entries the headline total is summed from, so
        the per-courier figures add up to it.
        """
        rows = await self._db.execute(
            sa.select(
                CodReceivable.provider,
                LedgerEntry.direction,
                sa.func.coalesce(sa.func.sum(LedgerEntry.amount_paisa), 0),
            )
            .join(CodReceivable, CodReceivable.id == LedgerEntry.entity_id)
            .where(
                LedgerEntry.entity_type == "cod_receivable",
                LedgerEntry.bucket == str(LedgerBucket.COD_SETTLED),
                LedgerEntry.business_date >= since,
                LedgerEntry.business_date <= until,
            )
            .group_by(CodReceivable.provider, LedgerEntry.direction)
        )
        totals: dict[str, int] = defaultdict(int)
        for provider, direction, amount in rows.all():
            signed = int(amount) if direction == str(LedgerDirection.CREDIT) else -int(amount)
            totals[provider] += signed
        return {provider: amount for provider, amount in totals.items() if amount}


def _delay(provider: str, days: list[int]) -> PayoutDelay:
    return PayoutDelay(
        provider=provider,
        samples=len(days),
        average_days=round(sum(days) / len(days), 1),
        median_days=int(statistics.median_low(days)),
    )


def _window_for(receivable: CodReceivable, delay: PayoutDelay | None, today: date) -> str:
    """Where a parcel's money falls in the forecast, or why it cannot be placed.

    The expected date is the delivery date plus the courier's usual (median)
    delay — an estimate, and reported as one.
    """
    if delay is None or not delay.is_reliable or receivable.eligible_business_date is None:
        return "no_history"
    expected = receivable.eligible_business_date + timedelta(days=delay.median_days)
    days_until = (expected - today).days
    if days_until < 0:
        return "past_expected"
    for key, start, end in FORECAST_WINDOWS:
        if days_until >= start and (end is None or days_until <= end):
            return key
    return "later"
