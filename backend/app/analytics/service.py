"""The figures behind the Home and Insights screens.

Master spec sections 1.1, 19 and 135. Two rules shape every method here.

The first is section 1.1's: the home screen is *"the seller's daily money
control screen"*, not a generic analytics dashboard. So it answers what came
in, what went out, and what is still owed — today, in taka — and nothing else.

The second is section 135's: an estimated figure must never be presented as
exact. Every total therefore travels with the quality of the snapshots behind
it, so the screen can say "৳12,400 (3 parcels estimated)" rather than a number
that looks measured when it is not.

Nothing here maintains a counter. Every figure is derived from current profit
snapshots, live receivables and the ledger, which is what makes the dashboard
reconcile to the ledger by construction (section 81.10) instead of by
discipline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import business_date, ensure_utc, utc_now
from app.expenses.service import ExpenseService
from app.ledger.service import LedgerService
from app.money.models import CodReceivable, ReceivableStatus
from app.money.service import ReceivableService
from app.notifications.alerts import MIN_RANKING_SAMPLE, AlertService, AlertSummary
from app.orders.models import Order, OrderItem
from app.profit.models import ProfitQuality, ProfitSnapshot
from app.profit.service import ProfitService
from app.reconciliation.models import CaseKind, CaseStatus, ReconciliationCase

__all__ = [
    "AnalyticsService",
    "HomeMetrics",
    "ProductLine",
    "ProfitReport",
    "RateLine",
    "ReturnReport",
]


@dataclass(slots=True)
class HomeMetrics:
    """Section 1.1's list, in its order.

    ``as_of`` is the Asia/Dhaka business date the "today" figures cover, so a
    seller looking at the screen at 01:00 knows which day it means.
    """

    as_of: date
    orders_today: int = 0
    delivered_today: int = 0
    returned_today: int = 0
    gross_sales_paisa: int = 0
    realized_revenue_paisa: int = 0
    contribution_profit_paisa: int = 0
    cod_outstanding_paisa: int = 0
    #: COD this shop's own settlement history says should land today. Zero
    #: when there is no history to say so — see ``cod_unforecast_paisa``.
    cod_expected_today_paisa: int = 0
    #: Outstanding COD with no arrival estimate, because the provider has not
    #: settled enough parcels for this shop to have observed a lag yet.
    #: Reported rather than folded into "expected", so the screen never
    #: implies money is due when nobody knows when it is coming.
    cod_unforecast_paisa: int = 0
    cod_overdue_paisa: int = 0
    mismatch_paisa: int = 0
    mismatch_count: int = 0
    return_loss_paisa: int = 0
    #: How many of today's parcels carry an estimated rather than a measured
    #: figure. Section 135: never present an estimate as exact.
    estimated_parcels: int = 0
    incomplete_parcels: int = 0
    alerts: list[AlertSummary] = field(default_factory=list)


@dataclass(slots=True)
class ProfitReport:
    """The Insights headline, with its own confidence attached."""

    since: date
    until: date
    parcel_count: int = 0
    realized_revenue_paisa: int = 0
    item_cost_paisa: int = 0
    delivery_charge_paisa: int = 0
    cod_fee_paisa: int = 0
    return_charge_paisa: int = 0
    packaging_paisa: int = 0
    ad_cost_paisa: int = 0
    write_off_cost_paisa: int = 0
    contribution_profit_paisa: int = 0
    #: Ad spend recorded but never allocated to a parcel. Shown separately
    #: because section 86 forbids smearing it silently across orders — a
    #: seller who spent it should see it even when nothing carries it.
    unallocated_ad_spend_paisa: int = 0
    fixed_cost_paisa: int = 0
    quality: dict[ProfitQuality, int] = field(default_factory=dict)

    @property
    def margin_basis_points(self) -> int | None:
        if self.realized_revenue_paisa == 0:
            return None
        return round(self.contribution_profit_paisa * 10_000 / self.realized_revenue_paisa)

    @property
    def operating_profit_paisa(self) -> int:
        """Contribution profit after the costs no parcel carries.

        Named separately from contribution profit because they answer
        different questions: contribution says whether selling one more is
        worth it, this says whether the shop made money.
        """
        return (
            self.contribution_profit_paisa - self.unallocated_ad_spend_paisa - self.fixed_cost_paisa
        )


@dataclass(frozen=True, slots=True)
class RateLine:
    """A return rate for one product, area or courier."""

    label: str
    parcel_count: int
    return_count: int
    loss_paisa: int

    @property
    def return_rate_basis_points(self) -> int:
        if self.parcel_count == 0:
            return 0
        return round(self.return_count * 10_000 / self.parcel_count)

    @property
    def has_enough_sample(self) -> bool:
        """Section 24's rule, carried on the row itself.

        Kept on the line rather than filtered out, so the screen can show the
        figure greyed with "not enough data yet" instead of hiding a product
        the seller knows they sold.
        """
        return self.parcel_count >= MIN_RANKING_SAMPLE


@dataclass(slots=True)
class ReturnReport:
    """Section 19's return economics."""

    since: date
    until: date
    return_count: int = 0
    parcel_count: int = 0
    direct_loss_paisa: int = 0
    outward_delivery_cost_paisa: int = 0
    return_delivery_cost_paisa: int = 0
    packaging_loss_paisa: int = 0
    write_off_paisa: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)
    by_product: list[RateLine] = field(default_factory=list)
    by_area: list[RateLine] = field(default_factory=list)
    by_courier: list[RateLine] = field(default_factory=list)
    #: Returns with no reason recorded. Section 19 wants the reason; this is
    #: how much of the picture is missing rather than a silent gap.
    unknown_reason_count: int = 0

    @property
    def return_rate_basis_points(self) -> int | None:
        if self.parcel_count == 0:
            return None
        return round(self.return_count * 10_000 / self.parcel_count)


@dataclass(frozen=True, slots=True)
class ProductLine:
    """One product's contribution over a period."""

    product_name: str
    parcel_count: int
    units_delivered: int
    revenue_paisa: int
    profit_paisa: int
    return_count: int

    @property
    def margin_basis_points(self) -> int | None:
        if self.revenue_paisa == 0:
            return None
        return round(self.profit_paisa * 10_000 / self.revenue_paisa)

    @property
    def has_enough_sample(self) -> bool:
        return self.parcel_count >= MIN_RANKING_SAMPLE


class AnalyticsService:
    """Read-only. Writes nothing, maintains nothing."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        profit: ProfitService | None = None,
        expenses: ExpenseService | None = None,
        receivables: ReceivableService | None = None,
        ledger: LedgerService | None = None,
        alerts: AlertService | None = None,
    ) -> None:
        self._db = session
        self._profit = profit or ProfitService(session)
        self._expenses = expenses or ExpenseService(session)
        self._receivables = receivables or ReceivableService(session)
        self._ledger = ledger or LedgerService(session)
        self._alerts = alerts or AlertService(session)

    # ------------------------------------------------------------- home --

    async def home(self, *, as_of: date | None = None) -> HomeMetrics:
        """The daily money control screen (section 1.1)."""
        day = as_of or business_date(at=utc_now())

        totals = await self._profit.totals(since=day, until=day)
        quality = await self._profit.quality_breakdown(since=day, until=day)
        outcomes = await self._outcome_counts(day, day)
        aging = await self._receivables.aging()
        mismatch_count, mismatch_paisa = await self._mismatch(day)
        expected_today, unforecast = await self._expected_today(day)

        return HomeMetrics(
            as_of=day,
            orders_today=await self._order_count(day, day),
            delivered_today=outcomes.get(str(ConsignmentStatus.DELIVERED), 0)
            + outcomes.get(str(ConsignmentStatus.PARTIAL_DELIVERED), 0),
            returned_today=outcomes.get(str(ConsignmentStatus.RETURNED), 0),
            gross_sales_paisa=await self._gross_sales(day, day),
            realized_revenue_paisa=totals["realized_revenue_paisa"],
            contribution_profit_paisa=totals["contribution_profit_paisa"],
            cod_outstanding_paisa=sum(amount for _, _, amount in aging),
            cod_expected_today_paisa=expected_today,
            cod_unforecast_paisa=unforecast,
            cod_overdue_paisa=sum(amount for bucket, _, amount in aging if bucket.min_days >= 8),
            mismatch_paisa=mismatch_paisa,
            mismatch_count=mismatch_count,
            return_loss_paisa=await self._direct_return_loss(day, day),
            estimated_parcels=quality.get(ProfitQuality.ESTIMATED, 0),
            incomplete_parcels=quality.get(ProfitQuality.MISSING, 0),
            alerts=[a for a in await self._alerts.current_alerts() if not a.is_empty],
        )

    # ----------------------------------------------------------- profit --

    async def profit(self, *, since: date, until: date) -> ProfitReport:
        """P&L over a period, with the ad spend that reached nothing shown."""
        totals = await self._profit.totals(since=since, until=until)
        spend = await self._expenses.totals(since=since, until=until)

        return ProfitReport(
            since=since,
            until=until,
            parcel_count=totals["parcel_count"],
            realized_revenue_paisa=totals["realized_revenue_paisa"],
            item_cost_paisa=totals["item_cost_paisa"],
            delivery_charge_paisa=totals["delivery_charge_paisa"],
            cod_fee_paisa=totals["cod_fee_paisa"],
            return_charge_paisa=totals["return_charge_paisa"],
            packaging_paisa=totals["packaging_paisa"],
            ad_cost_paisa=totals["ad_cost_paisa"],
            write_off_cost_paisa=totals["write_off_cost_paisa"],
            contribution_profit_paisa=totals["contribution_profit_paisa"],
            unallocated_ad_spend_paisa=spend["unallocated_paisa"],
            fixed_cost_paisa=spend["fixed_paisa"] + spend["other_paisa"],
            quality=await self._profit.quality_breakdown(since=since, until=until),
        )

    # ---------------------------------------------------------- returns --

    async def returns(self, *, since: date, until: date) -> ReturnReport:
        """Section 19's return economics, from the shop's own parcels."""
        report = ReturnReport(since=since, until=until)

        rows = await self._db.execute(
            sa.select(
                ProfitSnapshot.outcome,
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.delivery_charge_paisa), 0),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.return_charge_paisa), 0),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.packaging_paisa), 0),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.write_off_cost_paisa), 0),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.contribution_profit_paisa), 0),
            )
            .where(*self._window(since, until))
            .group_by(ProfitSnapshot.outcome)
        )

        for outcome, count, outward, back, packaging, write_off, profit in rows:
            report.parcel_count += int(count)
            if outcome != str(ConsignmentStatus.RETURNED):
                continue
            report.return_count = int(count)
            # On a return the outward leg was paid and collected nothing, so
            # it is a loss rather than a cost of sale.
            report.outward_delivery_cost_paisa = int(outward or 0)
            report.return_delivery_cost_paisa = int(back or 0)
            report.packaging_loss_paisa = int(packaging or 0)
            report.write_off_paisa = int(write_off or 0)
            report.direct_loss_paisa = -min(0, int(profit or 0))

        report.by_reason, report.unknown_reason_count = await self._return_reasons(since, until)
        report.by_product = await self._returns_by_product(since, until)
        report.by_area = await self._returns_by_area(since, until)
        report.by_courier = await self._returns_by_courier(since, until)
        return report

    # --------------------------------------------------------- products --

    async def products(self, *, since: date, until: date, limit: int = 50) -> list[ProductLine]:
        """Contribution profit by product, best first.

        A parcel's profit is split across its lines in proportion to what each
        line sold for. Crediting the whole parcel to each product would make
        every multi-item order count two or three times, and the ranking would
        reward bundling rather than margin.
        """
        rows = await self._db.execute(
            sa.select(
                ProfitSnapshot.order_id,
                ProfitSnapshot.contribution_profit_paisa,
                ProfitSnapshot.realized_revenue_paisa,
                ProfitSnapshot.outcome,
                OrderItem.product_name,
                OrderItem.quantity,
                OrderItem.unit_price_paisa,
                OrderItem.discount_paisa,
            )
            .join(OrderItem, OrderItem.order_id == ProfitSnapshot.order_id)
            .where(*self._window(since, until))
            .order_by(OrderItem.order_id, OrderItem.position)
        )

        from app.common.money import Money

        parcels: dict[object, tuple[int, int, str, list[tuple[str, int, int]]]] = {}
        for (
            order_id,
            profit,
            revenue,
            outcome,
            name,
            quantity,
            unit_price,
            discount,
        ) in rows:
            line_total = max(0, unit_price * quantity - discount)
            entry = parcels.setdefault(order_id, (int(profit or 0), int(revenue or 0), outcome, []))
            entry[3].append((name, line_total, int(quantity)))

        accumulated: dict[str, list[int]] = {}
        for profit, revenue, outcome, lines in parcels.values():
            weights = [weight for _, weight, _ in lines]
            profit_shares = Money(profit).allocate(weights)
            revenue_shares = Money(revenue).allocate(weights)
            returned = outcome == str(ConsignmentStatus.RETURNED)
            delivered = outcome in (
                str(ConsignmentStatus.DELIVERED),
                str(ConsignmentStatus.PARTIAL_DELIVERED),
            )
            for (name, _, quantity), profit_share, revenue_share in zip(
                lines, profit_shares, revenue_shares, strict=True
            ):
                row = accumulated.setdefault(name, [0, 0, 0, 0, 0])
                row[0] += 1  # parcels
                row[1] += quantity if delivered else 0
                row[2] += revenue_share.paisa
                row[3] += profit_share.paisa
                row[4] += 1 if returned else 0

        lines_out = [
            ProductLine(
                product_name=name,
                parcel_count=values[0],
                units_delivered=values[1],
                revenue_paisa=values[2],
                profit_paisa=values[3],
                return_count=values[4],
            )
            for name, values in accumulated.items()
        ]
        # Name breaks ties so the list is stable between identical requests.
        lines_out.sort(key=lambda line: (-line.profit_paisa, line.product_name))
        return lines_out[:limit]

    # -------------------------------------------------------- internals --

    def _window(self, since: date, until: date) -> tuple[sa.ColumnElement[bool], ...]:
        return (
            ProfitSnapshot.is_current.is_(True),
            ProfitSnapshot.business_date >= since,
            ProfitSnapshot.business_date <= until,
        )

    async def _outcome_counts(self, since: date, until: date) -> dict[str, int]:
        rows = await self._db.execute(
            sa.select(ProfitSnapshot.outcome, sa.func.count())
            .where(*self._window(since, until))
            .group_by(ProfitSnapshot.outcome)
        )
        return {outcome: int(count) for outcome, count in rows}

    async def _order_count(self, since: date, until: date) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(Order)
            .where(Order.business_date >= since, Order.business_date <= until)
        )
        return int(result.scalar_one())

    async def _gross_sales(self, since: date, until: date) -> int:
        """What was ordered, before anybody delivered anything.

        Distinct from realized revenue on purpose (section 1.1 lists both): a
        seller who confuses the two thinks a day of orders is a day of money.
        """
        result = await self._db.execute(
            sa.select(
                sa.func.coalesce(
                    sa.func.sum(
                        Order.subtotal_paisa - Order.discount_paisa + Order.delivery_fee_paisa
                    ),
                    0,
                )
            ).where(Order.business_date >= since, Order.business_date <= until)
        )
        return int(result.scalar_one() or 0)

    async def _expected_today(self, day: date) -> tuple[int, int]:
        """COD due today, and COD nobody can yet put a date on.

        The arrival date is ``became collectible + this shop's own median lag
        for that provider``. Section 140 forbids inventing provider payout
        behaviour, and no courier publishes a settlement calendar we have
        verified — but what a provider has actually paid *this* seller is
        observed fact, so that is what the forecast uses.

        Where the observation does not exist yet, the money is returned in the
        second figure instead of being quietly counted as due today. An
        invented date here would be worse than no date: the seller would ring
        the courier about money that was never late.
        """
        lags = await self._observed_settlement_lags()
        receivables = await self._db.execute(
            sa.select(CodReceivable).where(
                CodReceivable.status == str(ReceivableStatus.ELIGIBLE),
            )
        )

        expected = unforecast = 0
        for receivable in receivables.scalars().all():
            outstanding = receivable.outstanding_paisa
            if outstanding <= 0:
                continue
            lag = lags.get(receivable.provider)
            if lag is None or receivable.eligible_business_date is None:
                unforecast += outstanding
                continue
            if receivable.eligible_business_date + timedelta(days=lag) == day:
                expected += outstanding
        return expected, unforecast

    async def _observed_settlement_lags(self) -> dict[str, int]:
        """Median days from collectible to settled, per provider.

        Median rather than mean: one statement that arrived three weeks late
        should not push every future estimate out with it.
        """
        rows = await self._db.execute(
            sa.select(
                CodReceivable.provider,
                CodReceivable.eligible_business_date,
                CodReceivable.settled_at,
            ).where(
                CodReceivable.status == str(ReceivableStatus.SETTLED),
                CodReceivable.eligible_business_date.is_not(None),
                CodReceivable.settled_at.is_not(None),
            )
        )

        observed: dict[str, list[int]] = {}
        for provider, eligible_on, settled_at in rows:
            if eligible_on is None or settled_at is None:
                continue
            days = (business_date(at=ensure_utc(settled_at)) - eligible_on).days
            observed.setdefault(provider, []).append(max(0, days))

        medians: dict[str, int] = {}
        for provider, samples in observed.items():
            if len(samples) < MIN_RANKING_SAMPLE:
                # Section 24's rule again: too few settlements to claim a lag.
                continue
            samples.sort()
            medians[provider] = samples[len(samples) // 2]
        return medians

    async def _mismatch(self, day: date) -> tuple[int, int]:
        """Open under- and overpayments: how many, and how much is disputed."""
        result = await self._db.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(ReconciliationCase.amount_paisa), 0),
            ).where(
                ReconciliationCase.status.in_([str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)]),
                ReconciliationCase.kind.in_(
                    [
                        str(CaseKind.UNDERPAID),
                        str(CaseKind.OVERPAID),
                        str(CaseKind.UNKNOWN_DEDUCTION),
                    ]
                ),
            )
        )
        count, total = result.one()
        return int(count), int(total or 0)

    async def _direct_return_loss(self, since: date, until: date) -> int:
        result = await self._db.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(-ProfitSnapshot.contribution_profit_paisa), 0)
            ).where(
                *self._window(since, until),
                ProfitSnapshot.outcome == str(ConsignmentStatus.RETURNED),
                ProfitSnapshot.contribution_profit_paisa < 0,
            )
        )
        return int(result.scalar_one() or 0)

    async def _return_reasons(self, since: date, until: date) -> tuple[dict[str, int], int]:
        rows = await self._db.execute(
            sa.select(ProfitSnapshot.return_reason, sa.func.count())
            .where(
                *self._window(since, until),
                ProfitSnapshot.outcome == str(ConsignmentStatus.RETURNED),
            )
            .group_by(ProfitSnapshot.return_reason)
        )
        by_reason: dict[str, int] = {}
        unknown = 0
        for reason, count in rows:
            if reason is None:
                unknown = int(count)
                continue
            by_reason[reason] = int(count)
        return by_reason, unknown

    async def _returns_by_product(self, since: date, until: date) -> list[RateLine]:
        rows = await self._db.execute(
            sa.select(
                OrderItem.product_name,
                ProfitSnapshot.outcome,
                sa.func.count(sa.distinct(ProfitSnapshot.id)),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.contribution_profit_paisa), 0),
            )
            .join(OrderItem, OrderItem.order_id == ProfitSnapshot.order_id)
            .where(*self._window(since, until))
            .group_by(OrderItem.product_name, ProfitSnapshot.outcome)
        )
        return _rate_lines(rows)

    async def _returns_by_area(self, since: date, until: date) -> list[RateLine]:
        rows = await self._db.execute(
            sa.select(
                sa.func.coalesce(Order.delivery_area, Order.delivery_district, "Unknown"),
                ProfitSnapshot.outcome,
                sa.func.count(sa.distinct(ProfitSnapshot.id)),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.contribution_profit_paisa), 0),
            )
            .join(Order, Order.id == ProfitSnapshot.order_id)
            .where(*self._window(since, until))
            .group_by(
                sa.func.coalesce(Order.delivery_area, Order.delivery_district, "Unknown"),
                ProfitSnapshot.outcome,
            )
        )
        return _rate_lines(rows)

    async def _returns_by_courier(self, since: date, until: date) -> list[RateLine]:
        rows = await self._db.execute(
            sa.select(
                Consignment.provider,
                ProfitSnapshot.outcome,
                sa.func.count(sa.distinct(ProfitSnapshot.id)),
                sa.func.coalesce(sa.func.sum(ProfitSnapshot.contribution_profit_paisa), 0),
            )
            .join(Consignment, Consignment.id == ProfitSnapshot.consignment_id)
            .where(*self._window(since, until))
            .group_by(Consignment.provider, ProfitSnapshot.outcome)
        )
        return _rate_lines(rows)


def _rate_lines(rows: sa.CursorResult | object) -> list[RateLine]:
    """Fold ``(label, outcome, count, profit)`` rows into one line per label."""
    totals: dict[str, list[int]] = {}
    for label, outcome, count, profit in rows:  # type: ignore[union-attr]
        entry = totals.setdefault(str(label), [0, 0, 0])
        entry[0] += int(count)
        if outcome == str(ConsignmentStatus.RETURNED):
            entry[1] += int(count)
            entry[2] += -min(0, int(profit or 0))

    lines = [
        RateLine(label=label, parcel_count=values[0], return_count=values[1], loss_paisa=values[2])
        for label, values in totals.items()
    ]
    # Worst first: a return report is read to find the problem.
    lines.sort(key=lambda line: (-line.return_rate_basis_points, line.label))
    return lines


def default_window(days: int = 30) -> tuple[date, date]:
    """The period the Insights screen opens on."""
    until = business_date(at=utc_now())
    return until - timedelta(days=days - 1), until
