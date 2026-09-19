"""The alerts and the Friday summary.

Master spec section 23. Its four alerts, in its own words:

```text
🔴 Delivered but unpaid: 7 parcels — ৳8,950
🟠 Underpaid: 3 — shortage ৳420
🟡 15+ days in transit: 4 — ৳5,200 exposed
🔵 Returned but stock not restored: 6
```

Each one is a count and an amount, because a seller deciding what to do first
needs to know what it costs. And each is derived from the reconciliation cases
the Phase D engine already opens, rather than re-deriving the same conditions
in a second place where the two could disagree.

Section 23 closes with the constraint that matters most here: *"do not send
noisy notifications for non-actionable changes."*

V2.2: the notifications themselves are raised by the smart-alert engine
(:mod:`app.notifications.smart`), which owns deduplication, cooldowns and
resolution. The four lines above remain the Home and Friday-summary figures.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rto import RTO_STATUSES, is_rto_status
from app.common.money import Money, format_bdt
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import business_date, utc_now
from app.expenses.service import ExpenseService
from app.money.service import ReceivableService
from app.notifications.models import NotificationCategory, NotificationKind, Severity
from app.notifications.service import NotificationService
from app.orders.models import Order, OrderItem
from app.profit.models import ProfitSnapshot
from app.profit.service import ProfitService
from app.reconciliation.models import CaseKind, CaseStatus, ReconciliationCase
from app.tenants.roles import Permission

__all__ = [
    "MIN_RANKING_SAMPLE",
    "AlertService",
    "AlertSummary",
    "WeeklySummary",
]

#: Below this many parcels, a "best" or "worst" ranking is noise. Section 24:
#: *"do not show unreliable rankings before enough sample exists."* Two
#: deliveries of one product and one of another says nothing about which
#: sells better.
MIN_RANKING_SAMPLE = 5


@dataclass(frozen=True, slots=True)
class AlertSummary:
    """One of section 23's four lines."""

    kind: NotificationKind
    severity: Severity
    count: int
    amount_paisa: int

    @property
    def is_empty(self) -> bool:
        return self.count == 0


@dataclass(slots=True)
class WeeklySummary:
    """Section 23's Friday figures.

    ``best_product`` and friends are ``None`` when the week's sample is too
    small to rank honestly — section 24. A "worst product" chosen from three
    deliveries would send a seller to delist something that was simply
    unlucky.
    """

    week_start: date
    week_end: date
    order_count: int = 0
    delivered_count: int = 0
    return_count: int = 0
    return_loss_paisa: int = 0
    sales_paisa: int = 0
    contribution_profit_paisa: int = 0
    cod_outstanding_paisa: int = 0
    overdue_paisa: int = 0
    mismatch_count: int = 0
    ad_spend_paisa: int = 0
    #: ``(product name, contribution profit in paisa)``.
    best_product: tuple[str, int] | None = None
    worst_product: tuple[str, int] | None = None
    #: ``(provider, delivery success in basis points)``.
    best_courier: tuple[str, int] | None = None
    worst_courier: tuple[str, int] | None = None
    #: Why a ranking was withheld, so the screen can say so rather than show
    #: an empty space. Section 24: *"sample-size rule must be visible."*
    ranking_note: str | None = None
    courier_note: str | None = None
    alerts: list[AlertSummary] = field(default_factory=list)

    @property
    def return_rate_basis_points(self) -> int | None:
        settled = self.delivered_count + self.return_count
        if settled == 0:
            return None
        return round(self.return_count * 10_000 / settled)


class AlertService:
    """Turns the shop's own data into the four alerts and the Friday summary."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        notifications: NotificationService | None = None,
        receivables: ReceivableService | None = None,
        profit: ProfitService | None = None,
        expenses: ExpenseService | None = None,
    ) -> None:
        self._db = session
        self._notifications = notifications or NotificationService(session)
        self._receivables = receivables or ReceivableService(session)
        self._profit = profit or ProfitService(session)
        self._expenses = expenses or ExpenseService(session)

    # --------------------------------------------------------------- alerts --

    async def current_alerts(self) -> list[AlertSummary]:
        """Section 23's four lines, counted from the open cases.

        Derived from the cases the reconciliation engine opens rather than
        recomputed here. Two places deciding independently what "delivered but
        unpaid" means is two places that will eventually disagree, and the
        seller would have no way to tell which was right.
        """
        rows = await self._db.execute(
            sa.select(
                ReconciliationCase.kind,
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(ReconciliationCase.amount_paisa), 0),
            )
            .where(
                ReconciliationCase.status.in_([str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)])
            )
            .group_by(ReconciliationCase.kind)
        )
        by_kind = {kind: (int(count), int(total or 0)) for kind, count, total in rows}

        mapping: list[tuple[CaseKind, NotificationKind, Severity]] = [
            (
                CaseKind.DELIVERED_BUT_UNPAID,
                NotificationKind.DELIVERED_BUT_UNPAID,
                Severity.CRITICAL,
            ),
            (CaseKind.UNDERPAID, NotificationKind.UNDERPAID, Severity.WARNING),
            (
                CaseKind.STALE_IN_TRANSIT,
                NotificationKind.STALE_IN_TRANSIT,
                Severity.WARNING,
            ),
            (
                CaseKind.RETURNED_NOT_RESTOCKED,
                NotificationKind.RETURNED_NOT_RESTOCKED,
                Severity.ACTION,
            ),
        ]

        return [
            AlertSummary(
                kind=notification_kind,
                severity=severity,
                count=by_kind.get(str(case_kind), (0, 0))[0],
                amount_paisa=by_kind.get(str(case_kind), (0, 0))[1],
            )
            for case_kind, notification_kind, severity in mapping
        ]

    async def raise_daily_alerts(self) -> int:
        """Run the V2.2 smart alerts for this shop. Returns how many were new.

        The engine decides per alert identity whether anything is written, so
        running this hourly, or twice at once, raises each alert once; the old
        "one per kind per day" rule is gone, and with it the daily repeat of an
        unchanged warning. RTO is judged by the canonical classifier in
        :mod:`app.analytics.rto` (``RETURNED`` and courier ``CANCELLED``), not
        by counting ``RETURNED`` alone.
        """
        from app.notifications.smart import SmartAlerts

        return await SmartAlerts(self._db, notifications=self._notifications).run()

    # -------------------------------------------------------- friday summary --

    async def weekly_summary(self, *, week_end: date | None = None) -> WeeklySummary:
        """The Friday figures (master spec section 23).

        The week runs to the given Dhaka date inclusive, seven days back. Every
        figure comes from a current profit snapshot or a live receivable, so
        the summary agrees with what the seller sees on the other screens.
        """
        end = week_end or business_date(at=utc_now())
        start = end - timedelta(days=6)

        totals = await self._profit.totals(since=start, until=end)
        outcomes = await self._outcome_counts(start, end)
        aging = await self._receivables.aging()
        spend = await self._expenses.totals(since=start, until=end)

        order_count = await self._order_count(start, end)
        mismatch_count = await self._mismatch_count()
        return_loss = await self._return_loss(start, end)

        summary = WeeklySummary(
            week_start=start,
            week_end=end,
            order_count=order_count,
            delivered_count=outcomes.get(str(ConsignmentStatus.DELIVERED), 0)
            + outcomes.get(str(ConsignmentStatus.PARTIAL_DELIVERED), 0),
            return_count=sum(
                count for outcome, count in outcomes.items() if is_rto_status(outcome)
            ),
            return_loss_paisa=return_loss,
            sales_paisa=totals["realized_revenue_paisa"],
            contribution_profit_paisa=totals["contribution_profit_paisa"],
            cod_outstanding_paisa=sum(amount for _, _, amount in aging),
            overdue_paisa=sum(amount for bucket, _, amount in aging if bucket.min_days >= 8),
            mismatch_count=mismatch_count,
            ad_spend_paisa=spend["ad_spend_paisa"],
            alerts=[alert for alert in await self.current_alerts() if not alert.is_empty],
        )

        best, worst, note = await self._product_ranking(start, end)
        summary.best_product = best
        summary.worst_product = worst
        summary.ranking_note = note

        best_courier, worst_courier, courier_note = await self._courier_ranking(start, end)
        summary.best_courier = best_courier
        summary.worst_courier = worst_courier
        summary.courier_note = courier_note
        return summary

    async def send_weekly_summary(self, *, week_end: date | None = None) -> WeeklySummary:
        """Build the summary and put it in the notification centre.

        ``INFO``: it is worth reading, but it is not a reason to interrupt
        somebody's Friday evening (section 95).
        """
        summary = await self.weekly_summary(week_end=week_end)
        await self._notifications.notify(
            kind=NotificationKind.WEEKLY_SUMMARY,
            severity=Severity.INFO,
            title=f"Your week: {format_bdt(summary.contribution_profit_paisa)} profit",
            body=(
                f"{summary.delivered_count} delivered, {summary.return_count} came "
                f"back. {format_bdt(summary.cod_outstanding_paisa)} still with "
                "couriers."
            ),
            dedupe_key=f"weekly:{summary.week_end.isoformat()}",
            category=NotificationCategory.SUMMARY,
            # Profit figures: the people who may see Money.
            audience=Permission.MONEY_VIEW,
            amount_paisa=summary.contribution_profit_paisa,
            payload={
                "week_start": summary.week_start.isoformat(),
                "week_end": summary.week_end.isoformat(),
                "order_count": summary.order_count,
                "delivered_count": summary.delivered_count,
                "return_count": summary.return_count,
                "return_loss_paisa": summary.return_loss_paisa,
                "sales_paisa": summary.sales_paisa,
                "contribution_profit_paisa": summary.contribution_profit_paisa,
                "cod_outstanding_paisa": summary.cod_outstanding_paisa,
                "overdue_paisa": summary.overdue_paisa,
                "mismatch_count": summary.mismatch_count,
                "ad_spend_paisa": summary.ad_spend_paisa,
                "best_product": summary.best_product,
                "worst_product": summary.worst_product,
                "best_courier": summary.best_courier,
                "worst_courier": summary.worst_courier,
                "ranking_note": summary.ranking_note,
                "courier_note": summary.courier_note,
            },
        )
        return summary

    # ------------------------------------------------------------ internals --

    async def _outcome_counts(self, since: date, until: date) -> dict[str, int]:
        rows = await self._db.execute(
            sa.select(ProfitSnapshot.outcome, sa.func.count())
            .where(
                ProfitSnapshot.is_current.is_(True),
                ProfitSnapshot.business_date >= since,
                ProfitSnapshot.business_date <= until,
            )
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

    async def _mismatch_count(self) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(ReconciliationCase)
            .where(
                ReconciliationCase.status.in_([str(CaseStatus.OPEN), str(CaseStatus.IN_PROGRESS)]),
                ReconciliationCase.kind.in_([str(CaseKind.UNDERPAID), str(CaseKind.OVERPAID)]),
            )
        )
        return int(result.scalar_one())

    async def _return_loss(self, since: date, until: date) -> int:
        """What returns actually cost over the period.

        The loss on parcels that came back: charges paid with nothing
        collected, plus any goods written off. Section 19's "direct return
        loss".
        """
        result = await self._db.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(-ProfitSnapshot.contribution_profit_paisa), 0)
            ).where(
                ProfitSnapshot.is_current.is_(True),
                ProfitSnapshot.business_date >= since,
                ProfitSnapshot.business_date <= until,
                ProfitSnapshot.outcome.in_(RTO_STATUSES),
                ProfitSnapshot.contribution_profit_paisa < 0,
            )
        )
        return int(result.scalar_one() or 0)

    async def _product_ranking(
        self, since: date, until: date
    ) -> tuple[tuple[str, int] | None, tuple[str, int] | None, str | None]:
        """Best and worst product by contribution profit, or nothing.

        A parcel's profit is a parcel-level figure, so for a multi-item order
        it is split across the lines in proportion to what each line sold for,
        using the same largest-remainder allocator as ad cost. Attributing the
        whole parcel's profit to every product in it — the obvious join —
        would credit a three-product order three times and hand the seller a
        ranking built on triple-counted money.

        Section 24: *"do not show unreliable rankings before enough sample
        exists."* Below :data:`MIN_RANKING_SAMPLE` parcels the answer is a
        sentence explaining why there is no ranking, which is more useful than
        a confident one drawn from three orders.
        """
        rows = await self._db.execute(
            sa.select(
                ProfitSnapshot.order_id,
                ProfitSnapshot.contribution_profit_paisa,
                OrderItem.product_name,
                OrderItem.quantity,
                OrderItem.unit_price_paisa,
                OrderItem.discount_paisa,
            )
            .join(OrderItem, OrderItem.order_id == ProfitSnapshot.order_id)
            .where(
                ProfitSnapshot.is_current.is_(True),
                ProfitSnapshot.business_date >= since,
                ProfitSnapshot.business_date <= until,
            )
            .order_by(OrderItem.order_id, OrderItem.position)
        )

        parcels: dict[uuid.UUID, tuple[int, list[tuple[str, int]]]] = {}
        for order_id, profit, name, quantity, unit_price, discount in rows:
            line_total = max(0, unit_price * quantity - discount)
            _, lines = parcels.setdefault(order_id, (int(profit or 0), []))
            lines.append((name, line_total))

        profit_by_product: dict[str, int] = {}
        parcels_by_product: dict[str, int] = {}
        for profit, lines in parcels.values():
            shares = Money(profit).allocate([weight for _, weight in lines])
            for (name, _), share in zip(lines, shares, strict=True):
                profit_by_product[name] = profit_by_product.get(name, 0) + share.paisa
                parcels_by_product[name] = parcels_by_product.get(name, 0) + 1

        eligible = [
            (name, profit)
            for name, profit in profit_by_product.items()
            if parcels_by_product[name] >= MIN_RANKING_SAMPLE
        ]
        if len(eligible) < 2:
            total = len(parcels)
            return (
                None,
                None,
                (
                    "Not enough sales yet to rank products — "
                    f"{total} parcel{'' if total == 1 else 's'} this week, and a "
                    f"product needs {MIN_RANKING_SAMPLE} before its figure means "
                    "anything."
                ),
            )

        # Name breaks ties so the same week always ranks the same way.
        ordered = sorted(eligible, key=lambda item: (-item[1], item[0]))
        return ordered[0], ordered[-1], None

    async def _courier_ranking(
        self, since: date, until: date
    ) -> tuple[tuple[str, int] | None, tuple[str, int] | None, str | None]:
        """Best and worst courier by delivery success, or nothing.

        Success is delivered (including partly delivered) over everything that
        finished, in basis points, drawn only from this shop's own parcels —
        section 24 allows nothing else. The sample gate is the same one the
        product ranking uses, and for the same reason: telling a seller to drop
        a courier on the strength of four parcels is worse than telling them
        nothing.

        Only providers that actually carried parcels are ranked, and a shop
        using one courier gets a note rather than a "best" and "worst" that
        are the same company.
        """
        rows = await self._db.execute(
            sa.select(
                Consignment.provider,
                ProfitSnapshot.outcome,
                sa.func.count(),
            )
            .join(Consignment, Consignment.id == ProfitSnapshot.consignment_id)
            .where(
                ProfitSnapshot.is_current.is_(True),
                ProfitSnapshot.business_date >= since,
                ProfitSnapshot.business_date <= until,
            )
            .group_by(Consignment.provider, ProfitSnapshot.outcome)
        )

        delivered: dict[str, int] = {}
        finished: dict[str, int] = {}
        for provider, outcome, count in rows:
            finished[provider] = finished.get(provider, 0) + int(count)
            if outcome in (
                str(ConsignmentStatus.DELIVERED),
                str(ConsignmentStatus.PARTIAL_DELIVERED),
            ):
                delivered[provider] = delivered.get(provider, 0) + int(count)

        eligible = [
            (provider, round(delivered.get(provider, 0) * 10_000 / total))
            for provider, total in finished.items()
            if total >= MIN_RANKING_SAMPLE
        ]
        if len(eligible) < 2:
            carried = len(finished)
            note = (
                "Only one courier has enough parcels this week to compare."
                if carried > 1
                else (
                    f"A courier needs {MIN_RANKING_SAMPLE} finished parcels before "
                    "its success rate means anything."
                )
            )
            return None, None, note

        ordered = sorted(eligible, key=lambda item: (-item[1], item[0]))
        return ordered[0], ordered[-1], None
